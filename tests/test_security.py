"""
tests/test_security.py
======================
Unit tests cho Phase 2: Security Hardening.

Test Cases:
-----------
  - sanitize_identifier: SQL injection prevention
  - CredentialVault: 3-layer credential loading, Fernet encryption
  - MigrationAuditLog: immutable JSON Lines logging
  - SQLFileParser: Path traversal prevention, file size check
  - Web app: Security headers

Phương pháp TDD:
  1. Viết test trước khi viết code (Red)
  2. Code đủ để pass test (Green)
  3. Refactor (Refactor)
"""

import json
import os
import threading
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """Flask test client với security middleware."""
    from web.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# 2.1 Tests: SQL Identifier Sanitization
# ---------------------------------------------------------------------------

class TestSanitizeIdentifier:
    """
    Test SQL injection prevention thông qua identifier validation.

    OWASP Reference: https://owasp.org/www-community/attacks/SQL_Injection
    """

    def test_valid_simple_name(self):
        """Table/column names thông thường phải pass."""
        from db_migrator.security_utils import sanitize_identifier
        assert sanitize_identifier("users") == "users"
        assert sanitize_identifier("my_table") == "my_table"
        assert sanitize_identifier("column_v2") == "column_v2"
        assert sanitize_identifier("_private") == "_private"
        assert sanitize_identifier("PascalCase") == "PascalCase"

    def test_valid_with_numbers(self):
        """Tên có số ở giữa hoặc cuối phải pass."""
        from db_migrator.security_utils import sanitize_identifier
        assert sanitize_identifier("table2") == "table2"
        assert sanitize_identifier("col_123") == "col_123"

    def test_invalid_sql_injection_semicolon(self):
        """Tên chứa semicolon (SQL injection) phải bị từ chối."""
        from db_migrator.security_utils import sanitize_identifier
        with pytest.raises(ValueError, match="illegal characters"):
            sanitize_identifier("users; DROP TABLE users")

    def test_invalid_sql_injection_dash(self):
        """Tên chứa dấu gạch ngang phải bị từ chối."""
        from db_migrator.security_utils import sanitize_identifier
        with pytest.raises(ValueError, match="illegal characters"):
            sanitize_identifier("my-table")

    def test_invalid_starts_with_number(self):
        """Tên bắt đầu bằng số phải bị từ chối (SQL standard)."""
        from db_migrator.security_utils import sanitize_identifier
        with pytest.raises(ValueError, match="illegal characters"):
            sanitize_identifier("123table")

    def test_invalid_empty_string(self):
        """Tên rỗng phải bị từ chối."""
        from db_migrator.security_utils import sanitize_identifier
        with pytest.raises(ValueError, match="cannot be empty"):
            sanitize_identifier("")

    def test_invalid_none_type(self):
        """Kiểu không phải string phải bị từ chối."""
        from db_migrator.security_utils import sanitize_identifier
        with pytest.raises(ValueError, match="expected string"):
            sanitize_identifier(None)  # type: ignore

    def test_invalid_too_long(self):
        """Tên quá dài (>63 chars) phải bị từ chối."""
        from db_migrator.security_utils import sanitize_identifier
        long_name = "a" * 64
        with pytest.raises(ValueError, match="max length"):
            sanitize_identifier(long_name)

    def test_invalid_space(self):
        """Tên chứa khoảng trắng phải bị từ chối."""
        from db_migrator.security_utils import sanitize_identifier
        with pytest.raises(ValueError, match="illegal characters"):
            sanitize_identifier("my table")

    def test_invalid_quotes(self):
        """Tên chứa quote phải bị từ chối."""
        from db_migrator.security_utils import sanitize_identifier
        with pytest.raises(ValueError, match="illegal characters"):
            sanitize_identifier("tab'le")
        with pytest.raises(ValueError, match="illegal characters"):
            sanitize_identifier('tab"le')

    def test_context_in_error_message(self):
        """Error message phải chứa context để debug dễ hơn."""
        from db_migrator.security_utils import sanitize_identifier
        with pytest.raises(ValueError, match="schema"):
            sanitize_identifier("bad-schema", context="schema")


# ---------------------------------------------------------------------------
# 2.2 Tests: SQL Builder Functions
# ---------------------------------------------------------------------------

class TestPgSqlBuilders:
    """Test safe SQL query builders cho PostgreSQL."""

    def test_build_pg_delete_validates_schema(self):
        """build_pg_delete_sql phải validate schema name."""
        from db_migrator.security_utils import build_pg_delete_sql
        try:
            with pytest.raises((ValueError, ImportError)):
                build_pg_delete_sql("bad-schema", "users")
        except ImportError:
            pytest.skip("psycopg2 not installed")

    def test_build_pg_delete_validates_table(self):
        """build_pg_delete_sql phải validate table name."""
        from db_migrator.security_utils import build_pg_delete_sql
        try:
            with pytest.raises((ValueError, ImportError)):
                build_pg_delete_sql("public", "bad; DROP")
        except ImportError:
            pytest.skip("psycopg2 not installed")

    def test_build_pg_count_validates_identifiers(self):
        """build_pg_count_sql phải validate identifiers."""
        from db_migrator.security_utils import build_pg_count_sql
        try:
            with pytest.raises((ValueError, ImportError)):
                build_pg_count_sql("pub'lic", "users")
        except ImportError:
            pytest.skip("psycopg2 not installed")

    def test_build_pg_select_pks_returns_parameterized(self):
        """build_pg_select_pks_sql phải trả về parameterized query."""
        from db_migrator.security_utils import build_pg_select_pks_sql
        query = build_pg_select_pks_sql()
        # Phải dùng %s thay vì f-string
        assert "%s" in query
        assert "pg_index" in query


# ---------------------------------------------------------------------------
# 2.3 Tests: Credential Vault
# ---------------------------------------------------------------------------

class TestCredentialVault:
    """
    Test quản lý credentials an toàn.

    Security Note: Không dùng real credentials trong test —
    luôn mock os.environ hoặc dùng test values.
    """

    def test_get_connection_config_from_env(self):
        """Phải load credentials từ environment variables."""
        from db_migrator.security import CredentialVault
        env_vars = {
            "MYSQL_HOST": "testhost",
            "MYSQL_PORT": "3306",
            "MYSQL_DATABASE": "testdb",
            "MYSQL_USER": "testuser",
            "MYSQL_PASSWORD": "testpass",
        }
        with patch.dict(os.environ, env_vars, clear=False):
            vault = CredentialVault(env_file=None)
            config = vault.get_connection_config("mysql")

        assert config["host"] == "testhost"
        assert config["database"] == "testdb"
        assert config["user"] == "testuser"
        assert config["password"] == "testpass"  # noqa: S105 - test value, not a real credential
        assert config["port"] == 3306  # Phải convert sang int

    def test_get_connection_config_raises_when_missing(self):
        """Phải raise VaultError khi không có credentials."""
        from db_migrator.security import CredentialVault, VaultError
        # Clear các biến env MySQL
        with patch.dict(os.environ, {}, clear=True):
            vault = CredentialVault(env_file=None)
            with pytest.raises(VaultError, match="No credentials found"):
                vault.get_connection_config("mysql")

    def test_invalid_alias_raises(self):
        """Alias không hợp lệ phải raise VaultError."""
        from db_migrator.security import CredentialVault, VaultError
        vault = CredentialVault(env_file=None)
        with pytest.raises(VaultError, match="Unknown alias"):
            vault.get_connection_config("oracle")

    def test_encrypt_decrypt_roundtrip(self):
        """Mã hóa rồi giải mã phải cho ra data gốc."""
        from db_migrator.security import CredentialVault
        key = CredentialVault.generate_key()
        with patch.dict(os.environ, {"VAULT_KEY": key}):
            vault = CredentialVault(env_file=None)
            original = {"host": "localhost", "password": "s3cret"}
            encrypted = vault.encrypt_config(original)
            decrypted = vault.decrypt_config(encrypted)

        assert decrypted == original
        assert isinstance(encrypted, bytes)
        # Encrypted không được chứa plaintext password
        assert b"s3cret" not in encrypted

    def test_decrypt_with_wrong_key_raises(self):
        """Giải mã với sai key phải raise VaultError."""
        from db_migrator.security import CredentialVault, VaultError
        key1 = CredentialVault.generate_key()
        key2 = CredentialVault.generate_key()

        with patch.dict(os.environ, {"VAULT_KEY": key1}):
            vault1 = CredentialVault(env_file=None)
            encrypted = vault1.encrypt_config({"secret": "data"})

        with patch.dict(os.environ, {"VAULT_KEY": key2}):
            vault2 = CredentialVault(env_file=None)
            with pytest.raises(VaultError, match="decrypt"):
                vault2.decrypt_config(encrypted)

    def test_generate_key_returns_valid_fernet_key(self):
        """generate_key() phải tạo Fernet key hợp lệ."""
        from db_migrator.security import CredentialVault
        key = CredentialVault.generate_key()
        assert isinstance(key, str)
        # Fernet key là 44 ký tự base64
        assert len(key) == 44

    def test_encrypt_without_key_raises(self):
        """Mã hóa không có VAULT_KEY phải raise VaultError."""
        from db_migrator.security import CredentialVault, VaultError
        with patch.dict(os.environ, {}, clear=True):
            vault = CredentialVault(env_file=None)
            with pytest.raises(VaultError, match="VAULT_KEY"):
                vault.encrypt_config({"test": "data"})

    def test_flask_secret_key_from_env(self):
        """Flask secret key phải lấy từ FLASK_SECRET_KEY env var."""
        from db_migrator.security import CredentialVault
        with patch.dict(os.environ, {"FLASK_SECRET_KEY": "my-secret-key"}):
            vault = CredentialVault(env_file=None)
            assert vault.get_flask_secret_key() == "my-secret-key"

    def test_flask_secret_key_auto_generated(self):
        """Nếu không có FLASK_SECRET_KEY, phải tự tạo key ngẫu nhiên."""
        from db_migrator.security import CredentialVault
        env = {k: v for k, v in os.environ.items() if k != "FLASK_SECRET_KEY"}
        with patch.dict(os.environ, env, clear=True):
            vault = CredentialVault(env_file=None)
            key = vault.get_flask_secret_key()
            assert isinstance(key, str)
            assert len(key) > 16  # Phải đủ dài

    def test_save_and_load_encrypted_vault(self, tmp_path):
        """Lưu và đọc file vault phải round-trip chính xác."""
        from db_migrator.security import CredentialVault
        key = CredentialVault.generate_key()
        vault_path = str(tmp_path / "test.vault")
        config = {"host": "myhost", "password": "mypass"}

        with patch.dict(os.environ, {"VAULT_KEY": key}):
            vault = CredentialVault(env_file=None)
            vault.save_encrypted_config(config, vault_path)
            loaded = vault.load_encrypted_config(vault_path)

        assert loaded == config

    def test_load_nonexistent_vault_raises(self):
        """Load vault không tồn tại phải raise FileNotFoundError."""
        from db_migrator.security import CredentialVault
        vault = CredentialVault(env_file=None)
        with pytest.raises(FileNotFoundError):
            vault.load_encrypted_config("/nonexistent/path.vault")


# ---------------------------------------------------------------------------
# 2.4 Tests: Audit Log
# ---------------------------------------------------------------------------

class TestMigrationAuditLog:
    """
    Test Immutable Audit Trail.

    Các tính chất cần test:
    - Append-only (không xóa/sửa entries)
    - Structured (JSON Lines format)
    - Timestamped (có timestamp)
    - Thread-safe (đồng thời nhiều threads)
    """

    def test_log_creates_file(self, tmp_path):
        """Log phải tạo file audit.jsonl."""
        from db_migrator.audit import MigrationAuditLog
        log_file = str(tmp_path / "audit.jsonl")
        audit = MigrationAuditLog(log_path=log_file, echo_to_stdout=False)
        audit.log_event("test.event", {"key": "value"})
        assert os.path.exists(log_file)

    def test_log_is_valid_json_lines(self, tmp_path):
        """Mỗi dòng trong file phải là JSON hợp lệ."""
        from db_migrator.audit import MigrationAuditLog
        log_file = str(tmp_path / "audit.jsonl")
        audit = MigrationAuditLog(log_path=log_file, echo_to_stdout=False)

        audit.log_event("event1", {"foo": "bar"})
        audit.log_event("event2", {"baz": 123})

        with open(log_file, encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)  # Phải không raise JSONDecodeError
            assert "timestamp" in parsed
            assert "event" in parsed

    def test_log_migration_start(self, tmp_path):
        """log_migration_start phải ghi đủ thông tin."""
        from db_migrator.audit import AuditEvent, MigrationAuditLog
        log_file = str(tmp_path / "audit.jsonl")
        audit = MigrationAuditLog(log_path=log_file, echo_to_stdout=False)

        audit.log_migration_start(
            task_id="task-001",
            tables=["users", "orders"],
            strategy="upsert",
            source="mysql:localhost/db",
            target="pg:localhost/target.public",
        )

        events = audit.get_recent_events()
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == AuditEvent.MIGRATION_START
        assert ev["task_id"] == "task-001"
        assert ev["tables"] == ["users", "orders"]
        assert ev["strategy"] == "upsert"

    def test_log_migration_complete(self, tmp_path):
        """log_migration_complete phải ghi stats và duration."""
        from db_migrator.audit import AuditEvent, MigrationAuditLog
        log_file = str(tmp_path / "audit.jsonl")
        audit = MigrationAuditLog(log_path=log_file, echo_to_stdout=False)

        stats = {"users": {"success": 100, "errors": 2, "skipped": 1}}
        audit.log_migration_complete(task_id="task-001", stats=stats, duration_secs=15.5)

        events = audit.get_recent_events()
        ev = events[0]
        assert ev["event"] == AuditEvent.MIGRATION_COMPLETE
        assert ev["duration_seconds"] == 15.5
        assert ev["total_rows_migrated"] == 100
        assert ev["total_errors"] == 2

    def test_log_is_append_only(self, tmp_path):
        """Log phải chỉ append, không ghi đè."""
        from db_migrator.audit import MigrationAuditLog
        log_file = str(tmp_path / "audit.jsonl")

        # Log lần đầu
        audit1 = MigrationAuditLog(log_path=log_file, echo_to_stdout=False)
        audit1.log_event("event.first")

        # Log lần thứ hai (instance mới)
        audit2 = MigrationAuditLog(log_path=log_file, echo_to_stdout=False)
        audit2.log_event("event.second")

        events = audit2.get_recent_events(limit=10)
        assert len(events) == 2  # Phải có cả 2 events

    def test_log_sanitizes_password_in_error(self, tmp_path):
        """Log phải che password/secret trong error messages."""
        from db_migrator.audit import MigrationAuditLog
        log_file = str(tmp_path / "audit.jsonl")
        audit = MigrationAuditLog(log_path=log_file, echo_to_stdout=False)

        audit.log_migration_failed(
            task_id="task-001",
            error="Connection failed: password=myS3cretPwd123",
        )

        events = audit.get_recent_events()
        ev = events[0]
        assert "myS3cretPwd123" not in ev["error"]
        assert "***" in ev["error"]

    def test_log_thread_safe(self, tmp_path):
        """Ghi log từ nhiều threads không được corrupt file."""
        from db_migrator.audit import MigrationAuditLog
        log_file = str(tmp_path / "audit.jsonl")
        audit = MigrationAuditLog(log_path=log_file, echo_to_stdout=False)

        errors = []

        def write_events(thread_id):
            try:
                for i in range(10):
                    audit.log_event("thread.event", {"thread": thread_id, "i": i})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_events, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"

        # Tất cả 50 events phải được ghi thành công
        events = audit.get_recent_events(limit=100)
        assert len(events) == 50

    def test_get_recent_events_empty_file(self, tmp_path):
        """get_recent_events phải trả về list rỗng khi file không tồn tại."""
        from db_migrator.audit import MigrationAuditLog
        audit = MigrationAuditLog(log_path=str(tmp_path / "nonexistent.jsonl"), echo_to_stdout=False)
        assert audit.get_recent_events() == []


# ---------------------------------------------------------------------------
# 2.5 Tests: SQL Parser Security
# ---------------------------------------------------------------------------

class TestSQLParserSecurity:
    """Test các tính năng bảo mật mới trong SQLFileParser."""

    def test_path_traversal_prevention(self, tmp_path, sample_sql_file):
        """Path traversal attack phải bị chặn."""
        from db_migrator.sql_parser import SQLFileParser
        parser = SQLFileParser()

        # Tạo allowed_dir riêng biệt
        allowed_dir = str(tmp_path / "allowed")
        os.makedirs(allowed_dir, exist_ok=True)

        # Thử truy cập file ngoài allowed_dir (path traversal)
        with pytest.raises(PermissionError, match="Path traversal"):
            parser.parse(sample_sql_file, allowed_base_dir=allowed_dir)

    def test_normal_file_in_allowed_dir_passes(self, tmp_path):
        """File hợp lệ trong allowed_dir phải pass."""
        from db_migrator.sql_parser import SQLFileParser
        allowed_dir = str(tmp_path)
        sql_file = tmp_path / "test.sql"
        sql_file.write_text(
            "CREATE TABLE `t` (`id` int(11));\n"
            "INSERT INTO `t` VALUES (1);",
            encoding="utf-8",
        )
        parser = SQLFileParser()
        result = parser.parse(str(sql_file), allowed_base_dir=allowed_dir)
        assert "t" in result

    def test_file_too_large_raises(self, tmp_path, monkeypatch):
        """File quá lớn phải bị từ chối."""
        from db_migrator import sql_parser
        sql_file = tmp_path / "big.sql"
        sql_file.write_text("SELECT 1;", encoding="utf-8")

        # Mock _MAX_FILE_SIZE_BYTES = 1 byte để test
        monkeypatch.setattr(sql_parser, "_MAX_FILE_SIZE_BYTES", 1)

        from db_migrator.sql_parser import SQLFileParser
        parser = SQLFileParser()
        with pytest.raises(ValueError, match="too large"):
            parser.parse(str(sql_file))


# ---------------------------------------------------------------------------
# 2.6 Tests: Web App Security Headers
# ---------------------------------------------------------------------------

class TestWebSecurityHeaders:
    """Test Security Headers được thêm vào mọi HTTP response."""

    def test_security_headers_present(self, client):
        """Mọi response phải có đủ security headers."""
        response = client.get("/")

        assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert "Content-Security-Policy" in response.headers
        assert "Referrer-Policy" in response.headers

    def test_csp_header_restricts_sources(self, client):
        """Content-Security-Policy phải giới hạn nguồn tài nguyên."""
        response = client.get("/")
        csp = response.headers.get("Content-Security-Policy", "")
        assert "default-src 'self'" in csp

    def test_permissions_policy_restricts_apis(self, client):
        """Permissions-Policy phải tắt các Web APIs không cần thiết."""
        response = client.get("/")
        pp = response.headers.get("Permissions-Policy", "")
        assert "camera=()" in pp
        assert "microphone=()" in pp
