#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_migrator/config.py
=====================
Configuration manager for the migration library.

Reads/writes mapping_config.json and exposes typed accessors.
Backward compatible with v1 config files (no "version" key).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Default / empty config skeleton
# ---------------------------------------------------------------------------

_EMPTY_CONFIG: dict[str, Any] = {
    "version": "2.0",
    "target_schema": "public",
    "table_mapping": {},
    "column_mapping": {},
    "type_overrides": {},
    "value_transforms": {},
    "custom_rules": {
        "enum_mapping": {},
        "required_defaults": {},
        "ignored_source_columns": {},
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class MigrationConfig:
    """
    Load, validate, and expose migration configuration.

    Usage::

        cfg = MigrationConfig("mapping_config.json")
        print(cfg.table_mapping)          # {"user": "office-employee", ...}
        print(cfg.column_mapping("user")) # {"id": "Id", ...}
        print(cfg.target_schema)          # "hrm-services"
    """

    def __init__(self, config_file: str = "mapping_config.json"):
        self._path = config_file
        self._data: dict[str, Any] = self._load()

    # ------------------------------------------------------------------
    # Core accessors
    # ------------------------------------------------------------------

    @property
    def target_schema(self) -> str:
        return self._data.get("target_schema", "public")

    @property
    def table_mapping(self) -> dict[str, str]:
        """MySQL table name → PostgreSQL table name."""
        return self._data.get("table_mapping", {})

    def column_mapping(self, source_table: str | None = None) -> dict:
        """
        If source_table is given, return column map for that table.
        Otherwise return the entire column_mapping dict.
        """
        cm = self._data.get("column_mapping", {})
        if source_table is not None:
            return cm.get(source_table, {})
        return cm

    @property
    def type_overrides(self) -> dict[str, str]:
        """
        Per-column type overrides: "table.column" → "pg_type".
        Example: {"user.types": "jsonb", "user_movement.type": "integer"}
        """
        return self._data.get("type_overrides", {})

    @property
    def value_transforms(self) -> dict[str, dict]:
        """
        Rule-based transform config keyed by pattern strings.
        Example: {"*.deletedAt -> *.is_deleted": {"type": "null_to_bool"}}
        """
        return self._data.get("value_transforms", {})

    def custom_rules(self, key: str | None = None) -> Any:
        """
        Access custom_rules section.
        key: "enum_mapping" | "required_defaults" | "ignored_source_columns" | None
        """
        rules = self._data.get("custom_rules", {})
        if key is not None:
            return rules.get(key, {})
        return rules

    # ------------------------------------------------------------------
    # Convenience helpers (keep backward-compat API of config_manager.py)
    # ------------------------------------------------------------------

    def get_table_mapping(self) -> dict[str, str]:
        return self.table_mapping

    def get_column_mapping(self, table_name: str | None = None) -> dict:
        return self.column_mapping(table_name)

    def get_custom_rules(self) -> dict:
        return self.custom_rules()

    def get_enum_mapping(self, table: str, column: str) -> dict[str, str]:
        """Return enum text→value mapping for a specific table+column."""
        return (
            self.custom_rules("enum_mapping")
            .get(table, {})
            .get(column, {})
        )

    def get_required_defaults(self, postgres_table: str) -> dict[str, str]:
        """Return required column defaults for a PostgreSQL table."""
        return self.custom_rules("required_defaults").get(postgres_table, {})

    def get_ignored_columns(self, source_table: str) -> set[str]:
        """Return set of source column names to ignore for a table."""
        ignored = self.custom_rules("ignored_source_columns").get(source_table, [])
        return set(ignored) if isinstance(ignored, list) else set()

    def get_type_override(self, source_table: str, source_column: str) -> str | None:
        """Return explicit PG type override for a column, or None."""
        key = f"{source_table}.{source_column}"
        return self.type_overrides.get(key)

    # ------------------------------------------------------------------
    # Auto-mapping helper
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(name: str) -> str:
        """Lowercase + remove underscores for fuzzy comparison."""
        return name.lower().replace("_", "")

    def auto_map_fields(
        self,
        mysql_columns: list[str],
        postgres_columns: list[str],
        existing_mapping: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        Suggest column mappings based on name similarity.

        Returns a dict {mysql_col: postgres_col}.
        - Exact match (case-insensitive, ignores underscores) → auto mapped
        - Common convention: deletedAt → is_deleted
        - Already-mapped columns are preserved from existing_mapping
        """
        mapping: dict[str, str] = dict(existing_mapping or {})
        pg_lookup = {self._normalize(col): col for col in postgres_columns}

        for mysql_col in mysql_columns:
            if mysql_col in mapping:
                continue
            norm = self._normalize(mysql_col)

            # Exact normalised match
            if norm in pg_lookup:
                mapping[mysql_col] = pg_lookup[norm]
                continue

            # Well-known convention: deletedAt → is_deleted
            if norm == "deletedat" and "isdeleted" in pg_lookup:
                mapping[mysql_col] = pg_lookup["isdeleted"]
                continue

        return mapping

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, data: dict[str, Any] | None = None) -> None:
        """Persist config to disk. If data is provided, merge and save."""
        if data is not None:
            self._data = data
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def reload(self) -> None:
        """Re-read config from disk."""
        self._data = self._load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        if not os.path.exists(self._path):
            return dict(_EMPTY_CONFIG)

        with open(self._path, "r", encoding="utf-8") as f:
            raw: dict[str, Any] = json.load(f)

        return self._migrate_v1_to_v2(raw)

    @staticmethod
    def _migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
        """
        Ensure v1 configs (no "version" key) work transparently.
        We just fill in any missing top-level keys with defaults.
        """
        result = dict(_EMPTY_CONFIG)
        result.update(raw)

        # Ensure nested defaults
        for k, v in _EMPTY_CONFIG.get("custom_rules", {}).items():
            result.setdefault("custom_rules", {})[k] = result["custom_rules"].get(k, v)

        return result


# ---------------------------------------------------------------------------
# Module-level singleton — mirrors the old config_manager interface
# ---------------------------------------------------------------------------

_default_config: MigrationConfig | None = None


def _get_default() -> MigrationConfig:
    global _default_config
    if _default_config is None:
        _default_config = MigrationConfig()
    return _default_config


# Legacy shim functions (kept for backward compatibility with auto_sync.py / enhanced_converter.py)
def load_config() -> dict:
    return _get_default()._data

def save_config(data: dict) -> None:
    _get_default().save(data)

def get_table_mapping() -> dict[str, str]:
    return _get_default().get_table_mapping()

def get_column_mapping(table_name: str | None = None) -> dict:
    return _get_default().get_column_mapping(table_name)

def get_custom_rules() -> dict:
    return _get_default().get_custom_rules()

def auto_map_fields(
    mysql_columns: list[str],
    postgres_columns: list[str],
    existing_mapping: dict | None = None,
) -> dict[str, str]:
    return _get_default().auto_map_fields(mysql_columns, postgres_columns, existing_mapping)