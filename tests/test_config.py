import json
import os

from db_migrator.config import _EMPTY_CONFIG, MigrationConfig, load_config, save_config
from db_migrator.config import get_table_mapping as get_global_table_mapping


def test_migration_config_properties(sample_config):
    assert sample_config.target_schema == "test_schema"
    assert sample_config.table_mapping == {
        "mysql_users": "pg_users",
        "mysql_orders": "pg_orders"
    }
    assert sample_config.column_mapping("mysql_users") == {
        "id": "user_id",
        "name": "full_name",
        "email": "email_address"
    }
    assert sample_config.column_mapping("non_existent") == {}
    assert sample_config.column_mapping() == {
        "mysql_users": {
            "id": "user_id",
            "name": "full_name",
            "email": "email_address"
        }
    }
    assert sample_config.type_overrides == {
        "mysql_users.id": "uuid",
        "mysql_users.meta": "jsonb"
    }
    assert sample_config.value_transforms == {
        "*.deletedAt -> *.is_deleted": {"type": "null_to_bool"},
        "mysql_users.status -> pg_users.status_id": {
            "type": "enum_to_int",
            "mapping": {"active": 1, "inactive": 0}
        }
    }

def test_migration_config_custom_rules(sample_config):
    assert sample_config.custom_rules("enum_mapping") == {
        "mysql_users": {
            "role": {
                "admin": "super_user",
                "user": "regular_user"
            }
        }
    }
    assert sample_config.custom_rules("required_defaults") == {
        "pg_users": {
            "created_by": "system"
        }
    }
    assert sample_config.custom_rules("ignored_source_columns") == {
        "mysql_users": ["temp_session_token", "debug_info"]
    }
    assert sample_config.custom_rules("non_existent") == {}

    # Test convenience helpers
    assert sample_config.get_table_mapping() == sample_config.table_mapping
    assert sample_config.get_column_mapping("mysql_users") == sample_config.column_mapping("mysql_users")
    assert sample_config.get_custom_rules() == sample_config.custom_rules()

    assert sample_config.get_enum_mapping("mysql_users", "role") == {
        "admin": "super_user",
        "user": "regular_user"
    }
    # Let's verify how it gets role admin:
    # In our fixture:
    # "enum_mapping": {
    #     "mysql_users.role": {
    #         "admin": "super_user",
    #         "user": "regular_user"
    #     }
    # }
    # If table is "mysql_users.role" and column is "admin" or similar. Let's check:
    # if table = "mysql_users.role" and column = "admin":
    # self.custom_rules("enum_mapping").get("mysql_users.role", {}).get("admin", {})
    # wait! self.custom_rules("enum_mapping").get("mysql_users.role") is { "admin": "super_user", ... }
    # but the second get is on the column. If column is "admin", it gets "super_user", which is not a dict, wait!
    # Ah! The method returns a dict[str, str]: text -> value mapping.
    # If the enum mapping key is "mysql_users.role", then:
    # custom_rules("enum_mapping").get("mysql_users", {}).get("role", {}) would return
    # the dict {"admin": "super_user", "user": "regular_user"}.
    # Let's check our fixture:
    # "mysql_users.role" is the key, which might be a typo in the fixture if
    # table name is mysql_users and column is role.
    # Let's adjust get_enum_mapping test to reflect a correct structure:
    # custom_rules("enum_mapping") should be:
    # { "mysql_users": { "role": { "admin": "super_user", "user": "regular_user" } } }
    # Let's verify this in the config file. Yes, this makes sense.
    # Let's fix the test/fixture if needed, or check how get_enum_mapping is used.
    # In our fixture, we had:
    # "enum_mapping": {
    #     "mysql_users.role": {
    #         "admin": "super_user",
    #         "user": "regular_user"
    #     }
    # }
    # If table is "mysql_users.role" and column is (some dummy), then we get
    # a dict {"admin": "super_user", "user": "regular_user"}.
    # So, sample_config.get_enum_mapping("mysql_users.role", "any") would return {},
    # but sample_config.get_enum_mapping("mysql_users", "role") would return
    # {"admin": "super_user"}? No, self.custom_rules("enum_mapping").get("mysql_users", {})
    # is {}, so get("role") is {}.
    # Let's look at the fixture's enum_mapping key: "mysql_users.role".
    # Let's change the fixture or test it correctly.
    # Actually, let's write the test based on the actual methods:
    # sample_config.get_enum_mapping("mysql_users.role", "") -> gets {}
    # Let's test with a mock config that has proper nesting:
    # "enum_mapping": { "mysql_users": { "role": { "admin": "super_user" } } }

def test_enum_mapping_nested(tmp_path):
    config_data = {
        "version": "2.0",
        "custom_rules": {
            "enum_mapping": {
                "mysql_users": {
                    "role": {
                        "admin": "super_user",
                        "user": "regular_user"
                    }
                }
            }
        }
    }
    file_path = tmp_path / "nested_config.json"
    with open(file_path, "w") as f:
        json.dump(config_data, f)
    cfg = MigrationConfig(str(file_path))
    assert cfg.get_enum_mapping("mysql_users", "role") == {
        "admin": "super_user",
        "user": "regular_user"
    }

def test_get_required_defaults(sample_config):
    assert sample_config.get_required_defaults("pg_users") == {"created_by": "system"}
    assert sample_config.get_required_defaults("non_existent") == {}

def test_get_ignored_columns(sample_config):
    assert sample_config.get_ignored_columns("mysql_users") == {"temp_session_token", "debug_info"}
    assert sample_config.get_ignored_columns("non_existent") == set()

def test_get_type_override(sample_config):
    assert sample_config.get_type_override("mysql_users", "id") == "uuid"
    assert sample_config.get_type_override("mysql_users", "meta") == "jsonb"
    assert sample_config.get_type_override("mysql_users", "non_existent") is None

def test_auto_map_fields(sample_config):
    mysql_cols = ["id", "name", "email", "deletedAt", "unmapped_col"]
    pg_cols = ["user_id", "full_name", "email_address", "is_deleted"]
    existing = {"id": "user_id"}

    res = sample_config.auto_map_fields(mysql_cols, pg_cols, existing)
    # Norm match: "name" (norm: name) matches nothing because pg_cols has
    # "full_name" (norm: fullname) and "email_address" (norm: emailaddress).
    # "deletedAt" (norm: deletedat) matches "is_deleted" (norm: isdeleted) via deletedat -> isdeleted convention.
    # Existing "id" -> "user_id" is preserved.
    assert res == {
        "id": "user_id",
        "deletedAt": "is_deleted"
    }

def test_config_non_existent_file(tmp_path):
    path = str(tmp_path / "does_not_exist.json")
    cfg = MigrationConfig(path)
    assert cfg.target_schema == "public"
    assert cfg.table_mapping == {}
    assert cfg._data["version"] == "2.0"

def test_config_save_reload(tmp_path):
    path = str(tmp_path / "save_test.json")
    cfg = MigrationConfig(path)
    cfg.save({"version": "2.0", "target_schema": "new_schema", "table_mapping": {"a": "b"}})

    cfg2 = MigrationConfig(path)
    assert cfg2.target_schema == "new_schema"
    assert cfg2.table_mapping == {"a": "b"}

    # Reload test
    cfg.reload()
    assert cfg.target_schema == "new_schema"

def test_migrate_v1_to_v2():
    v1_raw = {
        "target_schema": "v1_schema",
        "table_mapping": {"v1_tbl": "v2_tbl"}
    }
    v2_data = MigrationConfig._migrate_v1_to_v2(v1_raw)
    assert v2_data["version"] == "2.0"
    assert v2_data["target_schema"] == "v1_schema"
    assert v2_data["custom_rules"] == _EMPTY_CONFIG["custom_rules"]

def test_legacy_shims(tmp_path, monkeypatch):
    # Test module-level global config functions
    # We will temporarily patch MigrationConfig initialization or use a custom global instance
    # First, let's write to the default path mapping_config.json using monkeypatching
    config_data = {"version": "2.0", "target_schema": "global_schema", "table_mapping": {"g_mysql": "g_pg"}}
    with open("mapping_config.json", "w") as f:
        json.dump(config_data, f)

    try:
        # Force re-initialization of default config
        import db_migrator.config
        db_migrator.config._default_config = None

        assert load_config()["target_schema"] == "global_schema"
        assert get_global_table_mapping() == {"g_mysql": "g_pg"}

        # Test save_config
        config_data["target_schema"] = "updated_global_schema"
        save_config(config_data)

        db_migrator.config._default_config.reload()
        assert load_config()["target_schema"] == "updated_global_schema"

    finally:
        if os.path.exists("mapping_config.json"):
            os.remove("mapping_config.json")
