from unittest.mock import MagicMock, patch

import pytest

from db_migrator.discovery import ColumnInfo, ColumnMatch, MappingSuggestion, SchemaDiscovery, TableMatch, TableSchema


def test_schema_data_structures():
    col1 = ColumnInfo(name="id", data_type="int", is_primary_key=True)
    col2 = ColumnInfo(name="name", data_type="varchar", nullable=True)
    ts = TableSchema(name="users", columns=[col1, col2], primary_key=["id"])

    assert ts.column_names() == ["id", "name"]
    assert ts.column_type("id") == "int"
    assert ts.column_type("name") == "varchar"
    assert ts.column_type("non_existent") is None

def test_discovery_from_sql_file(sample_sql_file):
    disc = SchemaDiscovery()
    schema = disc.from_sql_file(sample_sql_file)

    assert "mysql_users" in schema
    ts = schema["mysql_users"]
    assert ts.name == "mysql_users"
    assert ts.primary_key == ["id"]
    assert len(ts.columns) == 8

    col_names = ts.column_names()
    assert "id" in col_names
    assert "name" in col_names
    assert "email" in col_names

def test_discovery_from_sql_file_not_found():
    disc = SchemaDiscovery()
    with pytest.raises(FileNotFoundError):
        disc.from_sql_file("non_existent_dump.sql")

def test_discovery_suggest_mapping():
    # Source schema (MySQL)
    mysql_users = TableSchema(name="mysql_users", columns=[
        ColumnInfo(name="id", data_type="int"),
        ColumnInfo(name="first_name", data_type="varchar"),
        ColumnInfo(name="deletedAt", data_type="timestamp"),
        ColumnInfo(name="extra_col", data_type="text")
    ])
    source_schema = {"mysql_users": mysql_users}

    # Target schema (Postgres)
    pg_users = TableSchema(name="users", columns=[
        ColumnInfo(name="user_id", data_type="int"),
        ColumnInfo(name="first_name", data_type="text"),
        ColumnInfo(name="is_deleted", data_type="boolean")
    ])
    target_schema = {"users": pg_users}

    disc = SchemaDiscovery()

    # Run mapping suggestion
    suggestion = disc.suggest_mapping(source_schema, target_schema)
    assert len(suggestion.table_matches) == 1

    tm = suggestion.table_matches[0]
    # table name "mysql_users" (norm: mysqlusers) and "users" (norm: users) -> fuzzy match
    assert tm.source_table == "mysql_users"
    assert tm.target_table == "users"
    assert tm.confidence == "fuzzy"

    # Check column matches
    col_matches = {cm.source_col: cm for cm in tm.column_matches}

    # deletedAt -> is_deleted (convention)
    assert col_matches["deletedAt"].target_col == "is_deleted"
    assert col_matches["deletedAt"].confidence == "exact"

    # first_name -> first_name (exact norm match)
    assert col_matches["first_name"].target_col == "first_name"
    assert col_matches["first_name"].confidence == "exact"

    # id -> user_id (fuzzy match: target contains source or source contains target)
    assert col_matches["id"].target_col == "user_id"
    assert col_matches["id"].confidence == "fuzzy"

    # extra_col -> unmatched
    assert col_matches["extra_col"].target_col is None
    assert col_matches["extra_col"].confidence == "unmatched"

def test_discovery_suggest_mapping_with_existing():
    mysql_users = TableSchema(name="mysql_users", columns=[
        ColumnInfo(name="id", data_type="int"),
        ColumnInfo(name="first_name", data_type="varchar")
    ])
    source_schema = {"mysql_users": mysql_users}

    pg_users = TableSchema(name="pg_users", columns=[
        ColumnInfo(name="uid", data_type="int"),
        ColumnInfo(name="fname", data_type="text")
    ])
    target_schema = {"pg_users": pg_users}

    disc = SchemaDiscovery()

    # Existing mapping overrides fuzzy/unmatched
    existing_tbl = {"mysql_users": "pg_users"}
    existing_col = {"mysql_users": {"id": "uid", "first_name": "fname"}}

    suggestion = disc.suggest_mapping(
        source_schema, target_schema,
        existing_table_mapping=existing_tbl,
        existing_column_mapping=existing_col
    )

    tm = suggestion.table_matches[0]
    assert tm.target_table == "pg_users"
    col_matches = {cm.source_col: cm for cm in tm.column_matches}
    assert col_matches["id"].target_col == "uid"
    assert col_matches["first_name"].target_col == "fname"
    assert col_matches["id"].confidence == "exact"

def test_mapping_suggestion_to_config_dict():
    col_matches = [
        ColumnMatch(source_col="id", target_col="uid", confidence="exact"),
        ColumnMatch(source_col="name", target_col="fullname", confidence="fuzzy"),
        ColumnMatch(source_col="unmapped", target_col=None, confidence="unmatched")
    ]
    tm = TableMatch(
        source_table="mysql_tbl",
        target_table="pg_tbl",
        confidence="exact",
        column_matches=col_matches
    )
    suggestion = MappingSuggestion(table_matches=[tm])

    cfg = suggestion.to_config_dict()
    assert cfg["table_mapping"] == {"mysql_tbl": "pg_tbl"}
    assert cfg["column_mapping"] == {"mysql_tbl": {"id": "uid", "name": "fullname"}}

@patch("mysql.connector.connect")
def test_discovery_from_mysql(mock_connect):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor

    # Mock SHOW TABLES
    mock_cursor.fetchall.side_effect = [
        [("mysql_users",)],  # SHOW TABLES
        [                    # SHOW FULL COLUMNS FROM mysql_users
            ("id", "int(11)", None, "NO", "PRI", "NULL", "auto_increment", None, "User identifier"),
            ("name", "varchar(255)", None, "YES", "", "NULL", "", None, "")
        ]
    ]

    disc = SchemaDiscovery()
    schema = disc.from_mysql({
        "host": "localhost",
        "port": 3306,
        "database": "db",
        "user": "root",
        "password": ""
    })

    assert "mysql_users" in schema
    ts = schema["mysql_users"]
    assert ts.name == "mysql_users"
    assert ts.primary_key == ["id"]
    assert len(ts.columns) == 2
    assert ts.columns[0].name == "id"
    assert ts.columns[0].data_type == "int(11)"
    assert ts.columns[0].nullable is False
    assert ts.columns[0].is_primary_key is True
    assert ts.columns[0].is_unique is True
    assert ts.columns[0].extra == "auto_increment"
    assert ts.columns[0].comment == "User identifier"

@patch("psycopg2.connect")
def test_discovery_from_postgres(mock_connect):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor

    # Mock tables query and columns query
    mock_cursor.fetchall.side_effect = [
        [("pg_users",)],  # SELECT table_name
        [                 # SELECT column_name, data_type...
            ("user_id", "integer", "NO", "nextval('user_id_seq')", True),
            ("full_name", "text", "YES", "NULL", False)
        ]
    ]

    disc = SchemaDiscovery()
    schema = disc.from_postgres({
        "host": "localhost",
        "port": 5432,
        "database": "db",
        "user": "postgres",
        "password": ""
    }, schema="public")

    assert "pg_users" in schema
    ts = schema["pg_users"]
    assert ts.name == "pg_users"
    assert ts.primary_key == ["user_id"]
    assert len(ts.columns) == 2
    assert ts.columns[0].name == "user_id"
    assert ts.columns[0].data_type == "integer"
    assert ts.columns[0].nullable is False
    assert ts.columns[0].is_primary_key is True
