#!/usr/bin/env python3
"""
db_migrator/security_utils.py
==============================
Enterprise-grade SQL security utilities.

Mục đích
--------
Cung cấp các hàm tiện ích để ngăn chặn SQL Injection thông qua:
  1. ``sanitize_identifier()``  — Whitelist-only kiểm tra tên bảng/cột
  2. ``build_pg_insert_sql()``  — Xây dựng câu lệnh INSERT an toàn với psycopg2.sql
  3. ``build_pg_delete_sql()``  — Xây dựng câu lệnh DELETE an toàn với psycopg2.sql
  4. ``build_pg_count_sql()``   — Xây dựng câu lệnh COUNT an toàn với psycopg2.sql

So sánh với .NET (EntityFramework / Dapper):
--------------------------------------------
  # .NET Dapper (parameterized):
  conn.Execute("INSERT INTO @table ...", new { table = tableName })  # SAI — tên bảng không thể parameterize
  conn.Execute($"INSERT INTO [{tableName}] ...", data)               # Đúng — escape tên bảng bằng bracket

  # Python (psycopg2.sql):
  sql = SQL("INSERT INTO {}.{}").format(Identifier(schema), Identifier(table))
  cursor.execute(sql, values)   # ✓ An toàn hoàn toàn

Tại sao không dùng f-string?
-----------------------------
  f"INSERT INTO {table} VALUES ..."  →  SQL INJECTION nếu table = "users; DROP TABLE users;"
  psycopg2.sql.Identifier("users")  →  Auto-quote: "users" — không thể inject
"""

from __future__ import annotations

import re

__all__ = [
    "sanitize_identifier",
    "build_pg_insert_sql",
    "build_pg_delete_sql",
    "build_pg_count_sql",
    "build_pg_select_pks_sql",
]

# Pattern whitelist cho SQL identifiers: chỉ cho phép chữ, số, và dấu gạch dưới
# Đây là tiêu chuẩn OWASP cho SQL identifier validation
_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Độ dài tối đa của identifier trong PostgreSQL là 63 bytes
_MAX_IDENTIFIER_LEN = 63


# ---------------------------------------------------------------------------
# 2.1 Identifier Sanitization
# ---------------------------------------------------------------------------

def sanitize_identifier(name: str, context: str = "identifier") -> str:
    """
    Kiểm tra và làm sạch SQL identifier (tên bảng, tên cột, tên schema).

    Chỉ cho phép các ký tự: [a-zA-Z0-9_], bắt đầu bằng chữ hoặc '_'.
    Raise ValueError nếu tên không hợp lệ.

    Tại sao phương pháp whitelist tốt hơn blacklist?
    ------------------------------------------------
    Blacklist (chặn ký tự nguy hiểm): '; DROP TABLE --'  → dễ bỏ sót
    Whitelist (chỉ cho phép ký tự an toàn): mọi thứ ngoài [a-zA-Z0-9_] đều bị từ chối

    Tương đương .NET SqlCommandBuilder.QuoteIdentifier() nhưng nghiêm ngặt hơn.

    Args:
        name    : Tên identifier cần kiểm tra
        context : Ngữ cảnh (để thông báo lỗi rõ ràng hơn)

    Returns:
        Tên identifier đã được validate (không bị thay đổi)

    Raises:
        ValueError : Nếu tên không hợp lệ

    Examples:
        >>> sanitize_identifier("users")          # OK → "users"
        >>> sanitize_identifier("my_table_v2")   # OK → "my_table_v2"
        >>> sanitize_identifier("users; DROP")   # ValueError!
        >>> sanitize_identifier("123table")       # ValueError! (bắt đầu bằng số)
    """
    if not isinstance(name, str):
        raise ValueError(f"Invalid {context}: expected string, got {type(name).__name__!r}")

    if not name:
        raise ValueError(f"Invalid {context}: name cannot be empty")

    if len(name) > _MAX_IDENTIFIER_LEN:
        raise ValueError(
            f"Invalid {context}: name '{name[:20]}...' exceeds PostgreSQL max length of {_MAX_IDENTIFIER_LEN}"
        )

    if not _IDENTIFIER_PATTERN.match(name):
        raise ValueError(
            f"Invalid {context}: '{name}' contains illegal characters. "
            f"Only [a-zA-Z0-9_] are allowed and name must start with a letter or underscore."
        )

    return name


# ---------------------------------------------------------------------------
# 2.2 Safe SQL Query Builders (PostgreSQL)
# ---------------------------------------------------------------------------

def build_pg_insert_sql(schema: str, table: str, columns: list[str]) -> object:
    """
    Xây dựng câu lệnh INSERT an toàn bằng psycopg2.sql.

    Sử dụng psycopg2.sql.Identifier để tự động escape các tên bảng/cột,
    ngăn chặn hoàn toàn SQL Injection dù tên bảng có ký tự đặc biệt.

    Tương đương C# Dapper:
        var sql = $"INSERT INTO [{schema}].[{table}] ...";  // Dùng bracket escape

    Args:
        schema  : Tên PostgreSQL schema (ví dụ: "public")
        table   : Tên bảng đích
        columns : Danh sách tên cột

    Returns:
        psycopg2.sql.Composable object sẵn sàng cho cursor.execute()

    Raises:
        ImportError : Nếu psycopg2 chưa được cài đặt
        ValueError  : Nếu tên schema/table/column không hợp lệ
    """
    try:
        from psycopg2 import sql
    except ImportError as e:
        raise ImportError("psycopg2-binary is required: pip install psycopg2-binary") from e

    # Validate tất cả identifiers trước khi build query
    sanitize_identifier(schema, "schema")
    sanitize_identifier(table, "table")
    for col in columns:
        sanitize_identifier(col, "column")

    col_identifiers = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(columns))

    return sql.SQL(
        "INSERT INTO {schema}.{table} ({columns}) VALUES ({placeholders})"
    ).format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
        columns=col_identifiers,
        placeholders=placeholders,
    )


def build_pg_delete_sql(schema: str, table: str) -> object:
    """
    Xây dựng câu lệnh DELETE an toàn bằng psycopg2.sql.

    Args:
        schema : Tên PostgreSQL schema
        table  : Tên bảng cần xóa

    Returns:
        psycopg2.sql.Composable object
    """
    try:
        from psycopg2 import sql
    except ImportError as e:
        raise ImportError("psycopg2-binary is required: pip install psycopg2-binary") from e

    sanitize_identifier(schema, "schema")
    sanitize_identifier(table, "table")

    return sql.SQL("DELETE FROM {schema}.{table}").format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
    )


def build_pg_count_sql(schema: str, table: str) -> object:
    """
    Xây dựng câu lệnh COUNT(*) an toàn bằng psycopg2.sql.

    Args:
        schema : Tên PostgreSQL schema
        table  : Tên bảng cần đếm

    Returns:
        psycopg2.sql.Composable object
    """
    try:
        from psycopg2 import sql
    except ImportError as e:
        raise ImportError("psycopg2-binary is required: pip install psycopg2-binary") from e

    sanitize_identifier(schema, "schema")
    sanitize_identifier(table, "table")

    return sql.SQL("SELECT COUNT(*) FROM {schema}.{table}").format(
        schema=sql.Identifier(schema),
        table=sql.Identifier(table),
    )


def build_pg_select_pks_sql() -> object:
    """
    Xây dựng câu lệnh SELECT primary keys từ pg_index.

    Sử dụng parameterized query với %s thay vì f-string vì
    schema/table được truyền vào làm ::regclass literal.

    Returns:
        Tuple (sql_string, params_template)
        Caller phải dùng: cursor.execute(sql, (schema_table_str,))
    """
    # Query này an toàn vì dùng parameterized %s, không phải f-string
    query = """
        SELECT a.attname
        FROM   pg_index i
        JOIN   pg_attribute a ON a.attrelid = i.indrelid
                             AND a.attnum = ANY(i.indkey)
        WHERE  i.indrelid = %s::regclass
        AND    i.indisprimary;
    """
    return query
