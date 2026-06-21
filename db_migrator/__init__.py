#!/usr/bin/env python3
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
from .discovery import ColumnInfo as DiscoveryColumnInfo
from .discovery import MappingSuggestion, SchemaDiscovery, SchemaInfo, TableSchema
from .migrator import DatabaseMigrator
from .sql_parser import ColumnInfo as ParserColumnInfo
from .sql_parser import SQLFileParser, TableData
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
    "DiscoveryColumnInfo",
    "ParserColumnInfo",
]

__version__ = "1.0.0"
__author__ = "Pham Dinh Long"
