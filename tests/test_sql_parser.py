
import pytest

from db_migrator.sql_parser import SQLFileParser


def test_sql_parser_file_not_found():
    parser = SQLFileParser()
    with pytest.raises(FileNotFoundError):
        parser.parse("non_existent_file.sql")

def test_sql_parser_basic(sample_sql_file):
    parser = SQLFileParser()
    data = parser.parse(sample_sql_file)

    assert "mysql_users" in data
    td = data["mysql_users"]

    # Verify CREATE TABLE columns
    assert td.name == "mysql_users"
    assert td.raw_columns == [
        "id", "name", "email", "role", "status",
        "temp_session_token", "debug_info", "created_at"
    ]
    assert td.columns == td.raw_columns
    assert td.column_types["id"] == "int(11)"
    assert td.column_types["email"] == "varchar(255)"

    # Verify INSERT records
    assert len(td.inserts) == 3
    assert td.inserts[0] == (
        "1",
        "'Nguyen Van A'",
        "'a@example.com'",
        "'admin'",
        "'active'",
        "NULL",
        "'debug1'",
        "'2023-01-01 00:00:00'",
    )
    assert td.inserts[1] == (
        "2",
        "'Tran Thi B'",
        "'b@example.com'",
        "'user'",
        "'inactive'",
        "'token123'",
        "'debug2'",
        "'2023-01-02 12:00:00'",
    )
    # Check escaped strings
    assert td.inserts[2] == (
        "3",
        "'John Doe'",
        r"'john\'s.doe@example.com\n'",
        "'user'",
        "'active'",
        "NULL",
        "NULL",
        "'2023-01-03 08:30:00'",
    )

def test_sql_parser_table_filter(sample_sql_file):
    parser = SQLFileParser()
    # Filter for non-existent table
    data1 = parser.parse(sample_sql_file, tables=["non_existent"])
    assert len(data1) == 0

    # Filter for mysql_users
    data2 = parser.parse(sample_sql_file, tables=["mysql_users"])
    assert "mysql_users" in data2
    assert len(data2) == 1

def test_sql_parser_ignored_columns(sample_sql_file):
    # Ignore role and temp_session_token
    ignored = {"mysql_users": {"role", "temp_session_token"}}
    parser = SQLFileParser(ignored_columns=ignored)
    data = parser.parse(sample_sql_file)

    td = data["mysql_users"]
    # Check that td.columns does not contain ignored ones
    assert "role" not in td.columns
    assert "temp_session_token" not in td.columns
    assert len(td.columns) == 6

    # Verify inserts are filtered to match the columns size and alignment
    # columns order: id, name, email, status, debug_info, created_at
    assert len(td.inserts[0]) == 6
    # original: (1,'Nguyen Van A','a@example.com','admin','active',NULL,'debug1','2023-01-01 00:00:00')
    # filtered: ("1", "'Nguyen Van A'", "'a@example.com'", "'active'", "'debug1'", "'2023-01-01 00:00:00'")
    assert td.inserts[0] == ("1", "'Nguyen Van A'", "'a@example.com'", "'active'", "'debug1'", "'2023-01-01 00:00:00'")

def test_parse_values_escapes():
    parser = SQLFileParser()
    # Test double quote escape and nested string
    values_block = "(1, 'value \\'with\\' quotes', 'value with \"double\" quotes', 'nested \\'\"\\' json')"
    rows = parser._parse_values(values_block)
    assert len(rows) == 1
    assert rows[0] == ("1", "'value \\'with\\' quotes'", "'value with \"double\" quotes'", "'nested \\'\"\\' json'")

    # Test doubled single quote escape: 'john''s'
    values_block_2 = "(1, 'john''s')"
    rows2 = parser._parse_values(values_block_2)
    assert len(rows2) == 1
    assert rows2[0] == ("1", "'john''s'")
