import json

import pytest

from db_migrator.config import MigrationConfig


@pytest.fixture
def tmp_config_file(tmp_path):
    config_data = {
        "version": "2.0",
        "target_schema": "test_schema",
        "table_mapping": {
            "mysql_users": "pg_users",
            "mysql_orders": "pg_orders"
        },
        "column_mapping": {
            "mysql_users": {
                "id": "user_id",
                "name": "full_name",
                "email": "email_address"
            }
        },
        "type_overrides": {
            "mysql_users.id": "uuid",
            "mysql_users.meta": "jsonb"
        },
        "value_transforms": {
            "*.deletedAt -> *.is_deleted": {"type": "null_to_bool"},
            "mysql_users.status -> pg_users.status_id": {
                "type": "enum_to_int",
                "mapping": {"active": 1, "inactive": 0}
            }
        },
        "custom_rules": {
            "enum_mapping": {
                "mysql_users": {
                    "role": {
                        "admin": "super_user",
                        "user": "regular_user"
                    }
                }
            },
            "required_defaults": {
                "pg_users": {
                    "created_by": "system"
                }
            },
            "ignored_source_columns": {
                "mysql_users": ["temp_session_token", "debug_info"]
            }
        }
    }
    file_path = tmp_path / "mapping_config.json"
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=4)
    return str(file_path)

@pytest.fixture
def sample_config(tmp_config_file):
    return MigrationConfig(tmp_config_file)

@pytest.fixture
def sample_sql_file(tmp_path):
    sql_content = """
--
-- Table structure for table `mysql_users`
--
DROP TABLE IF EXISTS `mysql_users`;
CREATE TABLE `mysql_users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) DEFAULT NULL,
  `email` varchar(255) NOT NULL,
  `role` varchar(50) DEFAULT 'user',
  `status` varchar(20) DEFAULT 'active',
  `temp_session_token` varchar(255) DEFAULT NULL,
  `debug_info` text DEFAULT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

--
-- Dumping data for table `mysql_users`
--
LOCK TABLES `mysql_users` WRITE;
INSERT INTO `mysql_users` VALUES(1,'Nguyen Van A','a@example.com','admin','active',NULL,'debug1','2023-01-01 00:00:00'),
(2,'Tran Thi B','b@example.com','user','inactive','token123','debug2','2023-01-02 12:00:00'),
(3,'John Doe','john\\\'s.doe@example.com\\n','user','active',NULL,NULL,'2023-01-03 08:30:00');
UNLOCK TABLES;
"""
    file_path = tmp_path / "backup.sql"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(sql_content)
    return str(file_path)
