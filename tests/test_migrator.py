from unittest.mock import MagicMock, patch

import pytest

from db_migrator.config import MigrationConfig
from db_migrator.migrator import DatabaseMigrator
from db_migrator.sql_parser import TableData


@pytest.fixture
def mock_migrator_config(tmp_path):
    # Minimal config for mocking DatabaseMigrator
    cfg = MigrationConfig(str(tmp_path / "dummy.json"))
    cfg._data = {
        "version": "2.0",
        "target_schema": "public",
        "table_mapping": {
            "mysql_users": "pg_users"
        },
        "column_mapping": {
            "mysql_users": {
                "id": "user_id",
                "name": "full_name"
            }
        },
        "type_overrides": {},
        "value_transforms": {},
        "custom_rules": {
            "ignored_source_columns": {
                "mysql_users": ["debug_info"]
            }
        }
    }
    return cfg

@patch("db_migrator.migrator._mysql_connect")
def test_read_from_mysql(mock_mysql_connect, mock_migrator_config):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_mysql_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor

    # Mock DESCRIBE mysql_users and SELECT * FROM mysql_users
    mock_cursor.fetchall.side_effect = [
        [("id", "int(11)"), ("name", "varchar(255)"), ("debug_info", "text")], # DESCRIBE
        [(1, "A", "debug1"), (2, "B", "debug2")] # SELECT
    ]

    migrator = DatabaseMigrator(config=mock_migrator_config)
    result = migrator._read_from_mysql({}, ["mysql_users"])

    assert "mysql_users" in result
    td = result["mysql_users"]
    assert td.name == "mysql_users"
    assert td.columns == ["id", "name"]
    assert td.inserts == [("1", "A", "debug1"), ("2", "B", "debug2")]

@patch("db_migrator.migrator._mysql_connect")
def test_import_to_mysql(mock_mysql_connect, mock_migrator_config):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_mysql_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor

    # Mock SHOW COLUMNS FROM mysql_users
    mock_cursor.fetchall.return_value = [("id",), ("name",)]

    td = TableData(
        name="mysql_users",
        columns=["id", "name"],
        raw_columns=["id", "name"],
        column_types={"id": "int(11)", "name": "varchar(255)"},
        inserts=[("1", "A"), ("2", "B")],
        create_sql="CREATE TABLE `mysql_users` (`id` int, `name` varchar)"
    )

    migrator = DatabaseMigrator(config=mock_migrator_config)
    stats = migrator._import_to_mysql({"mysql_users": td}, {}, "truncate_insert")

    assert "mysql_users" in stats
    assert stats["mysql_users"]["success"] == 2
    assert stats["mysql_users"]["errors"] == 0

@patch("db_migrator.migrator._pg_connect")
def test_sync_to_postgres(mock_pg_connect, mock_migrator_config):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_pg_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cursor

    # Mock is_identity columns check (empty) and PK columns fetch (empty) and SELECT COUNT(*) fetch
    mock_cursor.fetchall.side_effect = [
        [],  # identity check
        [],  # primary key check
        [(2,)] # COUNT(*) verification
    ]

    td = TableData(
        name="mysql_users",
        columns=["id", "name"],
        raw_columns=["id", "name"],
        column_types={"id": "int(11)", "name": "varchar(255)"},
        inserts=[("1", "A"), ("2", "B")]
    )

    migrator = DatabaseMigrator(config=mock_migrator_config)
    stats = migrator._sync_to_postgres({"mysql_users": td}, {}, "truncate_insert")

    assert "mysql_users" in stats
    assert stats["mysql_users"]["success"] == 2
    assert stats["mysql_users"]["errors"] == 0

@patch("db_migrator.migrator.DatabaseMigrator._sync_to_postgres")
@patch("db_migrator.migrator.DatabaseMigrator._read_from_mysql")
def test_migrate_mysql_to_postgres(mock_read, mock_sync, mock_migrator_config):
    mock_read.return_value = {}
    mock_sync.return_value = {"mysql_users": {"success": 10, "errors": 0}}

    migrator = DatabaseMigrator(config=mock_migrator_config)
    stats = migrator.migrate_mysql_to_postgres({}, {})

    assert stats == {"mysql_users": {"success": 10, "errors": 0}}

@patch("db_migrator.migrator.DatabaseMigrator._import_to_mysql")
@patch("db_migrator.sql_parser.SQLFileParser.parse")
def test_migrate_file_to_mysql(mock_parse, mock_import, mock_migrator_config):
    mock_parse.return_value = {}
    mock_import.return_value = {"mysql_users": {"success": 5, "errors": 0}}

    migrator = DatabaseMigrator(config=mock_migrator_config)
    stats = migrator.migrate_file_to_mysql("backup.sql", {})

    assert stats == {"mysql_users": {"success": 5, "errors": 0}}

@patch("db_migrator.migrator.DatabaseMigrator.migrate_mysql_to_postgres")
@patch("db_migrator.migrator.DatabaseMigrator.migrate_file_to_mysql")
def test_migrate_file_to_postgres(mock_file_to_mysql, mock_mysql_to_postgres, mock_migrator_config):
    mock_file_to_mysql.return_value = {"mysql_users": {"success": 5, "errors": 0}}
    mock_mysql_to_postgres.return_value = {"mysql_users": {"success": 5, "errors": 0}}

    migrator = DatabaseMigrator(config=mock_migrator_config)
    stats = migrator.migrate_file_to_postgres("backup.sql", {}, {})

    assert "mysql_users" in stats
    assert stats["mysql_users"]["pg_success"] == 5
    assert stats["mysql_users"]["mysql_success"] == 5
