#!/usr/bin/env python3
"""
db_migrator/agent/smart_mapper.py
===================================
AI-Enhanced Schema Mapper — Gợi ý mapping MySQL→PostgreSQL thông minh.

Cách hoạt động:
---------------
1. Rule-based matching (luôn chạy, không cần LLM):
   - Exact match: users → users (100% confidence)
   - Case-insensitive: UserTable → usertable (90%)
   - Common patterns: created_at, updated_at, deleted_at
   - Type compatibility: int → integer, varchar → text

2. AI-enhanced matching (khi có LLM):
   - Semantic similarity: deletedAt → is_deleted (LLM hiểu context)
   - Domain understanding: `factory` là cột đặc thù cần ignored
   - Confidence scoring từ LLM reasoning

Output: MappingSuggestion với confidence score 0.0-1.0
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

__all__ = ["SmartMapper", "MappingSuggestionItem", "SmartMappingResult"]


@dataclass
class MappingSuggestionItem:
    """Gợi ý mapping cho một cặp source → target column."""
    source_col: str
    target_col: str
    confidence: float     # 0.0 = no confidence, 1.0 = certain
    method: str           # "exact", "normalized", "ai", "type_compatible"
    note: str = ""

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= 0.8

    def to_dict(self) -> dict:
        return {
            "source": self.source_col,
            "target": self.target_col,
            "confidence": round(self.confidence, 2),
            "method": self.method,
            "note": self.note,
        }


@dataclass
class SmartMappingResult:
    """Kết quả mapping analysis cho một bảng."""
    source_table: str
    target_table: str
    suggestions: list[MappingSuggestionItem] = field(default_factory=list)
    unmatched_source: list[str] = field(default_factory=list)
    unmatched_target: list[str] = field(default_factory=list)
    ai_explanation: str = ""

    @property
    def high_confidence_count(self) -> int:
        return sum(1 for s in self.suggestions if s.is_high_confidence)

    @property
    def avg_confidence(self) -> float:
        if not self.suggestions:
            return 0.0
        return sum(s.confidence for s in self.suggestions) / len(self.suggestions)

    def to_dict(self) -> dict:
        return {
            "source_table": self.source_table,
            "target_table": self.target_table,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "unmatched_source": self.unmatched_source,
            "unmatched_target": self.unmatched_target,
            "summary": {
                "total_mappings": len(self.suggestions),
                "high_confidence": self.high_confidence_count,
                "avg_confidence": round(self.avg_confidence, 2),
                "unmatched_source_count": len(self.unmatched_source),
                "unmatched_target_count": len(self.unmatched_target),
            },
            "ai_explanation": self.ai_explanation,
        }


class SmartMapper:
    """
    Hybrid mapper: Rule-based + AI-enhanced column mapping.

    Design:
        Luôn chạy rule-based trước (nhanh, deterministic, offline).
        Chỉ gọi LLM cho các column chưa match được (tiết kiệm API calls).

    Usage:
    ------
    .. code-block:: python

        mapper = SmartMapper(llm_provider=GeminiProvider())
        result = mapper.suggest(
            source_table="mysql_users",
            source_cols=["id", "user_name", "deletedAt"],
            target_table="pg_users",
            target_cols=["id", "username", "is_deleted"],
        )
        for s in result.suggestions:
            print(f"{s.source_col} → {s.target_col} ({s.confidence:.0%})")
        # id → id (100%)
        # user_name → username (85%)
        # deletedAt → is_deleted (75% via AI)
    """

    # Common column name normalizations
    _NORMALIZE_MAP = {
        r"[_\-\s]": "",          # Remove separators
        r"([a-z])([A-Z])": r"\1_\2",  # camelCase → snake_case
    }

    # Semantic equivalents (rule-based)
    _SEMANTIC_MAP = {
        frozenset({"deletedat", "deleted_at", "is_deleted", "isdeleted", "softdelete"}): "soft_delete",
        frozenset({"createdat", "created_at", "creation_date", "createtime"}): "created_at",
        frozenset({"updatedat", "updated_at", "modification_date", "modifiedat"}): "updated_at",
        frozenset({"userid", "user_id", "uid"}): "user_id",
        frozenset({"username", "user_name", "login", "loginname"}): "username",
        frozenset({"email", "emailaddress", "email_address"}): "email",
        frozenset({"phone", "phonenumber", "phone_number", "mobile"}): "phone",
    }

    def __init__(self, llm_provider=None) -> None:
        """
        Args:
            llm_provider: LLMProvider instance (optional).
                         Nếu None, chỉ dùng rule-based matching.
        """
        self._llm = llm_provider

    def suggest(
        self,
        source_table: str,
        source_cols: list[str],
        target_table: str,
        target_cols: list[str],
    ) -> SmartMappingResult:
        """
        Gợi ý mapping columns giữa source và target table.

        Thuật toán:
        1. Exact match (case-sensitive)
        2. Normalized match (lowercase, no separators)
        3. Semantic group match
        4. AI match (nếu có LLM) cho remaining unmatched

        Args:
            source_table: Tên bảng nguồn (MySQL)
            source_cols : List column names từ MySQL
            target_table: Tên bảng đích (PostgreSQL)
            target_cols : List column names từ PostgreSQL

        Returns:
            SmartMappingResult với danh sách suggestions
        """
        result = SmartMappingResult(
            source_table=source_table,
            target_table=target_table,
        )

        remaining_source = list(source_cols)
        remaining_target = list(target_cols)
        suggestions: list[MappingSuggestionItem] = []

        # --- Pass 1: Exact match ---
        exact_matches = set(remaining_source) & set(remaining_target)
        for col in exact_matches:
            suggestions.append(MappingSuggestionItem(
                source_col=col, target_col=col,
                confidence=1.0, method="exact",
                note="Tên cột khớp hoàn toàn",
            ))
        remaining_source = [c for c in remaining_source if c not in exact_matches]
        remaining_target = [c for c in remaining_target if c not in exact_matches]

        # --- Pass 2: Normalized match (lowercase, remove separators) ---
        src_norm = {self._normalize(c): c for c in remaining_source}
        tgt_norm = {self._normalize(c): c for c in remaining_target}

        for norm_key in set(src_norm.keys()) & set(tgt_norm.keys()):
            src_col = src_norm[norm_key]
            tgt_col = tgt_norm[norm_key]
            suggestions.append(MappingSuggestionItem(
                source_col=src_col, target_col=tgt_col,
                confidence=0.90, method="normalized",
                note=f"Chuẩn hóa: '{src_col}' ≈ '{tgt_col}'",
            ))
        matched_src_norm = {src_norm[k] for k in set(src_norm.keys()) & set(tgt_norm.keys())}
        matched_tgt_norm = {tgt_norm[k] for k in set(src_norm.keys()) & set(tgt_norm.keys())}
        remaining_source = [c for c in remaining_source if c not in matched_src_norm]
        remaining_target = [c for c in remaining_target if c not in matched_tgt_norm]

        # --- Pass 3: Semantic group match ---
        for src_col in remaining_source[:]:
            matched = self._find_semantic_match(src_col, remaining_target)
            if matched:
                suggestions.append(MappingSuggestionItem(
                    source_col=src_col, target_col=matched,
                    confidence=0.75, method="semantic",
                    note=f"Ngữ nghĩa tương đương: '{src_col}' → '{matched}'",
                ))
                remaining_source.remove(src_col)
                remaining_target.remove(matched)

        # --- Pass 4: AI match (nếu còn unmatched và có LLM) ---
        ai_explanation = ""
        if self._llm and remaining_source and remaining_target:
            ai_matches, ai_explanation = self._ai_match(
                source_table, remaining_source,
                target_table, remaining_target,
            )
            for src_col, tgt_col, confidence in ai_matches:
                suggestions.append(MappingSuggestionItem(
                    source_col=src_col, target_col=tgt_col,
                    confidence=confidence, method="ai",
                    note="Gợi ý bởi AI (cần xem xét)",
                ))
                if src_col in remaining_source:
                    remaining_source.remove(src_col)
                if tgt_col in remaining_target:
                    remaining_target.remove(tgt_col)

        result.suggestions = suggestions
        result.unmatched_source = remaining_source
        result.unmatched_target = remaining_target
        result.ai_explanation = ai_explanation

        logger.info(
            "SmartMapper: %d suggestions for %s → %s (%d unmatched src, %d unmatched tgt)",
            len(suggestions), source_table, target_table,
            len(remaining_source), len(remaining_target),
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(col: str) -> str:
        """Chuẩn hóa tên column để so sánh."""
        # camelCase → snake_case trước
        col = re.sub(r"([a-z])([A-Z])", r"\1_\2", col)
        # Loại bỏ tất cả separator và lowercase
        col = re.sub(r"[_\-\s]", "", col)
        return col.lower()

    def _find_semantic_match(self, src_col: str, target_cols: list[str]) -> str | None:
        """Tìm semantic match từ predefined groups."""
        src_normalized = self._normalize(src_col)

        for group, _ in self._SEMANTIC_MAP.items():
            if src_normalized in group:
                # Tìm target col thuộc cùng group
                for tgt_col in target_cols:
                    if self._normalize(tgt_col) in group:
                        return tgt_col
        return None

    def _ai_match(
        self,
        source_table: str,
        unmatched_source: list[str],
        target_table: str,
        unmatched_target: list[str],
    ) -> tuple[list[tuple[str, str, float]], str]:
        """
        Dùng LLM để match các columns còn lại.

        Returns:
            (list of (src, tgt, confidence) tuples, explanation text)
        """
        if not self._llm:
            return [], ""

        prompt = f"""Bạn là chuyên gia database migration MySQL → PostgreSQL.

Bảng nguồn (MySQL): `{source_table}`
Columns chưa match: {unmatched_source}

Bảng đích (PostgreSQL): `{target_table}`
Columns chưa match: {unmatched_target}

Hãy gợi ý mapping cho các columns trên (nếu có).
Format trả lời (JSON):
{{
  "mappings": [
    {{"source": "col_name", "target": "col_name", "confidence": 0.8, "reason": "..."}}
  ],
  "explanation": "Giải thích tổng quan"
}}

Confidence: 0.9=rất chắc, 0.7=có thể, 0.5=không chắc. Chỉ đề xuất nếu confidence >= 0.5.
Nếu không có mapping nào phù hợp, trả về {{"mappings": [], "explanation": "No matches found"}}"""

        try:
            import json  # noqa: PLC0415
            response = self._llm.complete(prompt)
            # Parse JSON từ response
            text = response.content.strip()
            # Extract JSON block nếu LLM wrap trong markdown code fence
            json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
            if json_match:
                text = json_match.group(1)

            data = json.loads(text)
            mappings = []
            for item in data.get("mappings", []):
                src = item.get("source", "")
                tgt = item.get("target", "")
                conf = float(item.get("confidence", 0.5))
                if src in unmatched_source and tgt in unmatched_target:
                    mappings.append((src, tgt, conf))

            return mappings, data.get("explanation", "")

        except Exception as e:
            logger.warning("AI matching failed: %s", e)
            return [], f"AI matching error: {e}"
