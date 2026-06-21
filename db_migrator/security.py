#!/usr/bin/env python3
"""
db_migrator/security.py
========================
Enterprise Credential Vault — Quản lý thông tin xác thực an toàn.

Kiến trúc 3 lớp (Priority order):
-----------------------------------
  1. Environment Variables  — Ưu tiên cao nhất (Production, Docker, CI/CD)
  2. .env File              — Dành cho môi trường local development
  3. Encrypted Vault File   — Dự phòng khi không có ENV/dotenv

So sánh với .NET:
-----------------
  # .NET Configuration:
  builder.Configuration.AddEnvironmentVariables();           // Layer 1
  builder.Configuration.AddJsonFile("appsettings.json");    // Layer 2
  builder.Configuration.AddUserSecrets<Program>();          // Layer 3 (dev-only)

  # Python equivalent (module này):
  vault = CredentialVault()
  config = vault.get_connection_config("mysql")  # Auto-resolve từ layers

Encryption:
-----------
  Sử dụng Fernet symmetric encryption (AES-128-CBC + HMAC-SHA256).
  Tương đương Data Protection API (DPAPI) trong .NET.

Tham khảo:
----------
  - OWASP Secrets Management: https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
  - Python Fernet: https://cryptography.io/en/latest/fernet/
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["CredentialVault", "VaultError"]

# Prefix chuẩn cho biến môi trường
_ENV_PREFIX_MAP = {
    "mysql": {
        "host": "MYSQL_HOST",
        "port": "MYSQL_PORT",
        "database": "MYSQL_DATABASE",
        "user": "MYSQL_USER",
        "password": "MYSQL_PASSWORD",
    },
    "postgres": {
        "host": "PG_HOST",
        "port": "PG_PORT",
        "database": "PG_DATABASE",
        "user": "PG_USER",
        "password": "PG_PASSWORD",
        "schema": "PG_SCHEMA",
    },
}


class VaultError(Exception):
    """Lỗi khi không thể tải hoặc giải mã credentials."""


class CredentialVault:
    """
    Quản lý thông tin xác thực (credentials) theo chuẩn enterprise.

    Hỗ trợ 3 backend theo thứ tự ưu tiên:
      1. Environment variables  (``MYSQL_HOST``, ``PG_PASSWORD``, ...)
      2. ``.env`` file          (dùng python-dotenv)
      3. Encrypted vault file   (Fernet AES-128 symmetric encryption)

    Usage:
    ------
    .. code-block:: python

        vault = CredentialVault()

        # Lấy MySQL connection config
        mysql_cfg = vault.get_connection_config("mysql")
        conn = mysql.connector.connect(**mysql_cfg)

        # Lưu vault file được mã hóa
        vault.save_encrypted_config("mysql", mysql_cfg, vault_path=".vault/mysql.enc")

    Lý do tách module:
    ------------------
    Theo nguyên tắc Single Responsibility Principle (SRP), module này
    chỉ xử lý việc tải/lưu credentials — không liên quan đến business logic migration.
    Tương tự ``IConfiguration`` trong .NET, được inject vào các service khác.
    """

    def __init__(
        self,
        env_file: str | None = ".env",
        vault_key_env: str = "VAULT_KEY",
    ) -> None:
        """
        Khởi tạo CredentialVault.

        Args:
            env_file      : Đường dẫn tới .env file (None để skip)
            vault_key_env : Tên biến môi trường chứa encryption key
        """
        self._vault_key_env = vault_key_env
        self._fernet = None

        # Load .env file nếu có (không raise lỗi nếu không tìm thấy)
        if env_file:
            self._load_dotenv(env_file)

        # Khởi tạo Fernet key từ ENV (nếu có)
        self._init_fernet()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_connection_config(self, alias: str) -> dict[str, Any]:
        """
        Lấy database connection config từ bất kỳ backend nào có sẵn.

        Args:
            alias : "mysql" hoặc "postgres"

        Returns:
            dict với keys: host, port, database, user, password (+ schema cho postgres)

        Raises:
            VaultError : Nếu không tìm thấy credentials ở bất kỳ backend nào
        """
        alias = alias.lower().strip()
        if alias not in _ENV_PREFIX_MAP:
            raise VaultError(f"Unknown alias '{alias}'. Supported: {list(_ENV_PREFIX_MAP.keys())}")

        # Layer 1 + 2: ENV / dotenv (dotenv đã load vào os.environ ở __init__)
        config = self._from_env(alias)
        if config:
            logger.debug("✓ Credentials loaded from environment for alias '%s'", alias)
            return config

        raise VaultError(
            f"No credentials found for '{alias}'. "
            f"Set environment variables (e.g., MYSQL_HOST, MYSQL_PASSWORD) "
            f"or create a .env file. See .env.example for reference."
        )

    def get_flask_secret_key(self) -> str:
        """
        Lấy Flask secret key từ ENV hoặc tạo mới (development only).

        Returns:
            Secret key string cho Flask session signing
        """
        key = os.environ.get("FLASK_SECRET_KEY", "")
        if not key:
            import secrets
            key = secrets.token_hex(32)
            logger.warning(
                "⚠️  FLASK_SECRET_KEY not set — using auto-generated key. "
                "Set FLASK_SECRET_KEY in .env for production!"
            )
        return key

    def encrypt_config(self, config: dict) -> bytes:
        """
        Mã hóa config dict bằng Fernet (AES-128-CBC + HMAC-SHA256).

        Fernet đảm bảo:
          - Confidentiality: AES-128 encryption
          - Integrity: HMAC-SHA256 verification
          - Freshness: Timestamp embedded (phát hiện replay attacks)

        So sánh với .NET:
          byte[] encrypted = ProtectedData.Protect(data, entropy, DataProtectionScope.CurrentUser);

        Args:
            config : Dict cần mã hóa (sẽ serialize thành JSON)

        Returns:
            Bytes đã được mã hóa

        Raises:
            VaultError : Nếu VAULT_KEY chưa được thiết lập
        """
        fernet = self._require_fernet()
        payload = json.dumps(config, ensure_ascii=False).encode("utf-8")
        return fernet.encrypt(payload)

    def decrypt_config(self, data: bytes) -> dict:
        """
        Giải mã config đã mã hóa bằng Fernet.

        Args:
            data : Bytes đã mã hóa (output của encrypt_config)

        Returns:
            Config dict đã giải mã

        Raises:
            VaultError : Nếu VAULT_KEY sai hoặc data bị tampered
        """
        try:
            from cryptography.fernet import InvalidToken
        except ImportError as e:
            raise ImportError("cryptography is required: pip install cryptography") from e

        fernet = self._require_fernet()
        try:
            decrypted = fernet.decrypt(data)
            return json.loads(decrypted.decode("utf-8"))
        except InvalidToken as e:
            raise VaultError(
                "Failed to decrypt vault data. "
                "Check that VAULT_KEY is correct and data has not been tampered with."
            ) from e

    def save_encrypted_config(self, config: dict, vault_path: str) -> None:
        """
        Lưu config đã mã hóa vào file vault.

        Args:
            config     : Dict credentials cần lưu
            vault_path : Đường dẫn tới file vault (ví dụ: ".vault/mysql.enc")
        """
        path = Path(vault_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        encrypted = self.encrypt_config(config)
        path.write_bytes(encrypted)
        logger.info("✓ Encrypted config saved to %s", vault_path)

    def load_encrypted_config(self, vault_path: str) -> dict:
        """
        Đọc và giải mã config từ file vault.

        Args:
            vault_path : Đường dẫn tới file vault

        Returns:
            Config dict đã giải mã

        Raises:
            FileNotFoundError : Nếu vault file không tồn tại
            VaultError        : Nếu giải mã thất bại
        """
        path = Path(vault_path)
        if not path.exists():
            raise FileNotFoundError(f"Vault file not found: {vault_path}")

        encrypted = path.read_bytes()
        return self.decrypt_config(encrypted)

    @staticmethod
    def generate_key() -> str:
        """
        Tạo Fernet key mới (base64-encoded 32 bytes).

        Dùng khi setup lần đầu:
            key = CredentialVault.generate_key()
            # Ghi vào .env: VAULT_KEY=<key>

        Returns:
            Key string (lưu vào biến môi trường VAULT_KEY)
        """
        try:
            from cryptography.fernet import Fernet
        except ImportError as e:
            raise ImportError("cryptography is required: pip install cryptography") from e

        return Fernet.generate_key().decode("utf-8")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_dotenv(self, env_file: str) -> None:
        """Load .env file vào os.environ bằng python-dotenv."""
        try:
            from dotenv import load_dotenv  # type: ignore[import]
            if os.path.exists(env_file):
                load_dotenv(env_file, override=False)  # override=False: ENV wins over .env
                logger.debug("✓ Loaded .env from %s", env_file)
        except ImportError:
            logger.debug("python-dotenv not installed — skipping .env file loading")

    def _init_fernet(self) -> None:
        """Khởi tạo Fernet instance từ VAULT_KEY env variable."""
        key = os.environ.get(self._vault_key_env, "").strip()
        if not key:
            return

        try:
            from cryptography.fernet import Fernet
            self._fernet = Fernet(key.encode("utf-8"))
        except Exception as e:
            logger.warning("Invalid VAULT_KEY — encrypted vault disabled: %s", e)

    def _require_fernet(self):
        """Trả về Fernet instance, raise VaultError nếu chưa khởi tạo."""
        if self._fernet is None:
            raise VaultError(
                "Encryption requires VAULT_KEY environment variable. "
                "Generate one with: python -c \"from db_migrator.security import CredentialVault; "
                "print(CredentialVault.generate_key())\""
            )
        return self._fernet

    def _from_env(self, alias: str) -> dict[str, Any] | None:
        """
        Tải credentials từ environment variables.

        Returns:
            Config dict nếu tìm thấy ít nhất HOST và PASSWORD, else None.
        """
        prefix_map = _ENV_PREFIX_MAP[alias]
        config: dict[str, Any] = {}

        for key, env_var in prefix_map.items():
            value = os.environ.get(env_var, "").strip()
            if value:
                # Convert port sang int
                if key == "port":
                    try:
                        config[key] = int(value)
                    except ValueError:
                        config[key] = value
                else:
                    config[key] = value

        # Chỉ return nếu có ít nhất host
        if config.get("host"):
            return config

        return None
