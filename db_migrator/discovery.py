#!/usr/bin/env python3
"""
db_migrator/discovery.py
========================
Auto-discover database schemas from live connections or SQL dump files.

Returns a normalised SchemaInfo dict so the rest of the library can
work with it regardless of the source.

Usage::

    disc = SchemaDiscovery()

    # From a live MySQL connection
    schema = disc.from_mysql({"host": "...", "port": 3306, ...})

    # From a SQL dump file (no DB connection needed)
    schema = disc.from_sql_file("backup.sql")

    # Suggest column mappings between two schemas
    suggestion = disc.suggest_mapping(mysql_schema, pg_schema)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ColumnInfo:
    """Single column metadata."""
    name: str
    data_type: str
    nullable: bool = True
    default: Any = None
    is_primary_key: bool = False
    is_unique: bool = False
    extra: str = ""           # e.g. "auto_increment"
    comment: str = ""


@dataclass
class TableSchema:
    """All column metadata for one table."""
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column_type(self, name: str) -> str | None:
        for c in self.columns:
            if c.name == name:
                return c.data_type
        return None


# Schema = dict of table_name → TableSchema
SchemaInfo = dict[str, TableSchema]


# ---------------------------------------------------------------------------
# Mapping suggestion structures
# ---------------------------------------------------------------------------

@dataclass
class ColumnMatch:
    source_col: str
    target_col: str | None
    confidence: str   # "exact" | "fuzzy" | "unmatched"


@dataclass
class TableMatch:
    source_table: str
    target_table: str | None
    confidence: str   # "exact" | "fuzzy" | "unmatched"
    column_matches: list[ColumnMatch] = field(default_factory=list)


@dataclass
class MappingSuggestion:
    """Result of suggest_mapping()."""
    table_matches: list[TableMatch] = field(default_factory=list)

    def to_config_dict(self) -> dict:
        """
        Convert suggestion into table_mapping + column_mapping dicts
        that can be saved directly to mapping_config.json.
        """
        table_mapping: dict[str, str] = {}
        column_mapping: dict[str, dict[str, str]] = {}

        for tm in self.table_matches:
            if tm.target_table:
                table_mapping[tm.source_table] = tm.target_table

            col_map: dict[str, str] = {}
            for cm in tm.column_matches:
                if cm.target_col:
                    col_map[cm.source_col] = cm.target_col

            if col_map:
                column_mapping[tm.source_table] = col_map

        return {"table_mapping": table_mapping, "column_mapping": column_mapping}


# ---------------------------------------------------------------------------
# SchemaDiscovery class
# ---------------------------------------------------------------------------

class SchemaDiscovery:
    """Discover database schemas from multiple sources."""

    # ------------------------------------------------------------------
    # From live MySQL connection
    # ------------------------------------------------------------------

    def from_mysql(
        self,
        config: dict,
        tables: list[str] | None = None,
    ) -> SchemaInfo:
        """
        Connect to MySQL and introspect the schema.

        Args:
            config : {"host", "port", "database", "user", "password"}
            tables : If given, only introspect these tables.

        Returns:
            SchemaInfo dict.
        """
        try:
            import mysql.connector
        except ImportError as e:
            raise ImportError("mysql-connector-python is required. Run: pip install mysql-connector-python") from e

        logger.info(f"🔍 Discovering MySQL schema: {config.get('host')}:{config.get('port')}/{config.get('database')}")
        conn = mysql.connector.connect(**config)
        cursor = conn.cursor()

        # Get all tables if not specified
        if tables is None:
            cursor.execute("SHOW TABLES")
            tables = [row[0] for row in cursor.fetchall()]

        schema: SchemaInfo = {}
        for table in tables:
            ts = self._introspect_mysql_table(cursor, table)
            schema[table] = ts
            logger.info(f"  ✓ `{table}`: {len(ts.columns)} columns")

        cursor.close()
        conn.close()
        return schema

    def _introspect_mysql_table(self, cursor, table_name: str) -> TableSchema:
        ts = TableSchema(name=table_name)

        cursor.execute(f"SHOW FULL COLUMNS FROM `{table_name}`")
        rows = cursor.fetchall()

        for row in rows:
            # SHOW FULL COLUMNS: Field, Type, Collation, Null, Key, Default, Extra, Privileges, Comment
            col = ColumnInfo(
                name=row[0],
                data_type=str(row[1]).lower(),
                nullable=(str(row[3]).upper() == "YES"),
                default=row[5],
                is_primary_key=(str(row[4]).upper() == "PRI"),
                is_unique=(str(row[4]).upper() in ("PRI", "UNI")),
                extra=str(row[6]) if row[6] else "",
                comment=str(row[8]) if row[8] else "",
            )
            ts.columns.append(col)
            if col.is_primary_key:
                ts.primary_key.append(col.name)

        return ts

    # ------------------------------------------------------------------
    # From live PostgreSQL connection
    # ------------------------------------------------------------------

    def from_postgres(
        self,
        config: dict,
        schema: str = "public",
        tables: list[str] | None = None,
    ) -> SchemaInfo:
        """
        Connect to PostgreSQL and introspect the schema.

        Args:
            config : {"host", "port", "database", "user", "password"}
            schema : PostgreSQL schema name (default: "public")
            tables : If given, only introspect these tables.
        """
        try:
            import psycopg2
        except ImportError as e:
            raise ImportError("psycopg2-binary is required. Run: pip install psycopg2-binary") from e

        logger.info(f"🔍 Discovering PostgreSQL schema: {config.get('host')}/{config.get('database')}.{schema}")
        conn = psycopg2.connect(**config)
        cursor = conn.cursor()

        if tables is None:
            cursor.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = %s AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """,
                (schema,),
            )
            tables = [row[0] for row in cursor.fetchall()]

        result: SchemaInfo = {}
        for table in tables:
            ts = self._introspect_pg_table(cursor, schema, table)
            result[table] = ts
            logger.info(f"  ✓ `{schema}`.`{table}`: {len(ts.columns)} columns")

        cursor.close()
        conn.close()
        return result

    def _introspect_pg_table(self, cursor, schema_name: str, table_name: str) -> TableSchema:
        ts = TableSchema(name=table_name)

        cursor.execute(
            """
            SELECT
                c.column_name,
                c.data_type,
                c.is_nullable,
                c.column_default,
                COALESCE(tc.constraint_type = 'PRIMARY KEY', FALSE) AS is_pk
            FROM information_schema.columns c
            LEFT JOIN information_schema.key_column_usage kcu
                ON c.column_name = kcu.column_name
                AND c.table_name = kcu.table_name
                AND c.table_schema = kcu.table_schema
            LEFT JOIN information_schema.table_constraints tc
                ON kcu.constraint_name = tc.constraint_name
                AND tc.constraint_type = 'PRIMARY KEY'
            WHERE c.table_schema = %s AND c.table_name = %s
            ORDER BY c.ordinal_position
            """,
            (schema_name, table_name),
        )

        for row in cursor.fetchall():
            col = ColumnInfo(
                name=row[0],
                data_type=str(row[1]).lower(),
                nullable=(str(row[2]).upper() == "YES"),
                default=row[3],
                is_primary_key=bool(row[4]),
            )
            ts.columns.append(col)
            if col.is_primary_key:
                ts.primary_key.append(col.name)

        return ts

    # ------------------------------------------------------------------
    # From SQL dump file
    # ------------------------------------------------------------------

    def from_sql_file(
        self,
        filepath: str,
        tables: list[str] | None = None,
    ) -> SchemaInfo:
        """
        Parse a MySQL dump file and return schema without connecting to any DB.

        This lets teams share just the .sql dump and have schema discovery
        without granting DB access.
        """
        from .sql_parser import _COL_DEF_RE, _CREATE_TABLE_RE

        logger.info(f"🔍 Discovering schema from SQL file: {filepath}")

        import os
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        with open(filepath, encoding="utf-8", errors="replace") as f:
            content = f.read()

        result: SchemaInfo = {}

        for match in _CREATE_TABLE_RE.finditer(content):
            tbl_name = match.group(1)
            if tables and tbl_name not in tables:
                continue

            body = match.group(2)
            ts = TableSchema(name=tbl_name)

            # Extract PK
            pk_match = re.search(r"PRIMARY KEY\s*\(`([^`]+)`\)", body, re.IGNORECASE)
            pk_col = pk_match.group(1) if pk_match else None

            for cm in _COL_DEF_RE.finditer(body):
                col_name = cm.group(1)
                col_type = cm.group(2).lower()
                col = ColumnInfo(
                    name=col_name,
                    data_type=col_type,
                    is_primary_key=(col_name == pk_col),
                )
                ts.columns.append(col)
                if col.is_primary_key:
                    ts.primary_key.append(col_name)

            result[tbl_name] = ts
            logger.info(f"  ✓ `{tbl_name}`: {len(ts.columns)} columns")

        return result

    # ------------------------------------------------------------------
    # Mapping suggestion
    # ------------------------------------------------------------------

    def suggest_mapping(
        self,
        source_schema: SchemaInfo,
        target_schema: SchemaInfo,
        existing_table_mapping: dict[str, str] | None = None,
        existing_column_mapping: dict[str, dict[str, str]] | None = None,
    ) -> MappingSuggestion:
        """
        Compare source and target schemas, suggest table + column mappings.
        """
        existing = existing_table_mapping or {}
        existing_cols = existing_column_mapping or {}
        suggestion = MappingSuggestion()

        # Build normalised lookup for target tables
        tgt_norm = {self._norm(t): t for t in target_schema}

        for src_table, src_ts in source_schema.items():
            # Check if manually mapped already
            if src_table in existing:
                tgt_table = existing[src_table]
                confidence = "exact"
            else:
                tgt_table, confidence = self._match_name(src_table, tgt_norm)

            tm = TableMatch(
                source_table=src_table,
                target_table=tgt_table,
                confidence=confidence,
            )

            if tgt_table and tgt_table in target_schema:
                tgt_ts = target_schema[tgt_table]
                tgt_col_norm = {self._norm(c.name): c.name for c in tgt_ts.columns}

                # Check for existing column mappings for this table
                tbl_existing_cols = existing_cols.get(src_table, {})
                assigned_tgt_cols = set()
                pending_src_cols = []

                # Pass 1: Exact matches and manual overrides
                for src_col in src_ts.column_names():
                    if src_col in tbl_existing_cols:
                        tgt_col = tbl_existing_cols[src_col]
                        assigned_tgt_cols.add(tgt_col)
                        tm.column_matches.append(
                            ColumnMatch(source_col=src_col, target_col=tgt_col, confidence="exact")
                        )
                        continue

                    tgt_col, col_confidence = self._match_name(src_col, tgt_col_norm, fuzzy=False)
                    if tgt_col:
                        assigned_tgt_cols.add(tgt_col)
                        tm.column_matches.append(
                            ColumnMatch(source_col=src_col, target_col=tgt_col, confidence=col_confidence)
                        )
                    else:
                        pending_src_cols.append(src_col)

                # Pass 2: Fuzzy matches for remaining
                for src_col in pending_src_cols:
                    tgt_col, col_confidence = self._match_name(
                        src_col, tgt_col_norm, fuzzy=True, exclude_targets=assigned_tgt_cols
                    )
                    if tgt_col:
                        assigned_tgt_cols.add(tgt_col)

                    tm.column_matches.append(
                        ColumnMatch(
                            source_col=src_col,
                            target_col=tgt_col,
                            confidence=col_confidence,
                        )
                    )

            suggestion.table_matches.append(tm)

        return suggestion

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _norm(name: str) -> str:
        """Normalise name: lowercase, remove underscores and hyphens."""
        return re.sub(r"[_\-\s]", "", name).lower()

    def _match_name(
        self,
        source: str,
        target_norm_lookup: dict[str, str],
        fuzzy: bool = True,
        exclude_targets: set[str] | None = None
    ) -> tuple[str | None, str]:
        """
        Try to find the best match for *source* in *target_norm_lookup*.
        Returns (matched_target_name | None, confidence).
        """
        norm_src = self._norm(source)
        excludes = exclude_targets or set()

        # Exact normalised match
        if norm_src in target_norm_lookup:
            tgt = target_norm_lookup[norm_src]
            if tgt not in excludes:
                return tgt, "exact"

        # Well-known conventions
        conventions = {
            "deletedat": "isdeleted",
            "createdat": "createdat",
            "updatedat": "updatedat",
        }
        if norm_src in conventions:
            tgt_norm = conventions[norm_src]
            if tgt_norm in target_norm_lookup:
                tgt = target_norm_lookup[tgt_norm]
                if tgt not in excludes:
                    return tgt, "exact"

        if not fuzzy:
            return None, "unmatched"

        # Fuzzy: target contains source or source contains target
        for norm_tgt, tgt_name in target_norm_lookup.items():
            if tgt_name in excludes:
                continue
            if norm_src in norm_tgt or norm_tgt in norm_src:
                return tgt_name, "fuzzy"

        return None, "unmatched"
