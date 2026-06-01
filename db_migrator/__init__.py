#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_migrator/__init__.py
=======================
Public API for the db_migrator library.

Quick start::

    from db_migrator import DatabaseMigrator, MigrationConfig, SchemaDiscovery

    cfg = MigrationConfig("mapping_config.json")
    migrator = DatabaseMigrator(cfg)

    # Full 3-step pipeline
    migrator.migrate_file_to_postgres(
        sql_file="alldatapostgre/backup.sql",
        mysql_config={"host": "...", "port": 3306, "database": "...", "user": "...", "password": "..."},
        pg_config={"host": "...", "port": 5432, "database": "...", "user": "...", "password": "..."},
    )
"""

from .config import MigrationConfig
from .discovery import SchemaDiscovery, MappingSuggestion, SchemaInfo, TableSchema, ColumnInfo as DiscoveryColumnInfo
from .migrator import DatabaseMigrator
from .sql_parser import SQLFileParser, TableData, ColumnInfo as ParserColumnInfo
from .type_mapper import TypeMapper
from .value_converter import ValueConverter

__all__ = [
    "MigrationConfig",
    "SchemaDiscovery",
    "MappingSuggestion",
    "SchemaInfo",
    "TableSchema",
    "DatabaseMigrator",
    "SQLFileParser",
    "TableData",
    "TypeMapper",
    "ValueConverter",
]

__version__ = "1.0.0"
__author__ = "Pham Dinh Long"