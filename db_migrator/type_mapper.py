#!/usr/bin/env python3
"""
db_migrator/type_mapper.py
==========================
Bidirectional database type mapping engine.

Handles MySQL ↔ PostgreSQL type conversion with:
- Full type coverage (not just the 10 types from the old code)
- Pattern-based matching (e.g. varchar(n), decimal(p,s))
- Per-column overrides from MigrationConfig
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Type mapping tables
# ---------------------------------------------------------------------------

# Exact matches first (checked before pattern matching)
_MYSQL_TO_PG_EXACT: dict[str, str] = {
    "char(36)":    "uuid",
    "varchar(36)": "uuid",
    "tinyint(1)":  "boolean",
    "bit(1)":      "boolean",
    "tinyint":     "smallint",
    "smallint":    "smallint",
    "mediumint":   "integer",
    "int":         "integer",
    "integer":     "integer",
    "bigint":      "bigint",
    "float":       "real",
    "double":      "double precision",
    "real":        "double precision",
    "date":        "date",
    "year":        "smallint",
    "time":        "time without time zone",
    "timestamp":   "timestamp without time zone",
    "datetime":    "timestamp without time zone",
    "tinytext":    "text",
    "text":        "text",
    "mediumtext":  "text",
    "longtext":    "text",
    "tinyblob":    "bytea",
    "blob":        "bytea",
    "mediumblob":  "bytea",
    "longblob":    "bytea",
    "json":        "jsonb",
    "boolean":     "boolean",
    "bool":        "boolean",
}

# Pattern-based matches (regex applied in order)
_MYSQL_TO_PG_PATTERNS: list[tuple[str, str]] = [
    # varchar(36) / char(36) handled above as exact match
    (r"^varchar\(\d+\)$",             "text"),
    (r"^char\(\d+\)$",                "text"),
    (r"^tinyint\(\d+\)$",             "smallint"),
    (r"^smallint\(\d+\)$",            "smallint"),
    (r"^mediumint\(\d+\)$",           "integer"),
    (r"^int\(\d+\)$",                 "integer"),
    (r"^integer\(\d+\)$",             "integer"),
    (r"^bigint\(\d+\)$",              "bigint"),
    (r"^decimal\((\d+),(\d+)\)$",     "numeric({1},{2})"),  # preserves precision
    (r"^numeric\((\d+),(\d+)\)$",     "numeric({1},{2})"),
    (r"^float\(\d+,\d+\)$",           "double precision"),
    (r"^double\(\d+,\d+\)$",          "double precision"),
    (r"^enum\(.+\)$",                 "text"),
    (r"^set\(.+\)$",                  "text"),
    (r"^varbinary\(\d+\)$",           "bytea"),
    (r"^binary\(\d+\)$",              "bytea"),
]


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class TypeMapper:
    """
    Convert MySQL column types to PostgreSQL equivalents.

    Usage::

        mapper = TypeMapper(type_overrides={"user.types": "jsonb"})
        mapper.mysql_to_pg("varchar(36)")          # → "uuid"  (exact match)
        mapper.mysql_to_pg("decimal(10,2)")        # → "numeric(10,2)"
        mapper.mysql_to_pg("json", "user", "types") # → "jsonb" (override)
    """

    def __init__(self, type_overrides: dict[str, str] | None = None):
        """
        Args:
            type_overrides: Dict of "table.column" → "pg_type" from MigrationConfig.
        """
        self._overrides = type_overrides or {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def mysql_to_pg(
        self,
        mysql_type: str,
        source_table: str | None = None,
        source_column: str | None = None,
    ) -> str:
        """
        Convert a MySQL column type to its PostgreSQL equivalent.

        Checks (in priority order):
        1. Per-column override from config (table.column)
        2. Exact match in the type table
        3. Pattern match
        4. Fallback: 'text'
        """
        # 1. Config override
        if source_table and source_column:
            override = self._overrides.get(f"{source_table}.{source_column}")
            if override:
                return override

        mysql_type_lower = mysql_type.strip().lower()

        # 2. Exact match
        if mysql_type_lower in _MYSQL_TO_PG_EXACT:
            return _MYSQL_TO_PG_EXACT[mysql_type_lower]

        # 3. Pattern match
        for pattern, pg_type in _MYSQL_TO_PG_PATTERNS:
            m = re.match(pattern, mysql_type_lower)
            if m:
                # Support group substitution for decimal(p,s) → numeric(p,s)
                if "{1}" in pg_type:
                    pg_type = pg_type.format(None, *m.groups())
                return pg_type

        # 4. Fallback
        return "text"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_uuid_type(self, mysql_type: str) -> bool:
        t = mysql_type.strip().lower()
        return t in ("char(36)", "varchar(36)")

    def is_boolean_type(self, mysql_type: str) -> bool:
        t = mysql_type.strip().lower()
        return t in ("tinyint(1)", "boolean", "bool", "bit(1)")

    def is_timestamp_type(self, mysql_type: str) -> bool:
        t = mysql_type.strip().lower()
        return t.startswith(("timestamp", "datetime"))

    def is_date_type(self, mysql_type: str) -> bool:
        return mysql_type.strip().lower() == "date"

    def is_json_type(self, mysql_type: str) -> bool:
        t = mysql_type.strip().lower()
        return t in ("json", "jsonb")

    def is_numeric_type(self, mysql_type: str) -> bool:
        t = mysql_type.strip().lower()
        return t.startswith(("int", "tinyint", "smallint", "mediumint",
                              "bigint", "float", "double", "decimal", "numeric", "real"))

    def is_text_type(self, mysql_type: str) -> bool:
        t = mysql_type.strip().lower()
        return t.startswith(("varchar", "char", "text", "tinytext",
                              "mediumtext", "longtext", "enum", "set"))

    def is_binary_type(self, mysql_type: str) -> bool:
        t = mysql_type.strip().lower()
        return t.startswith(("blob", "tinyblob", "mediumblob", "longblob",
                              "varbinary", "binary"))
