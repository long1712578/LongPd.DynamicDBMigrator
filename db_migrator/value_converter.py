#!/usr/bin/env python3
"""
db_migrator/value_converter.py
==============================
Rule-based value conversion pipeline.

Replaces the hardcoded if/elif chains in the old convert_value() and
convert_value_for_postgres() functions with a clean, config-driven
system of Transform objects.

Design
------
- Each Transform subclass handles ONE specific conversion concern.
- The ValueConverter loads rules from MigrationConfig.value_transforms
  and applies the right transform for each (source_col, target_col) pair.
- Users declare transforms in mapping_config.json — no code changes needed.

Built-in transform types
------------------------
  null_to_bool    : NULL → false, any value → true  (deletedAt → is_deleted)
  enum_to_int     : enum text → integer via lookup table
  json_normalize  : validate + normalize JSON strings
  timestamp       : format datetime strings for PostgreSQL
  uuid_validate   : validate UUID, generate new if invalid
  string_escape   : escape single-quotes and control chars
"""

from __future__ import annotations

import json
import re
import uuid as _uuid_mod
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Base Transform
# ---------------------------------------------------------------------------

class BaseTransform(ABC):
    """Base class for all value transforms."""

    @abstractmethod
    def apply(self, value: Any) -> str:
        """Convert *value* to a PostgreSQL literal string."""
        ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_null(value: Any) -> bool:
        return value is None or value == "" or str(value).upper() == "NULL"

    @staticmethod
    def _strip_quotes(value: str) -> str:
        """Remove surrounding single quotes added by the SQL parser."""
        s = str(value)
        if s.startswith("'") and s.endswith("'") and len(s) >= 2:
            return s[1:-1]
        return s

    @staticmethod
    def _escape(value: str) -> str:
        """Escape for PostgreSQL: single-quote and control chars."""
        s = str(value).replace("'", "''")
        s = re.sub(r"[\x00-\x1F\x7F]", " ", s)
        return s

    @staticmethod
    def _pg_null() -> str:
        return "NULL"


# ---------------------------------------------------------------------------
# Concrete Transforms
# ---------------------------------------------------------------------------

class NullToBoolTransform(BaseTransform):
    """
    NULL → false, any value → true.
    Used for deletedAt → is_deleted.
    """

    def apply(self, value: Any) -> str:
        return "false" if self._is_null(value) else "true"


class EnumToIntTransform(BaseTransform):
    """
    Map enum text values to integers using a lookup table.
    Config: {"type": "enum_to_int", "mapping": {"Bổ nhiệm": 1, ...}}
    """

    def __init__(self, mapping: dict[str, int | str]):
        self._mapping = {str(k): str(v) for k, v in mapping.items()}

    def apply(self, value: Any) -> str:
        if self._is_null(value):
            return self._pg_null()
        raw = self._strip_quotes(str(value))
        result = self._mapping.get(raw)
        return result if result is not None else self._pg_null()


class JsonNormalizeTransform(BaseTransform):
    """
    Validate and normalize a JSON string.
    Config: {"type": "json_normalize", "default": "[]", "cast": "jsonb"}
    """

    def __init__(self, default: str = "null", cast: str = "jsonb"):
        self._default = default
        self._cast = cast

    def apply(self, value: Any) -> str:
        if self._is_null(value):
            escaped_default = self._escape(self._default)
            return f"'{escaped_default}'::{self._cast}"

        raw = self._strip_quotes(str(value))
        # unescape MySQL escape sequences
        raw = raw.replace("\\'", "'").replace('\\"', '"').replace("\\\\", "\\").replace("''", "'")

        try:
            parsed = json.loads(raw)
            normalized = json.dumps(parsed, ensure_ascii=False)
            escaped = normalized.replace("'", "''")
            return f"'{escaped}'::{self._cast}"
        except (json.JSONDecodeError, ValueError):
            escaped = self._escape(raw)
            return f"'{escaped}'::{self._cast}"


class TimestampTransform(BaseTransform):
    """
    Format datetime strings for PostgreSQL.
    Config: {"type": "timestamp", "timezone_offset": 0}
    timezone_offset = 0 → keep UTC (default)
    timezone_offset = 7 → convert UTC to Vietnam (+7)
    """

    _FMT_IN  = "%Y-%m-%d %H:%M:%S"
    _FMT_OUT = "%Y-%m-%d %H:%M:%S"

    def __init__(self, timezone_offset: int = 0):
        self._tz = timezone_offset

    def apply(self, value: Any) -> str:
        if self._is_null(value):
            return self._pg_null()

        raw = self._strip_quotes(str(value)).strip()
        if not raw or raw.upper() == "NULL":
            return self._pg_null()

        try:
            dt = datetime.strptime(raw, self._FMT_IN)
            if self._tz:
                dt += timedelta(hours=self._tz)
            return f"'{dt.strftime(self._FMT_OUT)}'"
        except ValueError:
            escaped = self._escape(raw)
            return f"'{escaped}'"


class DateTransform(BaseTransform):
    """
    Convert MySQL date (YYYY-MM-DD) to PostgreSQL timestamp literal.
    No timezone shift because date has no time component.
    """

    def apply(self, value: Any) -> str:
        if self._is_null(value):
            return self._pg_null()
        raw = self._strip_quotes(str(value)).strip()
        if not raw or raw.upper() == "NULL":
            return self._pg_null()
        if len(raw) == 10:
            return f"'{raw} 00:00:00'::timestamp"
        escaped = self._escape(raw)
        return f"'{escaped}'::timestamp"


class UuidValidateTransform(BaseTransform):
    """
    Validate UUID format. Returns NULL if value is not a valid UUID.
    Config: {"type": "uuid_validate", "generate_if_missing": false}
    """

    _UUID_RE = re.compile(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        re.IGNORECASE,
    )

    def __init__(self, generate_if_missing: bool = False):
        self._generate = generate_if_missing

    def apply(self, value: Any) -> str:
        if self._is_null(value):
            if self._generate:
                return f"'{_uuid_mod.uuid4()}'"
            return self._pg_null()

        raw = self._strip_quotes(str(value)).strip()
        if self._UUID_RE.match(raw):
            return f"'{raw}'"
        if self._generate:
            return f"'{_uuid_mod.uuid4()}'"
        return self._pg_null()


class StringEscapeTransform(BaseTransform):
    """
    Default string escaping — handles most text columns.
    Config: {"type": "string_escape", "max_length": null}
    """

    _HTML_ENTITIES = {
        "&nbsp;": " ", "&lt;": "<", "&gt;": ">", "&amp;": "&",
        "&quot;": '"', "&#39;": "'", "&apos;": "'",
        "&hellip;": "...", "&mdash;": "—", "&ndash;": "–",
    }

    def __init__(self, max_length: int | None = None, strip_html: bool = True):
        self._max_length = max_length
        self._strip_html = strip_html

    def apply(self, value: Any) -> str:
        if self._is_null(value):
            return self._pg_null()

        raw = self._strip_quotes(str(value))

        if self._strip_html:
            for entity, replacement in self._HTML_ENTITIES.items():
                raw = raw.replace(entity, replacement)
            raw = re.sub(r"<[^>]*>", "", raw)

        if self._max_length and len(raw) > self._max_length:
            raw = raw[: self._max_length]

        escaped = self._escape(raw)
        return f"'{escaped}'"


class BooleanTransform(BaseTransform):
    """Convert MySQL tinyint(1) / 0 / 1 to PostgreSQL boolean."""

    def apply(self, value: Any) -> str:
        if self._is_null(value):
            return self._pg_null()
        raw = self._strip_quotes(str(value)).strip().lower()
        if raw in ("1", "true", "yes", "on"):
            return "true"
        if raw in ("0", "false", "no", "off"):
            return "false"
        return self._pg_null()


class IntegerTransform(BaseTransform):
    """Pass-through for integer values."""

    def apply(self, value: Any) -> str:
        if self._is_null(value):
            return self._pg_null()
        raw = self._strip_quotes(str(value)).strip()
        try:
            return str(int(float(raw)))
        except (ValueError, TypeError):
            return self._pg_null()


class PassthroughTransform(BaseTransform):
    """
    Raw pass-through with no conversion (escape only).
    Used as the last-resort fallback.
    """

    def apply(self, value: Any) -> str:
        if self._is_null(value):
            return self._pg_null()
        raw = self._strip_quotes(str(value))
        escaped = self._escape(raw)
        return f"'{escaped}'"


# ---------------------------------------------------------------------------
# Transform factory
# ---------------------------------------------------------------------------

_TRANSFORM_REGISTRY: dict[str, type[BaseTransform]] = {
    "null_to_bool":   NullToBoolTransform,
    "enum_to_int":    EnumToIntTransform,
    "json_normalize": JsonNormalizeTransform,
    "timestamp":      TimestampTransform,
    "date":           DateTransform,
    "uuid_validate":  UuidValidateTransform,
    "string_escape":  StringEscapeTransform,
    "boolean":        BooleanTransform,
    "integer":        IntegerTransform,
    "passthrough":    PassthroughTransform,
}


def _build_transform(spec: dict) -> BaseTransform:
    """Instantiate a Transform from a config spec dict."""
    t_type = spec.get("type", "passthrough")
    cls = _TRANSFORM_REGISTRY.get(t_type, PassthroughTransform)

    kwargs = {k: v for k, v in spec.items() if k != "type"}
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# Compiled rule
# ---------------------------------------------------------------------------

class _CompiledRule:
    """One resolved transform rule, ready to match against a column pair."""

    def __init__(self, pattern: str, transform: BaseTransform):
        # Pattern examples:
        #   "*.deletedAt -> *.is_deleted"
        #   "user.types -> *.Types"
        #   "user_movement.type -> *.Type"
        self._raw_pattern = pattern
        self._transform = transform
        self._src_table, self._src_col, self._tgt_col = self._parse(pattern)

    @staticmethod
    def _parse(pattern: str) -> tuple[str | None, str, str]:
        """Parse "src_table.src_col -> tgt_table.tgt_col" into components."""
        parts = [p.strip() for p in pattern.split("->")]
        src = parts[0] if len(parts) >= 1 else ""
        tgt = parts[1] if len(parts) >= 2 else "*.*"

        def split_dotted(s: str) -> tuple[str | None, str]:
            if "." in s:
                tbl, col = s.split(".", 1)
                return (None if tbl == "*" else tbl), col
            return None, s

        src_table, src_col = split_dotted(src)
        _tgt_table, tgt_col = split_dotted(tgt)
        return src_table, src_col, tgt_col

    def matches(self, source_table: str, source_col: str, target_col: str) -> bool:
        tbl_ok = (self._src_table is None) or (self._src_table == source_table)
        col_ok = (self._src_col == "*") or (self._src_col == source_col)
        tgt_ok = (self._tgt_col == "*") or (self._tgt_col == target_col)
        return tbl_ok and col_ok and tgt_ok

    def apply(self, value: Any) -> str:
        return self._transform.apply(value)


# ---------------------------------------------------------------------------
# ValueConverter — main public class
# ---------------------------------------------------------------------------

class ValueConverter:
    """
    Apply the right transform to every (source_col, target_col) value.

    Initialise once with config, then call convert() for each cell.

    Usage::

        converter = ValueConverter.from_config(migration_config)
        pg_literal = converter.convert(
            value="2025-01-15 08:00:00",
            source_table="user",
            source_col="createdAt",
            target_col="SyncCreatedDate",
            mysql_type="timestamp",
        )
        # → "'2025-01-15 08:00:00'"
    """

    def __init__(self, rules: list[_CompiledRule], type_mapper=None):
        self._rules = rules
        self._type_mapper = type_mapper

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config, type_mapper=None) -> ValueConverter:
        """
        Build a ValueConverter from a MigrationConfig instance.
        Also accepts a plain dict with a "value_transforms" key.
        """
        if hasattr(config, "value_transforms"):
            raw_transforms = config.value_transforms
        elif isinstance(config, dict):
            raw_transforms = config.get("value_transforms", {})
        else:
            raw_transforms = {}

        rules: list[_CompiledRule] = []
        for pattern, spec in raw_transforms.items():
            transform = _build_transform(spec)
            rules.append(_CompiledRule(pattern, transform))

        return cls(rules, type_mapper)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def convert(
        self,
        value: Any,
        source_table: str,
        source_col: str,
        target_col: str,
        mysql_type: str = "text",
    ) -> str:
        """
        Convert a single cell value to a PostgreSQL literal string.

        Priority:
        1. Config-declared transforms (value_transforms rules)
        2. Type-based auto transforms (UUID, boolean, timestamp, etc.)
        3. Default string escape
        """
        # 1. Config rules (first match wins)
        for rule in self._rules:
            if rule.matches(source_table, source_col, target_col):
                return rule.apply(value)

        # 2. Type-based auto detection
        return self._auto_convert(value, mysql_type, source_col, target_col)

    # ------------------------------------------------------------------
    # Auto conversion based on MySQL column type
    # ------------------------------------------------------------------

    def _auto_convert(
        self,
        value: Any,
        mysql_type: str,
        source_col: str,
        target_col: str,
    ) -> str:
        t = mysql_type.strip().lower()

        # NULL
        if value is None or value == "" or str(value).upper() == "NULL":
            return "NULL"

        # UUID columns
        if t in ("char(36)", "varchar(36)"):
            return UuidValidateTransform().apply(value)

        # Boolean
        if t in ("tinyint(1)", "boolean", "bool", "bit(1)"):
            return BooleanTransform().apply(value)

        # Timestamp / datetime
        if t.startswith(("timestamp", "datetime")):
            return TimestampTransform(timezone_offset=0).apply(value)

        # Date only
        if t == "date":
            return DateTransform().apply(value)

        # JSON
        if t in ("json", "jsonb"):
            return JsonNormalizeTransform().apply(value)

        # Integer types
        if t.startswith(("tinyint", "smallint", "mediumint", "int", "bigint")):
            return IntegerTransform().apply(value)

        # Default: safe string escape
        return StringEscapeTransform().apply(value)
