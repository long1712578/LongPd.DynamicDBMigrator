#!/usr/bin/env python3
"""
db_migrator/sql_parser.py
=========================
Parse MySQL dump (.sql) files into structured table data.

Key improvement over the old auto_sync.py
------------------------------------------
- OLD: for table in TABLES  (hardcoded list of 5 tables)
- NEW: discovers ALL tables in the file; caller can optionally filter

Public API
----------
    parser = SQLFileParser()
    data = parser.parse("backup.sql")            # all tables
    data = parser.parse("backup.sql", ["user"])  # specific tables

    data[table_name]  →  TableData(
        name, columns, raw_columns, inserts, create_sql
    )
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Giới hạn kích thước file SQL dump tối đa (2 GB)
# Ngăn chặn tấn công DoS bằng cách upload file quá lớn
_MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ColumnInfo:
    """Metadata for a single column parsed from CREATE TABLE."""
    name: str
    type: str
    nullable: bool = True
    default: Any = None
    is_primary_key: bool = False


@dataclass
class TableData:
    """All data parsed for a single table."""
    name: str
    columns: list[str]           = field(default_factory=list)  # filtered (ignored cols removed)
    raw_columns: list[str]       = field(default_factory=list)  # all columns as-is
    column_types: dict[str, str] = field(default_factory=dict)  # col_name → mysql_type
    inserts: list[tuple]         = field(default_factory=list)  # list of value tuples
    create_sql: str | None       = None


# ---------------------------------------------------------------------------
# Column type regex (covers the common MySQL column type declarations)
# ---------------------------------------------------------------------------

_COL_DEF_RE = re.compile(
    r"`(\w+)`\s+"
    r"((?:var)?char\(\d+\)|"
    r"tinyint\(\d+\)|smallint\(\d+\)|mediumint\(\d+\)|int\(\d+\)|bigint\(\d+\)|"
    r"tinyint|smallint|mediumint|int|integer|bigint|"
    r"decimal\(\d+,\d+\)|numeric\(\d+,\d+\)|"
    r"float\(\d+,\d+\)|float|double\(\d+,\d+\)|double|real|"
    r"timestamp(?:\(\d+\))?|datetime(?:\(\d+\))?|date|time(?:\(\d+\))?|year|"
    r"tinytext|mediumtext|longtext|text|"
    r"tinyblob|mediumblob|longblob|blob|"
    r"json|boolean|bool|bit\(\d+\)|"
    r"enum\([^)]+\)|set\([^)]+\)|"
    r"varbinary\(\d+\)|binary\(\d+\))",
    re.IGNORECASE,
)

_CREATE_TABLE_RE = re.compile(
    r"CREATE TABLE `(\w+)`\s*\(([\s\S]*?)\)\s*(?:ENGINE|;)",
    re.IGNORECASE,
)

_INSERT_RE = re.compile(
    r"INSERT INTO `(\w+)`\s*(\([^\)]*\))?\s*VALUES\s*([\s\S]*?)(?=;(?:\s*\n|$)|\Z)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# SQL parser
# ---------------------------------------------------------------------------

class SQLFileParser:
    """
    Parse a MySQL dump file into TableData objects.

    Parameters
    ----------
    ignored_columns : dict[str, set[str]]
        Table-level column ignore lists, e.g. {"user": {"factory", "temp"}}.
        Usually loaded from MigrationConfig.get_ignored_columns().
    """

    def __init__(self, ignored_columns: dict[str, set[str]] | None = None):
        self._ignored = ignored_columns or {}

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def parse(
        self,
        filepath: str,
        tables: list[str] | None = None,
        allowed_base_dir: str | None = None,
    ) -> dict[str, TableData]:
        """
        Parse *filepath* and return a dict of table_name → TableData.

        Args:
            filepath        : Path to .sql file.
            tables          : If given, only parse these table names.
                              If None, parse every table found in the file.
            allowed_base_dir: Nếu được cung cấp, kiểm tra filepath phải nằm trong
                              thư mục này để ngăn chặn Path Traversal attack.
                              Ví dụ: allowed_base_dir="./alldatapostgre"

        Security:
            - Path Traversal Prevention: Resolve đường dẫn tuyệt đối và
              kiểm tra filepath nằm trong allowed_base_dir
            - Max File Size: Từ chối file > 2GB để ngăn DoS
        """
        # Security: Resolve đường dẫn thực để tránh path traversal (../ attack)
        resolved = Path(filepath).resolve()

        if not resolved.exists():
            raise FileNotFoundError(f"SQL file not found: {filepath}")

        # Security: Kiểm tra path traversal nếu có allowed_base_dir
        if allowed_base_dir:
            base = Path(allowed_base_dir).resolve()
            try:
                resolved.relative_to(base)
            except ValueError as e:
                raise PermissionError(
                    f"Path traversal detected: '{filepath}' is outside allowed directory '{allowed_base_dir}'"
                ) from e

        # Security: Kiểm tra kích thước file trước khi đọc
        file_size = resolved.stat().st_size
        if file_size > _MAX_FILE_SIZE_BYTES:
            raise ValueError(
                f"File too large: {file_size / (1024**3):.1f}GB exceeds "
                f"maximum allowed {_MAX_FILE_SIZE_BYTES / (1024**3):.0f}GB"
            )

        logger.info("📖 Reading file: %s (%.1f MB)", filepath, file_size / (1024 * 1024))
        with open(resolved, encoding="utf-8", errors="replace") as f:
            content = f.read()

        result: dict[str, TableData] = {}

        # Pass 1 — parse CREATE TABLE
        for match in _CREATE_TABLE_RE.finditer(content):
            tbl_name = match.group(1)
            if tables and tbl_name not in tables:
                continue

            create_body = match.group(2)
            td = TableData(name=tbl_name)
            td.create_sql = f"CREATE TABLE `{tbl_name}` ({create_body})"

            self._parse_columns(tbl_name, create_body, td)
            result[tbl_name] = td
            logger.info(f"  ✓ CREATE TABLE `{tbl_name}` — {len(td.raw_columns)} columns")

        # Pass 2 — parse INSERT statements
        for match in _INSERT_RE.finditer(content):
            tbl_name = match.group(1)
            if tables and tbl_name not in tables:
                continue

            # Ensure TableData exists even when CREATE TABLE wasn't in the file
            if tbl_name not in result:
                result[tbl_name] = TableData(name=tbl_name)

            td = result[tbl_name]
            insert_cols_raw = match.group(2)  # "(col1, col2, ...)" or None
            values_block = match.group(3).strip()

            # Determine column order from INSERT header (if present)
            if insert_cols_raw:
                parsed_cols = re.findall(r"`([^`]+)`", insert_cols_raw)
                if parsed_cols:
                    td.raw_columns = parsed_cols

            # Apply ignored columns filter
            ignored = self._ignored.get(tbl_name, set())
            keep_indices = [
                i for i, c in enumerate(td.raw_columns) if c not in ignored
            ]
            td.columns = [td.raw_columns[i] for i in keep_indices]

            if ignored:
                logger.info(f"  ↷ `{tbl_name}`: skipping columns {ignored}")

            # Parse rows
            rows = self._parse_values(values_block)
            filtered = [
                tuple(row[i] if i < len(row) else None for i in keep_indices)
                for row in rows
            ]
            td.inserts.extend(filtered)

        # Summary
        for name, td in result.items():
            logger.info(f"  📊 `{name}`: {len(td.inserts)} rows")

        return result

    # ------------------------------------------------------------------
    # Column parsing
    # ------------------------------------------------------------------

    def _parse_columns(self, tbl_name: str, create_body: str, td: TableData) -> None:
        ignored = self._ignored.get(tbl_name, set())

        for m in _COL_DEF_RE.finditer(create_body):
            col_name = m.group(1)
            col_type = m.group(2)

            td.raw_columns.append(col_name)
            td.column_types[col_name] = col_type.lower()

            if col_name not in ignored:
                td.columns.append(col_name)

    # ------------------------------------------------------------------
    # Value parsing (robust state-machine tokeniser)
    # ------------------------------------------------------------------

    def _parse_values(self, values_block: str) -> list[tuple]:
        """
        Tokenise the VALUES block of a MySQL INSERT statement.

        Handles:
        - Quoted strings with escaped quotes: \', ''
        - NULL keyword
        - Nested JSON inside strings
        - Multi-row: (...), (...)
        """
        rows: list[tuple] = []
        current_row: list[str] = []
        current_val = ""
        in_string = False
        in_row = False
        quote_char = ""
        i = 0

        while i < len(values_block):
            ch = values_block[i]

            if in_string:
                if ch == "\\" and i + 1 < len(values_block):
                    current_val += ch + values_block[i + 1]
                    i += 2
                    continue
                elif ch == quote_char:
                    # Check for doubled-quote escape: '' or ""
                    if i + 1 < len(values_block) and values_block[i + 1] == quote_char:
                        current_val += ch + ch
                        i += 2
                        continue
                    current_val += ch
                    in_string = False
                else:
                    current_val += ch
            else:
                if ch in ("'", '"'):
                    if in_row:
                        in_string = True
                        quote_char = ch
                        current_val += ch
                elif ch == "(":
                    in_row = True
                    current_row = []
                    current_val = ""
                elif ch == ")":
                    if in_row:
                        current_row.append(current_val.strip())
                        if current_row:
                            rows.append(tuple(current_row))
                        current_row = []
                        current_val = ""
                        in_row = False
                elif ch == ",":
                    if in_row:
                        current_row.append(current_val.strip())
                        current_val = ""
                elif ch not in (" ", "\n", "\r", "\t"):
                    if in_row:
                        current_val += ch

            i += 1

        return rows
