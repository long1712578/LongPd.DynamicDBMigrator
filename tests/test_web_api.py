from unittest.mock import patch

import pytest

from web.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_index_route(client):
    res = client.get("/")
    assert res.status_code == 200

def test_discover_from_file_missing_param(client):
    res = client.post("/api/discover/file", json={})
    assert res.status_code == 400
    assert "Thiếu tên file" in res.json["message"]

def test_discover_from_file_not_found(client):
    res = client.post("/api/discover/file", json={"filename": "does_not_exist.sql"})
    assert res.status_code == 404
    assert "Không tìm thấy file" in res.json["message"]

@patch("db_migrator.discovery.SchemaDiscovery.from_sql_file")
def test_discover_from_file_success(mock_from_sql_file, client, tmp_path):
    # Mock return schema
    from db_migrator.discovery import ColumnInfo, TableSchema
    mock_ts = TableSchema(name="mysql_users", columns=[
        ColumnInfo(name="id", data_type="int", is_primary_key=True),
        ColumnInfo(name="name", data_type="varchar")
    ], primary_key=["id"])
    mock_from_sql_file.return_value = {"mysql_users": mock_ts}

    # Create empty dummy file
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with open(tmp_path / "dummy.sql", "w") as f:
        f.write("")

    res = client.post("/api/discover/file", json={"filename": "dummy.sql"})
    assert res.status_code == 200
    assert res.json["success"] is True
    assert "mysql_users" in res.json["schema"]
    assert res.json["tables"] == ["mysql_users"]

@patch("db_migrator.discovery.SchemaDiscovery.from_mysql")
def test_discover_from_mysql_success(mock_from_mysql, client):
    from db_migrator.discovery import TableSchema
    mock_from_mysql.return_value = {"mysql_users": TableSchema(name="mysql_users")}

    res = client.post("/api/discover/mysql", json={"host": "localhost"})
    assert res.status_code == 200
    assert res.json["success"] is True
    assert "mysql_users" in res.json["schema"]

@patch("db_migrator.discovery.SchemaDiscovery.from_postgres")
def test_discover_from_postgres_success(mock_from_postgres, client):
    from db_migrator.discovery import TableSchema
    mock_from_postgres.return_value = {"pg_users": TableSchema(name="pg_users")}

    res = client.post("/api/discover/postgres", json={"config": {"host": "localhost"}})
    assert res.status_code == 200
    assert res.json["success"] is True
    assert "pg_users" in res.json["schema"]

@patch("db_migrator.discovery.SchemaDiscovery.suggest_mapping")
def test_suggest_mapping_endpoint(mock_suggest, client):
    from db_migrator.discovery import MappingSuggestion
    mock_suggest.return_value = MappingSuggestion()

    payload = {
        "source_schema": {"mysql_users": {"columns": [{"name": "id", "type": "int"}], "primary_key": ["id"]}},
        "target_schema": {"pg_users": {"columns": [{"name": "user_id", "type": "integer"}], "primary_key": ["user_id"]}}
    }
    res = client.post("/api/mapping/suggest", json=payload)
    assert res.status_code == 200
    assert res.json["success"] is True
    assert "mapping" in res.json

@patch("db_migrator.config.MigrationConfig.save")
def test_save_mapping_endpoint(mock_save, client):
    payload = {
        "table_mapping": {"mysql_tbl": "pg_tbl"},
        "column_mapping": {"mysql_tbl": {"id": "uid"}},
        "value_transforms": {"*.deletedAt -> *.is_deleted": {"type": "null_to_bool"}},
        "target_schema": "new_schema"
    }
    res = client.post("/api/mapping/save", json=payload)
    assert res.status_code == 200
    assert res.json["success"] is True
    assert mock_save.called

@patch("db_migrator.migrator.DatabaseMigrator.migrate_file_to_postgres")
def test_start_migration_endpoint(mock_migrate, client, tmp_path):
    app.config["UPLOAD_FOLDER"] = str(tmp_path)
    with open(tmp_path / "test.sql", "w") as f:
        f.write("")

    mock_migrate.return_value = {"mysql_users": {"success": 10, "errors": 0, "skipped": 0}}

    payload = {
        "flow": "file_to_postgres",
        "sql_filename": "test.sql",
        "mysql_config": {},
        "pg_config": {},
        "strategy": "truncate_insert"
    }
    res = client.post("/api/migrate/start", json=payload)
    assert res.status_code == 200
    assert res.json["success"] is True
    assert "task_id" in res.json

    task_id = res.json["task_id"]

    # Wait for the background task to register/run
    import time
    # Check status endpoint
    for _ in range(10):
        status_res = client.get(f"/api/migrate/status/{task_id}")
        assert status_res.status_code == 200
        if status_res.json["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)

    # Check final status
    status_res = client.get(f"/api/migrate/status/{task_id}")
    assert status_res.json["success"] is True
    assert status_res.json["status"] == "completed"
    assert status_res.json["progress"] == 100
    assert status_res.json["summary"]["success"] == 10

def test_get_migration_status_not_found(client):
    res = client.get("/api/migrate/status/non_existent_id")
    assert res.status_code == 404
