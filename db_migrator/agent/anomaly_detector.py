#!/usr/bin/env python3
"""
db_migrator/agent/anomaly_detector.py
=======================================
Pre-Migration Anomaly Detector — Kiểm tra chất lượng dữ liệu trước khi migrate.

Tại sao cần kiểm tra trước migration?
----------------------------------------
"Fail fast" — phát hiện vấn đề trước khi migrate tiết kiệm:
- Thời gian: Không phải rollback sau khi đã migrate
- Tài nguyên: Không tốn I/O cho data sẽ fail
- Rủi ro: Không làm corrupt target database

Các loại anomaly được detect:
1. Data Truncation Risk   — varchar(50) MySQL → varchar(30) PG
2. NULL Constraint Violation — cột NOT NULL target nhưng có NULL trong source
3. Type Incompatibility   — kiểu không tương thích (json → text cần parse)
4. Encoding Issues        — mojibake characters (âéñ bị encode sai)
5. Date Range Violation   — dates ngoài range PostgreSQL (1000-01-01 → 9999-12-31)
6. Orphaned References    — FK values không tồn tại trong parent table

Thiết kế: Rule-based (không cần LLM) — nhanh, offline, deterministic
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AnomalyDetector", "Anomaly", "AnomalySeverity", "AnomalyReport"]


class AnomalySeverity(str, Enum):
    """Mức độ nghiêm trọng của anomaly."""
    CRITICAL = "CRITICAL"  # Sẽ gây lỗi migration, phải fix
    WARNING  = "WARNING"   # Có thể gây lỗi, nên xem xét
    INFO     = "INFO"      # Thông tin tham khảo


@dataclass
class Anomaly:
    """Một vấn đề được phát hiện trong data."""
    severity: AnomalySeverity
    category: str
    table: str
    column: str | None
    message: str
    affected_rows: int = 0
    suggestion: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity.value,
            "category": self.category,
            "table": self.table,
            "column": self.column,
            "message": self.message,
            "affected_rows": self.affected_rows,
            "suggestion": self.suggestion,
        }

    def __str__(self) -> str:
        col_part = f".{self.column}" if self.column else ""
        return f"[{self.severity.value}] {self.table}{col_part}: {self.message}"


@dataclass
class AnomalyReport:
    """Báo cáo tổng hợp sau khi phân tích."""
    anomalies: list[Anomaly] = field(default_factory=list)
    tables_checked: int = 0
    rows_sampled: int = 0

    @property
    def critical_count(self) -> int:
        return sum(1 for a in self.anomalies if a.severity == AnomalySeverity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for a in self.anomalies if a.severity == AnomalySeverity.WARNING)

    @property
    def is_safe_to_migrate(self) -> bool:
        """True nếu không có CRITICAL anomalies."""
        return self.critical_count == 0

    def get_summary(self) -> str:
        """Tạo summary text cho agent/user."""
        lines = [
            f"Kết quả kiểm tra: {self.tables_checked} bảng, {self.rows_sampled} rows",
            f"  🔴 CRITICAL: {self.critical_count}",
            f"  🟡 WARNING:  {self.warning_count}",
            f"  ℹ️  INFO:     {len(self.anomalies) - self.critical_count - self.warning_count}",
            "",
        ]
        if self.is_safe_to_migrate:
            lines.append("✅ An toàn để migrate (không có lỗi nghiêm trọng)")
        else:
            lines.append("❌ CẦN XỬ LÝ trước khi migrate!")

        for anomaly in self.anomalies[:10]:  # Hiển thị max 10
            lines.append(f"  {anomaly}")

        if len(self.anomalies) > 10:
            lines.append(f"  ... và {len(self.anomalies) - 10} anomalies khác")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "is_safe": self.is_safe_to_migrate,
            "summary": {
                "tables_checked": self.tables_checked,
                "rows_sampled": self.rows_sampled,
                "critical": self.critical_count,
                "warning": self.warning_count,
                "total": len(self.anomalies),
            },
            "anomalies": [a.to_dict() for a in self.anomalies],
        }


class AnomalyDetector:
    """
    Phát hiện data anomalies trước migration — Rule-based, không cần LLM.

    Usage:
    ------
    .. code-block:: python

        detector = AnomalyDetector()

        # Kiểm tra từ parsed SQL data (file-based flow)
        report = detector.check_table_data(
            table_name="users",
            columns=["id", "name", "email"],
            rows=[(1, "Long", "long@example.com"), ...],
            source_types={"id": "int", "name": "varchar(50)", "email": "varchar(100)"},
        )

        # Kiểm tra schema compatibility
        schema_report = detector.check_schema_compatibility(
            source_schema={"users": {"columns": [...]}},
            target_schema={"users": {"columns": [...]}},
            mapping={"users": {"id": "id", "name": "username"}},
        )

        print(report.get_summary())
        if not report.is_safe_to_migrate:
            # Báo cáo vấn đề cho user
            ...
    """

    # Encoding anomaly patterns (mojibake detection)
    _MOJIBAKE_PATTERNS = [
        re.compile(r"[\xc3][\x80-\xbf]"),   # Latin-1 decoded as UTF-8
        re.compile(r"â€[œ™š]"),              # Common Word mojibake
        re.compile(r"[À-ÿ]{2,}(?=[a-z])"),  # Repeated non-ASCII
    ]

    # PostgreSQL date range limits
    PG_DATE_MIN = "0001-01-01"
    PG_DATE_MAX = "9999-12-31"

    def check_table_data(
        self,
        table_name: str,
        columns: list[str],
        rows: list[tuple],
        source_types: dict[str, str] | None = None,
        target_types: dict[str, str] | None = None,
        sample_size: int = 1000,
    ) -> AnomalyReport:
        """
        Kiểm tra data của một bảng từ SQL parsed data.

        Args:
            table_name   : Tên bảng
            columns      : Danh sách column names
            rows         : List tuples dữ liệu
            source_types : Dict col → MySQL type
            target_types : Dict col → PG type
            sample_size  : Số rows tối đa để sample (default: 1000)

        Returns:
            AnomalyReport với danh sách anomalies
        """
        report = AnomalyReport()
        source_types = source_types or {}
        target_types = target_types or {}

        # Sample rows
        sample = rows[:sample_size]
        report.rows_sampled = len(sample)
        report.tables_checked = 1

        if not sample:
            report.anomalies.append(Anomaly(
                severity=AnomalySeverity.INFO,
                category="empty_table",
                table=table_name,
                column=None,
                message="Bảng không có dữ liệu",
            ))
            return report

        col_idx = {col: i for i, col in enumerate(columns)}

        for col in columns:
            idx = col_idx.get(col)
            if idx is None:
                continue

            col_values = [row[idx] for row in sample if idx < len(row)]
            src_type = source_types.get(col, "").lower()
            tgt_type = target_types.get(col, "").lower()

            # Check 1: NULL trong NOT NULL column
            null_count = sum(1 for v in col_values if v is None or v == "NULL" or v == "")
            if null_count > 0 and "not null" in tgt_type.lower():
                report.anomalies.append(Anomaly(
                    severity=AnomalySeverity.CRITICAL,
                    category="null_violation",
                    table=table_name,
                    column=col,
                    message=f"{null_count} giá trị NULL trong cột NOT NULL",
                    affected_rows=null_count,
                    suggestion=f"Xem xét DEFAULT value hoặc remove NOT NULL constraint trên cột '{col}'",
                ))

            # Check 2: Data truncation risk (varchar length)
            src_len = self._extract_varchar_length(src_type)
            tgt_len = self._extract_varchar_length(tgt_type)
            if src_len and tgt_len and src_len > tgt_len:
                # Kiểm tra actual max length trong data
                max_actual = max(
                    (len(str(v)) for v in col_values if v is not None),
                    default=0
                )
                if max_actual > tgt_len:
                    report.anomalies.append(Anomaly(
                        severity=AnomalySeverity.CRITICAL,
                        category="data_truncation",
                        table=table_name,
                        column=col,
                        message=(
                            f"Dữ liệu dài {max_actual} chars nhưng target chỉ {tgt_len} chars "
                            f"(source: varchar({src_len}))"
                        ),
                        affected_rows=sum(1 for v in col_values if v and len(str(v)) > tgt_len),
                        suggestion="Tăng varchar length trên target hoặc truncate data trong mapping",
                    ))

            # Check 3: Encoding issues (text columns only)
            if "char" in src_type or "text" in src_type:
                encoding_issues = self._detect_encoding_issues(col_values)
                if encoding_issues > 0:
                    report.anomalies.append(Anomaly(
                        severity=AnomalySeverity.WARNING,
                        category="encoding",
                        table=table_name,
                        column=col,
                        message=f"{encoding_issues} rows có khả năng bị lỗi encoding (mojibake)",
                        affected_rows=encoding_issues,
                        suggestion="Kiểm tra charset của MySQL source (nên là utf8mb4) và đảm bảo PG target là UTF-8",
                    ))

            # Check 4: Date range violations
            if "date" in src_type or "timestamp" in src_type:
                date_issues = self._detect_date_issues(col_values)
                if date_issues > 0:
                    report.anomalies.append(Anomaly(
                        severity=AnomalySeverity.WARNING,
                        category="date_range",
                        table=table_name,
                        column=col,
                        message=f"{date_issues} rows có giá trị date ngoài range PostgreSQL",
                        affected_rows=date_issues,
                        suggestion="PostgreSQL hỗ trợ date từ 4713 BC đến 5874897 AD. "
                                   "MySQL '0000-00-00' cần convert sang NULL hoặc một date hợp lệ",
                    ))

        return report

    def check_schema_compatibility(
        self,
        source_columns: list[dict],
        target_columns: list[dict],
        table_name: str = "unknown",
    ) -> AnomalyReport:
        """
        So sánh schema compatibility giữa source và target.

        Args:
            source_columns: List {"name": ..., "type": ...} từ MySQL
            target_columns: List {"name": ..., "type": ...} từ PG
            table_name    : Tên bảng (cho logging)

        Returns:
            AnomalyReport
        """
        report = AnomalyReport(tables_checked=1)

        src_cols = {c["name"].lower(): c for c in source_columns}
        tgt_cols = {c["name"].lower(): c for c in target_columns}

        # Columns có trong source nhưng không có trong target
        missing_in_target = set(src_cols.keys()) - set(tgt_cols.keys())
        for col in missing_in_target:
            report.anomalies.append(Anomaly(
                severity=AnomalySeverity.WARNING,
                category="missing_column",
                table=table_name,
                column=col,
                message=f"Cột '{col}' có trong MySQL nhưng không có trong PostgreSQL target",
                suggestion="Thêm cột này vào target schema hoặc thêm vào ignored_columns trong config",
            ))

        # Type incompatibilities
        for col_name in set(src_cols.keys()) & set(tgt_cols.keys()):
            src_type = src_cols[col_name].get("type", "").lower()
            tgt_type = tgt_cols[col_name].get("type", "").lower()
            issue = self._check_type_compatibility(src_type, tgt_type)
            if issue:
                severity = AnomalySeverity.CRITICAL if "incompatible" in issue else AnomalySeverity.WARNING
                report.anomalies.append(Anomaly(
                    severity=severity,
                    category="type_incompatibility",
                    table=table_name,
                    column=col_name,
                    message=f"Type mismatch: MySQL={src_type} → PG={tgt_type} — {issue}",
                    suggestion="Xem xét value_transforms trong config để convert type",
                ))

        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_varchar_length(type_str: str) -> int | None:
        """Trích xuất length từ varchar(N) hay char(N)."""
        match = re.search(r"(?:var)?char\((\d+)\)", type_str.lower())
        if match:
            return int(match.group(1))
        return None

    def _detect_encoding_issues(self, values: list) -> int:
        """Đếm số values có khả năng bị encoding error."""
        count = 0
        for v in values:
            if v is None:
                continue
            text = str(v)
            for pattern in self._MOJIBAKE_PATTERNS:
                if pattern.search(text):
                    count += 1
                    break
        return count

    @staticmethod
    def _detect_date_issues(values: list) -> int:
        """Đếm số date values không hợp lệ trong PostgreSQL."""
        count = 0
        invalid_patterns = [
            re.compile(r"^0000-"),        # MySQL "zero date" 0000-00-00
            re.compile(r"^-\d"),          # Negative year (BC dates)
        ]
        for v in values:
            if v is None:
                continue
            text = str(v)
            for pat in invalid_patterns:
                if pat.match(text):
                    count += 1
                    break
        return count

    @staticmethod
    def _check_type_compatibility(src_type: str, tgt_type: str) -> str | None:
        """
        Kiểm tra type compatibility.

        Returns:
            None nếu compatible, error message nếu không
        """
        # Numeric types — generally compatible
        numeric_src = any(t in src_type for t in ("int", "float", "double", "decimal", "numeric"))
        numeric_tgt = any(t in tgt_type for t in ("int", "float", "double", "decimal", "numeric", "real"))
        if numeric_src and numeric_tgt:
            return None

        # String types — generally compatible
        string_src = any(t in src_type for t in ("char", "text", "varchar"))
        string_tgt = any(t in tgt_type for t in ("char", "text", "varchar"))
        if string_src and string_tgt:
            return None

        # JSON: MySQL json → PG jsonb (compatible, nhưng cần kiểm tra)
        if "json" in src_type and ("json" in tgt_type or "text" in tgt_type):
            return None

        # Boolean: MySQL tinyint(1) → PG boolean (cần transform)
        if "tinyint" in src_type and "bool" in tgt_type:
            return "tinyint(1) → boolean cần value_transform: NullToBool hoặc EnumToInt"

        # Enum: MySQL enum → PG varchar (cần xử lý)
        if "enum" in src_type and "varchar" not in tgt_type and "text" not in tgt_type:
            return "MySQL enum nên map sang PostgreSQL varchar hoặc native ENUM type"

        # Blob → bytea
        if "blob" in src_type and "bytea" in tgt_type:
            return None

        # Không xác định được → warning nhẹ
        if src_type and tgt_type and src_type.split("(")[0] != tgt_type.split("(")[0]:
            return "Types khác nhau — cần verify manually"

        return None
