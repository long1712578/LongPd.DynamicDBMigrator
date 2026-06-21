#!/usr/bin/env python3
"""
db_migrator/audit.py
=====================
Immutable Audit Trail — Ghi nhật ký migration theo chuẩn enterprise.

Khái niệm Audit Trail:
----------------------
Audit Trail (hay Audit Log) là bản ghi lịch sử bất biến của mọi hoạt động
quan trọng trong hệ thống. Đặc điểm:
  - IMMUTABLE: chỉ append, không sửa/xóa
  - STRUCTURED: JSON Lines format (mỗi dòng là một JSON object)
  - TIMESTAMPED: mọi event đều có ISO 8601 timestamp
  - TRACEABLE: có correlation ID để track một flow end-to-end

So sánh với .NET:
-----------------
  // .NET Serilog (structured logging):
  Log.ForContext("TaskId", taskId)
     .Information("Migration started {@Tables} strategy={Strategy}", tables, strategy);

  # Python equivalent (module này):
  audit = MigrationAuditLog()
  audit.log_migration_start(task_id="abc", tables=tables, strategy="upsert", ...)

Compliance:
-----------
Audit Trail là yêu cầu bắt buộc trong các tiêu chuẩn:
  - ISO 27001 (Information Security Management)
  - PCI DSS (Payment Card Industry)
  - GDPR Article 30 (Records of Processing Activities)
  - SOC 2 Type II

Tham khảo:
----------
  - OWASP Logging Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["MigrationAuditLog", "AuditEvent"]

logger = logging.getLogger(__name__)

# Mặc định ghi log vào thư mục logs/ cùng cấp với dự án
_DEFAULT_AUDIT_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "audit.jsonl")


class AuditEvent:
    """
    Event type constants cho Audit Log.

    Sử dụng constants thay vì string literals để:
      1. Tránh typo (type-safe)
      2. IDE auto-complete
      3. Refactoring dễ dàng
    """
    # Migration lifecycle
    MIGRATION_START = "migration.start"
    MIGRATION_COMPLETE = "migration.complete"
    MIGRATION_FAILED = "migration.failed"
    MIGRATION_PROGRESS = "migration.progress"

    # API events
    API_REQUEST = "api.request"
    API_RESPONSE = "api.response"

    # Security events
    SECURITY_VALIDATION_FAILED = "security.validation_failed"
    SECURITY_RATE_LIMITED = "security.rate_limited"
    SECURITY_INVALID_CONFIG = "security.invalid_config"

    # System events
    SYSTEM_CONFIG_LOADED = "system.config_loaded"
    SYSTEM_SCHEMA_DISCOVERED = "system.schema_discovered"


class MigrationAuditLog:
    """
    Ghi nhật ký migration theo chuẩn enterprise — IMMUTABLE, STRUCTURED, THREADSAFE.

    Format output (JSON Lines / NDJSON):
    ------------------------------------
    Mỗi dòng trong file audit.jsonl là một JSON object độc lập:

    .. code-block:: json

        {"timestamp": "2026-06-21T08:00:00Z", "event": "migration.start",
         "task_id": "abc-123", "tables": ["users"], "strategy": "upsert",
         "source": "mysql:localhost:3306/db", "target": "pg:localhost:5432/db.public"}
        {"timestamp": "2026-06-21T08:00:15Z", "event": "migration.complete",
         "task_id": "abc-123", "duration_seconds": 15.3, "stats": {"success": 1000}}

    Tại sao JSON Lines (NDJSON) thay vì JSON array?
    ------------------------------------------------
    - Append-only: chỉ cần ``f.write(json_line + "\\n")`` — không cần lock toàn file
    - Streaming: có thể đọc từng dòng mà không cần load cả file vào memory
    - Fault-tolerant: nếu crash giữa chừng, các dòng trước vẫn valid
    - Compatible với: ``jq``, Elasticsearch, Splunk, Datadog

    Usage:
    ------
    .. code-block:: python

        audit = MigrationAuditLog()  # ghi vào logs/audit.jsonl
        audit.log_migration_start(task_id="abc", tables=["users"], strategy="upsert",
                                  source="mysql:localhost", target="pg:localhost")
        # ... migration runs ...
        audit.log_migration_complete(task_id="abc", stats=stats, duration_secs=15.3)
    """

    def __init__(self, log_path: str | None = None, echo_to_stdout: bool = True) -> None:
        """
        Khởi tạo audit logger.

        Args:
            log_path       : Đường dẫn file audit.jsonl. Mặc định: logs/audit.jsonl
            echo_to_stdout : Nếu True, in ra console bên cạnh ghi file
        """
        self._log_path = Path(log_path or _DEFAULT_AUDIT_PATH).resolve()
        self._echo = echo_to_stdout
        self._lock = threading.Lock()  # Thread-safe file writes

        # Tạo thư mục nếu chưa có
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Migration Lifecycle Events
    # ------------------------------------------------------------------

    def log_migration_start(
        self,
        task_id: str,
        tables: list[str],
        strategy: str,
        source: str,
        target: str,
        extra: dict | None = None,
    ) -> None:
        """
        Ghi sự kiện bắt đầu migration.

        Args:
            task_id  : UUID của task (để correlate với các events khác)
            tables   : Danh sách bảng cần migrate
            strategy : Chiến lược (truncate_insert / upsert / append)
            source   : Mô tả nguồn (ví dụ: "mysql:localhost:3306/mydb")
            target   : Mô tả đích (ví dụ: "pg:localhost:5432/targetdb.public")
            extra    : Thông tin bổ sung tùy chỉnh
        """
        self._write({
            "event": AuditEvent.MIGRATION_START,
            "task_id": task_id,
            "tables": tables,
            "table_count": len(tables),
            "strategy": strategy,
            "source": source,
            "target": target,
            **(extra or {}),
        })

    def log_migration_complete(
        self,
        task_id: str,
        stats: dict[str, Any],
        duration_secs: float,
        extra: dict | None = None,
    ) -> None:
        """
        Ghi sự kiện hoàn thành migration.

        Args:
            task_id      : UUID của task
            stats        : Thống kê chi tiết (success/errors/skipped per table)
            duration_secs: Thời gian chạy tính bằng giây
            extra        : Thông tin bổ sung
        """
        total_success = sum(v.get("success", 0) for v in stats.values() if isinstance(v, dict))
        total_errors = sum(v.get("errors", 0) for v in stats.values() if isinstance(v, dict))

        self._write({
            "event": AuditEvent.MIGRATION_COMPLETE,
            "task_id": task_id,
            "duration_seconds": round(duration_secs, 3),
            "total_rows_migrated": total_success,
            "total_errors": total_errors,
            "tables_stats": stats,
            **(extra or {}),
        })

    def log_migration_failed(
        self,
        task_id: str,
        error: str,
        duration_secs: float | None = None,
        extra: dict | None = None,
    ) -> None:
        """
        Ghi sự kiện migration thất bại.

        Args:
            task_id      : UUID của task
            error        : Thông báo lỗi (KHÔNG bao gồm password hay sensitive data)
            duration_secs: Thời gian đã chạy trước khi lỗi (nếu biết)
            extra        : Thông tin bổ sung
        """
        self._write({
            "event": AuditEvent.MIGRATION_FAILED,
            "task_id": task_id,
            "error": _sanitize_log_value(error),
            "duration_seconds": round(duration_secs, 3) if duration_secs else None,
            **(extra or {}),
        })

    # ------------------------------------------------------------------
    # API & Security Events
    # ------------------------------------------------------------------

    def log_api_request(
        self,
        method: str,
        path: str,
        remote_addr: str,
        payload_keys: list[str] | None = None,
    ) -> str:
        """
        Ghi sự kiện API request.

        Lưu ý: KHÔNG bao giờ log toàn bộ request body vì có thể chứa credentials.
        Chỉ log method, path, và danh sách keys (không log values).

        Args:
            method       : HTTP method (GET/POST/...)
            path         : URL path
            remote_addr  : IP của client
            payload_keys : Danh sách key trong request body (không phải values)

        Returns:
            request_id : UUID để correlate request với response
        """
        request_id = str(uuid.uuid4())[:8]
        self._write({
            "event": AuditEvent.API_REQUEST,
            "request_id": request_id,
            "method": method,
            "path": path,
            "remote_addr": remote_addr,
            "payload_keys": payload_keys or [],
        })
        return request_id

    def log_security_event(
        self,
        event_type: str,
        details: str,
        severity: str = "WARNING",
        remote_addr: str | None = None,
    ) -> None:
        """
        Ghi sự kiện bảo mật.

        Dùng khi:
          - Phát hiện tên bảng/cột chứa ký tự bất hợp lệ (SQL Injection attempt)
          - Rate limit bị vượt
          - Config có giá trị không hợp lệ

        Args:
            event_type  : Loại sự kiện (dùng AuditEvent.SECURITY_* constants)
            details     : Mô tả chi tiết (không log sensitive data!)
            severity    : INFO / WARNING / ERROR / CRITICAL
            remote_addr : IP của client (nếu là HTTP request)
        """
        self._write({
            "event": event_type,
            "severity": severity,
            "details": _sanitize_log_value(details),
            "remote_addr": remote_addr,
        })

    def log_event(self, event_type: str, details: dict | None = None) -> None:
        """
        Generic event logger cho các trường hợp khác.

        Args:
            event_type : Loại sự kiện (dùng AuditEvent.* constants)
            details    : Dict với các thông tin bổ sung
        """
        self._write({
            "event": event_type,
            **(details or {}),
        })

    # ------------------------------------------------------------------
    # Query & Analysis
    # ------------------------------------------------------------------

    def get_recent_events(self, limit: int = 50) -> list[dict]:
        """
        Đọc N event gần nhất từ audit log.

        Args:
            limit : Số event tối đa cần lấy

        Returns:
            Danh sách dict, mỗi phần tử là một audit event
        """
        if not self._log_path.exists():
            return []

        events = []
        try:
            with open(self._log_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass  # Skip malformed lines
        except OSError:
            pass

        return events[-limit:]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _write(self, event_data: dict) -> None:
        """
        Ghi một event vào file audit.jsonl (thread-safe, append-only).

        Mọi event đều được tự động bổ sung:
          - timestamp: ISO 8601 UTC
          - log_id   : UUID để uniquely identify event
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "log_id": str(uuid.uuid4())[:8],
            **event_data,
        }

        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))

        with self._lock:
            try:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError as e:
                # Không raise lỗi vì audit failure không nên crash ứng dụng
                logger.error("Failed to write audit log: %s", e)

        if self._echo:
            logger.info("[AUDIT] %s", line)


def _sanitize_log_value(value: str) -> str:
    """
    Loại bỏ thông tin nhạy cảm khỏi log values.

    Ngăn chặn credential leakage trong log files — một lỗ hổng bảo mật
    phổ biến theo OWASP A09:2021 Security Logging and Monitoring Failures.

    Patterns bị che:
      - password=xxx    → password=***
      - key=xxx         → key=***
      - token=xxx       → token=***
      - secret=xxx      → secret=***
    """
    import re
    return re.sub(
        r"(password|passwd|pwd|key|token|secret|credential)[=:\s]+\S+",
        r"\1=***",
        value,
        flags=re.IGNORECASE,
    )


# Module-level singleton (optional convenience)
_default_audit: MigrationAuditLog | None = None


def get_default_audit() -> MigrationAuditLog:
    """Lấy instance audit log mặc định (singleton pattern)."""
    global _default_audit  # noqa: PLW0603
    if _default_audit is None:
        _default_audit = MigrationAuditLog()
    return _default_audit
