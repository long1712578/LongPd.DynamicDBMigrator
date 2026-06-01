#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
db_migrator/migrator.py
=======================
Core migration engine — orchestrates the full data transfer pipeline.

Supports:
    1. migrate_file_to_mysql()     — SQL dump → MySQL local  (Step 1+2)
    2. migrate_mysql_to_postgres() — MySQL live → PostgreSQL (Step 3)
    3. migrate_file_to_postgres()  — full flow: dump → MySQL → PostgreSQL

All public methods accept:
    tables=None   → process all tables found in config / source
    tables=[...]  → process only specific tables

Migration strategies:
    "truncate_insert"  — delete all rows, re-insert (default)
    "upsert"           — INSERT ... ON CONFLICT DO UPDATE (PostgreSQL)
    "append"           — INSERT without deleting existing rows
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from .config import MigrationConfig
from .sql_parser import SQLFileParser, TableData
from .type_mapper import TypeMapper
from .value_converter import ValueConverter

logger = logging.getLogger(__name__)

# Progress callback type: (table, done_rows, total_rows, message) → None
ProgressCallback = Callable[[str, int, int, str], None]


def _noop_progress(table: str, done: int, total: int, msg: str) -> None:
    pass


# ---------------------------------------------------------------------------
# MySQL helpers
# ---------------------------------------------------------------------------

def _mysql_connect(config: dict):
    try:
        import mysql.connector
    except ImportError:
        raise ImportError("mysql-connector-python is required. Run: pip install mysql-connector-python")
    return mysql.connector.connect(**config)


def _pg_connect(config: dict):
    try:
        import psycopg2
    except ImportError:
        raise ImportError("psycopg2-binary is required. Run: pip install psycopg2-binary")
    return psycopg2.connect(**config)


# ---------------------------------------------------------------------------
# DatabaseMigrator
# ---------------------------------------------------------------------------

class DatabaseMigrator:
    """
    High-level migration engine.

    Usage::

        cfg = MigrationConfig("mapping_config.json")
        m = DatabaseMigrator(cfg)

        # Full 3-step flow from SQL dump
        m.migrate_file_to_postgres(
            sql_file="alldatapostgre/backup.sql",
            mysql_config={...},
            pg_config={...},
        )

        # Only import dump into MySQL local
        m.migrate_file_to_mysql(
            sql_file="alldatapostgre/backup.sql",
            mysql_config={...},
        )

        # Only sync from MySQL to PostgreSQL (after dump is already in MySQL)
        m.migrate_mysql_to_postgres(
            mysql_config={...},
            pg_config={...},
        )
    """

    def __init__(
        self,
        config: MigrationConfig | None = None,
        on_progress: ProgressCallback | None = None,
    ):
        self._config = config or MigrationConfig()
        self._on_progress = on_progress or _noop_progress

        self._type_mapper = TypeMapper(type_overrides=self._config.type_overrides)
        self._value_converter = ValueConverter.from_config(self._config, self._type_mapper)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def migrate_file_to_mysql(
        self,
        sql_file: str,
        mysql_config: dict,
        tables: list[str] | None = None,
        strategy: str = "truncate_insert",
    ) -> dict[str, dict]:
        """
        Parse SQL dump and import into MySQL.

        Returns per-table stats: {table: {"success": n, "errors": n}}
        """
        logger.info("=" * 60)
        logger.info("Step 1+2: SQL File → MySQL")
        logger.info("=" * 60)

        parser = SQLFileParser(
            ignored_columns={
                t: self._config.get_ignored_columns(t)
                for t in (tables or [])
            } if tables else self._build_ignored_all()
        )
        tables_data = parser.parse(sql_file, tables)
        return self._import_to_mysql(tables_data, mysql_config, strategy)

    def migrate_mysql_to_postgres(
        self,
        mysql_config: dict,
        pg_config: dict,
        tables: list[str] | None = None,
        strategy: str = "truncate_insert",
    ) -> dict[str, dict]:
        """
        Read from live MySQL, write to PostgreSQL.

        Returns per-table stats.
        """
        logger.info("=" * 60)
        logger.info("Step 3: MySQL → PostgreSQL")
        logger.info("=" * 60)

        tables_data = self._read_from_mysql(mysql_config, tables)
        return self._sync_to_postgres(tables_data, pg_config, strategy)

    def migrate_file_to_postgres(
        self,
        sql_file: str,
        mysql_config: dict,
        pg_config: dict,
        tables: list[str] | None = None,
        strategy: str = "truncate_insert",
    ) -> dict[str, dict]:
        """
        Full 3-step pipeline: SQL file → MySQL local → PostgreSQL.

        Returns per-table stats from the PostgreSQL step.
        """
        logger.info("=" * 60)
        logger.info("Full Pipeline: SQL File → MySQL → PostgreSQL")
        logger.info("=" * 60)

        # Step 1+2: parse + import to MySQL
        mysql_stats = self.migrate_file_to_mysql(sql_file, mysql_config, tables, strategy)

        # Step 3: MySQL → PostgreSQL
        pg_stats = self.migrate_mysql_to_postgres(mysql_config, pg_config, tables, strategy)

        # Merge stats to report errors from both phases
        combined = {}
        for tbl in set(list(mysql_stats.keys()) + list(pg_stats.keys())):
            m = mysql_stats.get(tbl, {"success": 0, "errors": 0, "skipped": 0})
            p = pg_stats.get(tbl, {"success": 0, "errors": 0, "skipped": 0})
            combined[tbl] = {
                "success": p["success"],
                "errors": m["errors"] + p["errors"],
                "skipped": m.get("skipped", 0) + p.get("skipped", 0),
                "mysql_success": m["success"],
                "mysql_errors": m["errors"],
                "mysql_skipped": m.get("skipped", 0),
                "pg_success": p["success"],
                "pg_errors": p["errors"],
                "pg_skipped": p.get("skipped", 0),
            }
            
        return combined

    # ------------------------------------------------------------------
    # Internal: read from live MySQL
    # ------------------------------------------------------------------

    def _read_from_mysql(
        self,
        mysql_config: dict,
        tables: list[str] | None,
    ) -> dict[str, TableData]:
        """Read table data from a live MySQL connection."""
        conn = _mysql_connect(mysql_config)
        cursor = conn.cursor()

        target_tables = tables or list(self._config.table_mapping.keys())
        result: dict[str, TableData] = {}

        for table in target_tables:
            try:
                cursor.execute(f"DESCRIBE `{table}`")
                col_rows = cursor.fetchall()
                col_names = [r[0] for r in col_rows]
                col_types = {r[0]: r[1].lower() for r in col_rows}

                cursor.execute(f"SELECT * FROM `{table}`")
                rows = cursor.fetchall()

                ignored = self._config.get_ignored_columns(table)
                keep = [c for c in col_names if c not in ignored]

                td = TableData(name=table)
                td.raw_columns = col_names
                td.columns = keep
                td.column_types = col_types
                td.inserts = [
                    tuple(str(v) if v is not None else "NULL" for v in row)
                    for row in rows
                ]
                result[table] = td
                logger.info(f"  ✓ `{table}`: {len(rows)} rows read from MySQL")
            except Exception as e:
                logger.warning(f"  ⚠️  Error reading `{table}`: {e}")

        cursor.close()
        conn.close()
        return result

    # ------------------------------------------------------------------
    # Internal: import to MySQL
    # ------------------------------------------------------------------

    def _import_to_mysql(
        self,
        tables_data: dict[str, TableData],
        mysql_config: dict,
        strategy: str,
    ) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        conn = _mysql_connect(mysql_config)
        cursor = conn.cursor()

        logger.info(f"\n🔌 Connected MySQL: {mysql_config.get('host')}:{mysql_config.get('port')}/{mysql_config.get('database')}")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")

        for table, td in tables_data.items():
            s = {"success": 0, "errors": 0, "skipped": 0}
            stats[table] = s
            total = len(td.inserts)

            if not td.inserts:
                logger.info(f"  ⏭️  `{table}`: no data")
                continue

            # Create table if not exists
            if td.create_sql:
                try:
                    create_sql = td.create_sql.replace(
                        f"CREATE TABLE `{table}`",
                        f"CREATE TABLE IF NOT EXISTS `{table}`",
                    )
                    create_sql = re.sub(
                        r",?\s*CONSTRAINT[^,)]+FOREIGN KEY[^,)]+REFERENCES[^,)]+",
                        "", create_sql, flags=re.IGNORECASE,
                    )
                    create_sql = re.sub(
                        r",?\s*FOREIGN KEY[^,)]+REFERENCES[^,)]+",
                        "", create_sql, flags=re.IGNORECASE,
                    )
                    if "ENGINE" not in create_sql:
                        create_sql = create_sql.rstrip(";)") + ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
                    cursor.execute(create_sql)
                    conn.commit()
                except Exception as e:
                    logger.warning(f"  ⚠️  CREATE TABLE `{table}`: {e}")

            # Truncate if strategy requires it
            if strategy in ("truncate_insert",):
                try:
                    cursor.execute(f"TRUNCATE TABLE `{table}`")
                    conn.commit()
                except Exception as e:
                    logger.warning(f"  ⚠️  TRUNCATE `{table}`: {e}")

            # Determine valid columns (that exist in the destination table)
            try:
                cursor.execute(f"SHOW COLUMNS FROM `{table}`")
                existing_cols = {r[0] for r in cursor.fetchall()}
            except Exception:
                existing_cols = set(td.columns)

            valid_idx = [i for i, c in enumerate(td.columns) if c in existing_cols]
            columns = [td.columns[i] for i in valid_idx]

            if not columns:
                logger.warning(f"  ⚠️  `{table}`: no valid columns to insert")
                s["skipped"] += total
                continue

            placeholders = ", ".join(["%s"] * len(columns))
            cols_str = ", ".join([f"`{c}`" for c in columns])
            sql = f"INSERT INTO `{table}` ({cols_str}) VALUES ({placeholders})"

            for idx, row in enumerate(td.inserts):
                try:
                    filtered = [row[i] if i < len(row) else None for i in valid_idx]
                    values = [self._clean_mysql_value(v) for v in filtered]
                    # Pad / trim
                    values = (values + [None] * len(columns))[:len(columns)]
                    cursor.execute(sql, values)
                    s["success"] += 1
                except Exception as e:
                    s["errors"] += 1
                    if s["errors"] <= 3:
                        logger.warning(f"    Row error `{table}`: {str(e)[:120]}")

                if (idx + 1) % 500 == 0:
                    conn.commit()
                    self._on_progress(table, idx + 1, total, "importing to MySQL")

            conn.commit()
            logger.info(f"  ✓ `{table}`: {s['success']} inserted, {s['errors']} errors, {s['skipped']} skipped")

        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        cursor.close()
        conn.close()
        return stats

    @staticmethod
    def _clean_mysql_value(val: Any) -> Any:
        """Convert raw parsed string value to a Python type for MySQL insert."""
        if val is None or str(val).upper() == "NULL" or val == "":
            return None
        s = str(val)
        # Strip surrounding single-quotes added by the SQL parser
        if s.startswith("'") and s.endswith("'") and len(s) >= 2:
            inner = s[1:-1]
            inner = inner.replace("\\'", "'").replace("''", "'")
            return inner
        return s

    # ------------------------------------------------------------------
    # Internal: sync to PostgreSQL
    # ------------------------------------------------------------------

    def _sync_to_postgres(
        self,
        tables_data: dict[str, TableData],
        pg_config: dict,
        strategy: str,
    ) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        schema = self._config.target_schema

        conn = _pg_connect(pg_config)
        cursor = conn.cursor()
        logger.info(f"\n🔌 Connected PostgreSQL: {pg_config.get('host')}:{pg_config.get('port')}/{pg_config.get('database')}.{schema}")

        table_map = self._config.table_mapping

        # --- Truncate in dependency order ---
        if strategy == "truncate_insert":
            pg_tables = [table_map.get(t, t) for t in tables_data if table_map.get(t)]
            for pg_table in reversed(pg_tables):
                try:
                    cursor.execute(f'DELETE FROM "{schema}"."{pg_table}"')
                    deleted = cursor.rowcount
                    conn.commit()
                    logger.info(f"  🗑️  DELETE `{pg_table}`: {deleted} rows removed")
                except Exception as e:
                    logger.warning(f"  ⚠️  Cannot delete `{pg_table}`: {e}")
                    conn.rollback()

        # --- Insert ---
        for src_table, td in tables_data.items():
            pg_table = table_map.get(src_table)
            if not pg_table:
                logger.info(f"  ⏭️  `{src_table}`: no table mapping, skipping")
                continue

            s = {"success": 0, "errors": 0, "skipped": 0}
            stats[src_table] = s
            col_map = self._config.column_mapping(src_table)
            required_defaults = self._config.get_required_defaults(pg_table)
            total = len(td.inserts)
            identity_always_cols: set[str] = set()
            try:
                cursor.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                      AND table_name = %s
                      AND is_identity = 'YES'
                      AND identity_generation = 'ALWAYS'
                    """,
                    (schema, pg_table),
                )
                identity_always_cols = {str(r[0]).lower() for r in cursor.fetchall()}
            except Exception as e:
                logger.warning(f"  ⚠️  Could not read identity metadata for `{pg_table}`: {e}")
                conn.rollback()

            if not total:
                logger.info(f"  ⏭️  `{pg_table}`: no source data")
                continue

            # Fetch PK metadata once. PK is mandatory only for upsert conflict handling.
            pks = ["id"]
            try:
                cursor.execute(f"""
                    SELECT a.attname
                    FROM   pg_index i
                    JOIN   pg_attribute a ON a.attrelid = i.indrelid
                                         AND a.attnum = ANY(i.indkey)
                    WHERE  i.indrelid = '"{schema}"."{pg_table}"'::regclass
                    AND    i.indisprimary;
                """)
                fetched = [r[0] for r in cursor.fetchall()]
                if fetched:
                    pks = fetched
            except Exception as e:
                logger.warning(f"  ⚠️  Could not fetch PKs for `{pg_table}`: {e}")
                conn.rollback()

            id_cols_sql = ', '.join([f'"{pk}"' for pk in pks])
            
            batch_vals_list = []
            batch_cols = None

            for idx, row in enumerate(td.inserts):
                try:
                    pg_cols: list[str] = []
                    pg_vals: list[str] = []

                    for i, src_col in enumerate(td.columns):
                        tgt_col = col_map.get(src_col)
                        if tgt_col is None or f'"{tgt_col}"' in pg_cols:
                            continue

                        raw_val = row[i] if i < len(row) else None
                        mysql_type = td.column_types.get(src_col, "text")

                        converted = self._value_converter.convert(
                            value=raw_val,
                            source_table=src_table,
                            source_col=src_col,
                            target_col=tgt_col,
                            mysql_type=mysql_type,
                        )

                        pg_cols.append(f'"{tgt_col}"')
                        pg_vals.append(converted)

                    # Inject required defaults (e.g. TotalMember, Level)
                    for req_col, req_val in required_defaults.items():
                        if f'"{req_col}"' not in pg_cols:
                            pg_cols.append(f'"{req_col}"')
                            pg_vals.append(f"'{req_val}'")
                            
                    # Require non-null PK only for UPSERT.
                    # For append/truncate_insert, PK can be omitted when DB provides defaults/identity.
                    has_null_pk = False
                    if strategy == "upsert":
                        col_to_val = {
                            c.strip('"').lower(): v
                            for c, v in zip(pg_cols, pg_vals)
                        }
                        for pk in pks:
                            pk_key = str(pk).lower()
                            if pk_key not in col_to_val:
                                has_null_pk = True
                                break
                            if col_to_val[pk_key] == "NULL" or col_to_val[pk_key] is None:
                                has_null_pk = True
                                break

                    if not pg_cols or has_null_pk:
                        s["skipped"] += 1
                        if idx < 3:
                            logger.warning(f"  ⚠️  DEBUG SKIP: {pg_table} row {idx} - pg_cols: {pg_cols}, has_null_pk: {has_null_pk}")
                        continue
                        
                    if not batch_cols:
                        batch_cols = pg_cols
                        
                    batch_vals_list.append(f"({', '.join(pg_vals)})")

                except Exception as e:
                    logger.warning(f"    Row parse error `{pg_table}`: {str(e)[:150]}")
                    s["errors"] += 1

                # Execute batch
                if len(batch_vals_list) >= 500 or idx == total - 1:
                    if not batch_vals_list:
                        continue
                        
                    try:
                        cursor.execute("SAVEPOINT batch_savepoint")
                        
                        if strategy == "upsert":
                            needs_override_system = any(
                                c.strip('"').lower() in identity_always_cols
                                for c in (batch_cols or [])
                            )
                            overriding_clause = " OVERRIDING SYSTEM VALUE" if needs_override_system else ""
                            update_parts = [
                                f"{c} = EXCLUDED.{c}"
                                for c in batch_cols if c not in [f'"{pk}"' for pk in pks]
                            ]
                            insert_sql = f"""
                                INSERT INTO "{schema}"."{pg_table}"
                                ({', '.join(batch_cols)})
                                {overriding_clause}
                                VALUES {', '.join(batch_vals_list)}
                            """
                            if update_parts:
                                insert_sql += f"""
                                ON CONFLICT ({id_cols_sql}) DO UPDATE SET
                                {', '.join(update_parts)}
                                """
                            else:
                                insert_sql += f" ON CONFLICT ({id_cols_sql}) DO NOTHING"
                        else:
                            needs_override_system = any(
                                c.strip('"').lower() in identity_always_cols
                                for c in (batch_cols or [])
                            )
                            overriding_clause = " OVERRIDING SYSTEM VALUE" if needs_override_system else ""
                            insert_sql = f"""
                                INSERT INTO "{schema}"."{pg_table}"
                                ({', '.join(batch_cols)})
                                {overriding_clause}
                                VALUES {', '.join(batch_vals_list)}
                            """
                        
                        cursor.execute(insert_sql)
                        cursor.execute("RELEASE SAVEPOINT batch_savepoint")
                        s["success"] += len(batch_vals_list)
                        
                    except Exception as e:
                        cursor.execute("ROLLBACK TO SAVEPOINT batch_savepoint")
                        s["errors"] += len(batch_vals_list)
                        if s["errors"] <= 1500:
                            logger.warning(f"    Batch error `{pg_table}` (rows {idx-len(batch_vals_list)+1} to {idx}): {str(e)[:150]}")
                            
                    batch_vals_list = []
                    batch_cols = None
                    conn.commit()
                    self._on_progress(pg_table, idx + 1, total, "syncing to PostgreSQL")

            conn.commit()
            logger.info(f"  ✓ `{pg_table}`: {s['success']} inserted, {s['errors']} errors, {s['skipped']} skipped")

        # Verify counts
        logger.info("\n📊 Row count verification:")
        for src_table, td in tables_data.items():
            pg_table = table_map.get(src_table)
            if not pg_table:
                continue
            try:
                cursor.execute(f'SELECT COUNT(*) FROM "{schema}"."{pg_table}"')
                count = cursor.fetchone()[0]
                expected = len(td.inserts)
                ok = "✓" if count == expected else "⚠️"
                logger.info(f"  {ok} `{pg_table}`: {count} rows (expected {expected})")
            except Exception as e:
                logger.warning(f"  ❌ `{pg_table}`: {e}")

        cursor.close()
        conn.close()
        return stats

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_ignored_all(self) -> dict[str, set[str]]:
        """Build ignored-columns dict for all tables in config."""
        result = {}
        for table in self._config.table_mapping:
            ignored = self._config.get_ignored_columns(table)
            if ignored:
                result[table] = ignored
        return result