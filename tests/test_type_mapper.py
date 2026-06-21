from db_migrator.type_mapper import TypeMapper


def test_type_mapper_exact_matches():
    mapper = TypeMapper()
    assert mapper.mysql_to_pg("char(36)") == "uuid"
    assert mapper.mysql_to_pg("varchar(36)") == "uuid"
    assert mapper.mysql_to_pg("tinyint(1)") == "boolean"
    assert mapper.mysql_to_pg("int") == "integer"
    assert mapper.mysql_to_pg("json") == "jsonb"
    assert mapper.mysql_to_pg("datetime") == "timestamp without time zone"

def test_type_mapper_pattern_matches():
    mapper = TypeMapper()
    assert mapper.mysql_to_pg("varchar(255)") == "text"
    assert mapper.mysql_to_pg("char(10)") == "text"
    assert mapper.mysql_to_pg("decimal(12,4)") == "numeric(12,4)"
    assert mapper.mysql_to_pg("numeric(8,2)") == "numeric(8,2)"
    assert mapper.mysql_to_pg("int(11)") == "integer"
    assert mapper.mysql_to_pg("varbinary(64)") == "bytea"

def test_type_mapper_overrides():
    overrides = {"users.id": "uuid", "users.meta": "jsonb"}
    mapper = TypeMapper(type_overrides=overrides)

    # Matching table + column
    assert mapper.mysql_to_pg("varchar(50)", "users", "id") == "uuid"
    # Not matching table
    assert mapper.mysql_to_pg("varchar(50)", "orders", "id") == "text"
    # No table/column provided (exact fallback)
    assert mapper.mysql_to_pg("varchar(36)") == "uuid"

def test_type_mapper_fallback():
    mapper = TypeMapper()
    assert mapper.mysql_to_pg("unknown_mysql_type") == "text"

def test_type_mapper_helpers():
    mapper = TypeMapper()

    # UUID helper
    assert mapper.is_uuid_type("char(36)") is True
    assert mapper.is_uuid_type("varchar(36)") is True
    assert mapper.is_uuid_type("varchar(50)") is False

    # Boolean helper
    assert mapper.is_boolean_type("tinyint(1)") is True
    assert mapper.is_boolean_type("bool") is True
    assert mapper.is_boolean_type("tinyint(3)") is False

    # Timestamp helper
    assert mapper.is_timestamp_type("timestamp") is True
    assert mapper.is_timestamp_type("datetime") is True
    assert mapper.is_timestamp_type("date") is False

    # Date helper
    assert mapper.is_date_type("date") is True
    assert mapper.is_date_type("datetime") is False

    # JSON helper
    assert mapper.is_json_type("json") is True
    assert mapper.is_json_type("jsonb") is True
    assert mapper.is_json_type("text") is False

    # Numeric helper
    assert mapper.is_numeric_type("int") is True
    assert mapper.is_numeric_type("decimal(10,2)") is True
    assert mapper.is_numeric_type("double") is True
    assert mapper.is_numeric_type("varchar(50)") is False

    # Text helper
    assert mapper.is_text_type("varchar(255)") is True
    assert mapper.is_text_type("text") is True
    assert mapper.is_text_type("enum('a','b')") is True
    assert mapper.is_text_type("int") is False

    # Binary helper
    assert mapper.is_binary_type("blob") is True
    assert mapper.is_binary_type("varbinary(100)") is True
    assert mapper.is_binary_type("varchar(100)") is False
