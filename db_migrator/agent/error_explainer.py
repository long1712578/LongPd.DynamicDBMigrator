#!/usr/bin/env python3
"""
db_migrator/agent/error_explainer.py
======================================
Migration Error Explainer — Giải thích lỗi migration bằng ngôn ngữ tự nhiên.

Khi migration fail, thông báo lỗi thường khó đọc:
    ERROR: insert or update on table "orders" violates foreign key constraint
    "orders_user_id_fkey" on table "orders"
    DETAIL: Key (user_id)=(12345) is not present in table "users".

Module này:
1. Parse và phân loại lỗi PostgreSQL
2. Cung cấp explanation dễ hiểu (tiếng Việt)
3. Đề xuất remediation steps cụ thể
4. (Optional) Dùng LLM để giải thích phức tạp hơn

Error categories:
    - FK Violation      : Foreign key constraint failed
    - NOT NULL          : Inserting NULL vào NOT NULL column
    - Type Mismatch     : Wrong data type
    - Unique Violation  : Duplicate key
    - Overflow          : Value too large for column
    - Encoding          : Invalid encoding/charset
    - Permission        : Access denied
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

__all__ = ["ErrorExplainer", "ExplainedError", "ErrorCategory"]


class ErrorCategory(str, Enum):
    FOREIGN_KEY = "foreign_key"
    NOT_NULL = "not_null"
    UNIQUE = "unique"
    TYPE_MISMATCH = "type_mismatch"
    OVERFLOW = "overflow"
    ENCODING = "encoding"
    PERMISSION = "permission"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    UNKNOWN = "unknown"


@dataclass
class ExplainedError:
    """Lỗi migration đã được phân tích và giải thích."""
    original_error: str
    category: ErrorCategory
    title: str
    explanation: str
    table: str | None = None
    column: str | None = None
    value: str | None = None
    remediation_steps: list[str] = field(default_factory=list)
    config_fix: str | None = None   # Gợi ý thay đổi mapping_config.json
    ai_explanation: str | None = None  # Từ LLM (optional)

    def to_dict(self) -> dict:
        return {
            "category": self.category.value,
            "title": self.title,
            "explanation": self.explanation,
            "table": self.table,
            "column": self.column,
            "remediation": self.remediation_steps,
            "config_fix": self.config_fix,
            "ai_explanation": self.ai_explanation,
        }

    def format_for_user(self) -> str:
        """Format readable message cho user."""
        parts = [
            f"❌ **{self.title}**",
            f"\n📋 **Nguyên nhân**: {self.explanation}",
        ]
        if self.table:
            parts.append(f"📊 **Bảng**: {self.table}")
        if self.column:
            parts.append(f"🔑 **Cột**: {self.column}")
        if self.remediation_steps:
            parts.append("\n🔧 **Cách xử lý**:")
            for i, step in enumerate(self.remediation_steps, 1):
                parts.append(f"  {i}. {step}")
        if self.config_fix:
            parts.append(f"\n⚙️ **Cấu hình**: {self.config_fix}")
        if self.ai_explanation:
            parts.append(f"\n🤖 **AI Analysis**: {self.ai_explanation}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Error pattern matchers
# ---------------------------------------------------------------------------

@dataclass
class _ErrorPattern:
    """Một pattern để match và extract thông tin từ error message."""
    pattern: re.Pattern
    category: ErrorCategory
    title: str
    explanation_template: str
    remediation: list[str]
    config_fix: str | None = None

    def match(self, error_text: str) -> re.Match | None:
        return self.pattern.search(error_text, re.IGNORECASE)


_PATTERNS: list[_ErrorPattern] = [
    # Foreign Key Violation
    _ErrorPattern(
        pattern=re.compile(
            r"(?:violates foreign key constraint|foreign key violation)"
            r'.*?["\`](\w+)["\`].*?Key \((\w+)\)=\(([^)]+)\)',
            re.IGNORECASE | re.DOTALL,
        ),
        category=ErrorCategory.FOREIGN_KEY,
        title="Vi phạm Foreign Key Constraint",
        explanation_template=(
            "Giá trị '{value}' trong cột '{column}' không tồn tại trong bảng tham chiếu. "
            "Migration đang cố gắng insert một record có foreign key chỉ đến record chưa được migrate."
        ),
        remediation=[
            "Đảm bảo bảng cha (parent table) được migrate TRƯỚC bảng con (child table)",
            "Kiểm tra thứ tự các bảng trong migration config",
            "Hoặc tạm thời disable FK constraints: SET session_replication_role = replica;",
            "Sau khi migrate xong tất cả: SET session_replication_role = DEFAULT;",
        ],
        config_fix='Thêm "migration_order": ["parent_table", "child_table"] vào config',
    ),

    # NOT NULL Violation
    _ErrorPattern(
        pattern=re.compile(
            r'null value in column["\s]+(["\`]?\w+["\`]?)["\s]+.*?violates not-null constraint',
            re.IGNORECASE,
        ),
        category=ErrorCategory.NOT_NULL,
        title="Vi phạm NOT NULL Constraint",
        explanation_template=(
            "Cột '{column}' trong target PostgreSQL có ràng buộc NOT NULL, "
            "nhưng dữ liệu nguồn MySQL có giá trị NULL."
        ),
        remediation=[
            "Thêm DEFAULT value cho cột trong PostgreSQL: ALTER TABLE ... ALTER COLUMN ... SET DEFAULT '...'",
            "Hoặc thêm value transform trong config để replace NULL với giá trị mặc định",
            "Hoặc đổi constraint thành NULLABLE nếu business logic cho phép",
        ],
        config_fix=(
            'Thêm "value_transforms": {"table.column": {"type": "NullToBool", "null_value": false}} '
            'vào mapping config'
        ),
    ),

    # Unique Constraint Violation
    _ErrorPattern(
        pattern=re.compile(
            r"duplicate key value violates unique constraint.*?"
            r'Key \(([^)]+)\)=\(([^)]+)\) already exists',
            re.IGNORECASE | re.DOTALL,
        ),
        category=ErrorCategory.UNIQUE,
        title="Vi phạm Unique Constraint",
        explanation_template=(
            "Giá trị '{value}' trong cột '{column}' đã tồn tại trong target database. "
            "Đây thường xảy ra khi dùng strategy 'append' mà data đã được migrate trước đó."
        ),
        remediation=[
            "Đổi migration strategy sang 'upsert' thay vì 'append'",
            "Hoặc dùng 'truncate_insert' để xóa data cũ trước khi insert",
            "Hoặc kiểm tra và remove duplicate data từ nguồn",
        ],
        config_fix='Thêm "strategy": "upsert" vào table mapping trong config',
    ),

    # Type Mismatch
    _ErrorPattern(
        pattern=re.compile(
            r"invalid input syntax for (?:type )?(\w+).*?['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        ),
        category=ErrorCategory.TYPE_MISMATCH,
        title="Lỗi kiểu dữ liệu không hợp lệ",
        explanation_template=(
            "Giá trị '{value}' không thể convert sang kiểu dữ liệu PostgreSQL '{column}'. "
            "MySQL và PostgreSQL có cách xử lý một số kiểu dữ liệu khác nhau."
        ),
        remediation=[
            "Thêm value_transform để convert giá trị trước khi insert",
            "Ví dụ: MySQL tinyint(1) → PostgreSQL boolean cần EnumToInt transform",
            "Ví dụ: MySQL datetime '0000-00-00' → PostgreSQL cần convert sang NULL",
        ],
        config_fix='Thêm "value_transforms" với type phù hợp trong mapping config',
    ),

    # Numeric Overflow
    _ErrorPattern(
        pattern=re.compile(
            r"(?:numeric field overflow|value too long for type|integer out of range)",
            re.IGNORECASE,
        ),
        category=ErrorCategory.OVERFLOW,
        title="Giá trị vượt quá giới hạn",
        explanation_template=(
            "Giá trị quá lớn so với kiểu dữ liệu của cột trong target PostgreSQL. "
            "Ví dụ: varchar(50) không thể chứa string dài 100 ký tự."
        ),
        remediation=[
            "Tăng độ dài của cột trong target PostgreSQL (ALTER TABLE ... ALTER COLUMN ... TYPE varchar(200))",
            "Hoặc thêm transform để truncate giá trị trong config",
            "Kiểm tra AnomalyDetector để phát hiện sớm vấn đề trước khi migrate",
        ],
        config_fix=None,
    ),

    # Connection Error
    _ErrorPattern(
        pattern=re.compile(
            r"(?:could not connect|connection refused|connection timed out|server closed the connection)",
            re.IGNORECASE,
        ),
        category=ErrorCategory.CONNECTION,
        title="Lỗi kết nối Database",
        explanation_template=(
            "Không thể kết nối đến database. "
            "Có thể server chưa chạy hoặc thông tin kết nối sai."
        ),
        remediation=[
            "Kiểm tra database server đang chạy: pg_ctl status",
            "Xác minh host, port, username, password trong config",
            "Kiểm tra firewall/network có block connection không",
            "Thử kết nối trực tiếp: psql -h host -U user -d database",
        ],
        config_fix='Kiểm tra "pg_config" trong mapping_config.json hoặc PG_HOST env variable',
    ),

    # Permission Error
    _ErrorPattern(
        pattern=re.compile(
            r"permission denied for (?:table|relation|schema) (\w+)",
            re.IGNORECASE,
        ),
        category=ErrorCategory.PERMISSION,
        title="Lỗi không đủ quyền truy cập",
        explanation_template=(
            "Database user không có quyền thực hiện thao tác trên bảng '{column}'. "
        ),
        remediation=[
            "Grant quyền cho user: GRANT INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO username;",
            "Hoặc dùng superuser account cho migration",
        ],
        config_fix='Kiểm tra PG_USER trong config có đủ quyền không',
    ),
]


class ErrorExplainer:
    """
    Phân tích và giải thích lỗi migration.

    Kết hợp rule-based pattern matching (nhanh, offline) với
    optional LLM enhancement cho lỗi phức tạp.

    Usage:
    ------
    .. code-block:: python

        explainer = ErrorExplainer(llm_provider=GeminiProvider())

        try:
            migrator.migrate_mysql_to_postgres(...)
        except Exception as e:  # noqa: BLE001
            explained = explainer.explain(str(e))
            print(explained.format_for_user())
    """

    def __init__(self, llm_provider=None) -> None:
        """
        Args:
            llm_provider: LLMProvider (optional). Nếu None, chỉ rule-based.
        """
        self._llm = llm_provider

    def explain(self, error_text: str, table: str | None = None) -> ExplainedError:
        """
        Phân tích error message và trả về explanation.

        Args:
            error_text: Raw error message từ exception
            table     : Tên bảng đang migrate (hint cho LLM)

        Returns:
            ExplainedError với explanation và remediation steps
        """
        error_text = str(error_text)

        # Thử từng pattern
        for pattern in _PATTERNS:
            m = pattern.match(error_text)
            if m:
                # Extract dynamic values từ regex groups
                groups = m.groups()
                column = groups[0] if groups else None
                value = groups[1] if len(groups) > 1 else None

                explanation = pattern.explanation_template.format(
                    column=column or "?",
                    value=value or "?",
                    table=table or "?",
                )

                explained = ExplainedError(
                    original_error=error_text,
                    category=pattern.category,
                    title=pattern.title,
                    explanation=explanation,
                    table=table,
                    column=column,
                    value=value,
                    remediation_steps=pattern.remediation,
                    config_fix=pattern.config_fix,
                )

                # Enhance với LLM nếu có
                if self._llm:
                    explained.ai_explanation = self._get_ai_explanation(error_text, explained, table)

                return explained

        # Không match pattern nào → unknown error
        explained = ExplainedError(
            original_error=error_text,
            category=ErrorCategory.UNKNOWN,
            title="Lỗi Migration Không Xác Định",
            explanation=f"Đây là lỗi chưa được phân loại: {error_text[:200]}",
            table=table,
            remediation_steps=[
                "Xem log chi tiết trong file audit.jsonl",
                "Thử chạy lại với strategy='truncate_insert' để bắt đầu từ đầu",
                "Kiểm tra dữ liệu nguồn có hợp lệ không",
                "Liên hệ support hoặc mở issue trên GitHub",
            ],
        )

        if self._llm:
            explained.ai_explanation = self._get_ai_explanation(error_text, explained, table)

        return explained

    def explain_batch(self, errors: list[str], table: str | None = None) -> list[ExplainedError]:
        """
        Phân tích nhiều lỗi cùng lúc.

        Args:
            errors: List error messages
            table : Tên bảng (chung cho tất cả lỗi)

        Returns:
            List ExplainedError (cùng thứ tự với input)
        """
        return [self.explain(e, table) for e in errors]

    def _get_ai_explanation(
        self,
        error_text: str,
        explained: ExplainedError,
        table: str | None,
    ) -> str | None:
        """Dùng LLM để tạo explanation phong phú hơn."""
        if not self._llm:
            return None

        prompt = f"""Phân tích lỗi migration MySQL→PostgreSQL sau:

Lỗi gốc: {error_text[:500]}
Bảng: {table or 'unknown'}
Đã phân loại là: {explained.category.value}

Hãy giải thích ngắn gọn (2-3 câu tiếng Việt) tại sao lỗi xảy ra và hướng khắc phục cụ thể nhất.
Tập trung vào nguyên nhân kỹ thuật thực sự, không lặp lại thông tin đã có."""

        try:
            response = self._llm.complete(prompt)
            return response.content.strip()
        except Exception as e:
            logger.debug("AI explanation failed: %s", e)
            return None
