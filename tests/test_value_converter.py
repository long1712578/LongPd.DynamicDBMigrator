from db_migrator.value_converter import (
    BooleanTransform,
    DateTransform,
    EnumToIntTransform,
    IntegerTransform,
    JsonNormalizeTransform,
    NullToBoolTransform,
    PassthroughTransform,
    StringEscapeTransform,
    TimestampTransform,
    UuidValidateTransform,
    ValueConverter,
    _CompiledRule,
)


def test_base_transform_helpers():
    # We can test via PassthroughTransform which is a subclass of BaseTransform
    t = PassthroughTransform()
    assert t._is_null(None) is True
    assert t._is_null("") is True
    assert t._is_null("NULL") is True
    assert t._is_null("null") is True
    assert t._is_null("value") is False

    assert t._strip_quotes("'hello'") == "hello"
    assert t._strip_quotes("hello") == "hello"
    assert t._strip_quotes("'a'") == "a"
    assert t._strip_quotes("'") == "'"

    assert t._escape("hello 'world'") == "hello ''world''"
    assert t._escape("hello\x00world") == "hello world"

def test_null_to_bool_transform():
    t = NullToBoolTransform()
    assert t.apply(None) == "false"
    assert t.apply("NULL") == "false"
    assert t.apply("") == "false"
    assert t.apply("2023-01-01") == "true"

def test_enum_to_int_transform():
    t = EnumToIntTransform({"active": 1, "inactive": 0})
    assert t.apply("'active'") == "1"
    assert t.apply("inactive") == "0"
    assert t.apply("unknown") == "NULL"
    assert t.apply(None) == "NULL"

def test_json_normalize_transform():
    t = JsonNormalizeTransform(default="[]")
    assert t.apply(None) == "'[]'::jsonb"

    # Valid JSON
    assert t.apply("'{\"a\": 1}'") == "'{\"a\": 1}'::jsonb"
    # Escaped JSON from MySQL
    assert t.apply("'{\\\"a\\\": 1}'") == "'{\"a\": 1}'::jsonb"

    # Invalid JSON should be escaped but treated as string representation
    assert t.apply("'{invalid}'") == "'{invalid}'::jsonb"

def test_timestamp_transform():
    t_no_tz = TimestampTransform(timezone_offset=0)
    assert t_no_tz.apply("'2023-01-01 12:00:00'") == "'2023-01-01 12:00:00'"
    assert t_no_tz.apply(None) == "NULL"

    # Tz offset +7 (Vietnam timezone offset conversion)
    t_tz = TimestampTransform(timezone_offset=7)
    assert t_tz.apply("'2023-01-01 12:00:00'") == "'2023-01-01 19:00:00'"

    # Invalid datetime format
    assert t_no_tz.apply("invalid-date") == "'invalid-date'"

def test_date_transform():
    t = DateTransform()
    assert t.apply(None) == "NULL"
    assert t.apply("'2023-01-01'") == "'2023-01-01 00:00:00'::timestamp"
    assert t.apply("'invalid-date'") == "'invalid-date'::timestamp"

def test_uuid_validate_transform():
    t_no_gen = UuidValidateTransform(generate_if_missing=False)
    valid_uuid = "123e4567-e89b-12d3-a456-426614174000"
    assert t_no_gen.apply(f"'{valid_uuid}'") == f"'{valid_uuid}'"
    assert t_no_gen.apply("invalid-uuid") == "NULL"
    assert t_no_gen.apply(None) == "NULL"

    t_gen = UuidValidateTransform(generate_if_missing=True)
    assert t_gen.apply(None).startswith("'")
    assert len(t_gen.apply(None)) == 38  # 'uuid' is 36 + 2 quotes

def test_string_escape_transform():
    t = StringEscapeTransform(max_length=10, strip_html=True)
    assert t.apply(None) == "NULL"
    # HTML strip
    assert t.apply("'<div>hello &nbsp; world</div>'") == "'hello   wo'"

    # Escape single quote
    t_no_strip = StringEscapeTransform(strip_html=False)
    assert t_no_strip.apply("'john\\'s'") == "'john\\''s'"

def test_boolean_transform():
    t = BooleanTransform()
    assert t.apply(None) == "NULL"
    assert t.apply("1") == "true"
    assert t.apply("true") == "true"
    assert t.apply("yes") == "true"
    assert t.apply("0") == "false"
    assert t.apply("false") == "false"
    assert t.apply("no") == "false"
    assert t.apply("other") == "NULL"

def test_integer_transform():
    t = IntegerTransform()
    assert t.apply(None) == "NULL"
    assert t.apply("123") == "123"
    assert t.apply("123.45") == "123"
    assert t.apply("invalid") == "NULL"

def test_compiled_rule_matching():
    # Match specific source table and column
    rule1 = _CompiledRule("users.deletedAt -> *.is_deleted", NullToBoolTransform())
    assert rule1.matches("users", "deletedAt", "is_deleted") is True
    assert rule1.matches("orders", "deletedAt", "is_deleted") is False

    # Match any source table
    rule2 = _CompiledRule("*.deletedAt -> *.is_deleted", NullToBoolTransform())
    assert rule2.matches("users", "deletedAt", "is_deleted") is True
    assert rule2.matches("orders", "deletedAt", "is_deleted") is True

def test_value_converter_from_config(sample_config):
    converter = ValueConverter.from_config(sample_config)

    # Test config rule match (*.deletedAt -> *.is_deleted)
    assert converter.convert(None, "mysql_users", "deletedAt", "is_deleted") == "false"
    assert converter.convert("2023-01-01", "mysql_users", "deletedAt", "is_deleted") == "true"

    # Test config rule match (mysql_users.status -> pg_users.status_id)
    assert converter.convert("active", "mysql_users", "status", "status_id") == "1"

    # Test auto conversion for UUID (char(36))
    assert converter.convert("invalid", "mysql_users", "id", "user_id", "char(36)") == "NULL"

    # Test auto conversion for Boolean
    assert converter.convert("1", "mysql_users", "active", "active", "tinyint(1)") == "true"

    # Test auto conversion for datetime
    res = converter.convert("2023-01-01 12:00:00", "mysql_users", "created_at", "created_at", "timestamp")
    assert res == "'2023-01-01 12:00:00'"

    # Test auto conversion for JSON
    assert converter.convert('{"a":1}', "mysql_users", "meta", "meta", "json") == "'{\"a\": 1}'::jsonb"

    # Test default pass-through/string escape
    assert converter.convert("hello 'world'", "mysql_users", "name", "full_name", "varchar(255)") == "'hello ''world'''"
