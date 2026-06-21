#!/usr/bin/env python3
"""
db_migrator/agent/llm_provider.py
===================================
LLM Provider abstraction + Google Gemini implementation.

Thiết kế theo pattern Strategy (GoF):
- `LLMProvider` (ABC): Interface chung cho mọi LLM
- `GeminiProvider`: Google Gemini Flash 2.0 implementation
- `MockLLMProvider`: Dùng trong tests, không cần API key

Tại sao cần abstraction?
-------------------------
Nếu hardcode trực tiếp với Gemini API, code sẽ bị "vendor lock-in".
Với interface `LLMProvider`, bạn có thể swap sang OpenAI/Claude chỉ
bằng cách đổi 1 dòng khởi tạo — không cần refactor toàn bộ logic.

Tương đương .NET:
    interface ILLMProvider {
        Task<string> CompleteAsync(string prompt);
    }
    class GeminiProvider : ILLMProvider { ... }
    class OpenAIProvider : ILLMProvider { ... }

Token Management:
    Gemini Flash 2.0 có context window 1M tokens nhưng:
    - Giới hạn input để tối ưu chi phí: mặc định 8,000 tokens
    - Track token usage để báo cáo và kiểm soát ngân sách
    - Retry tự động khi gặp rate limit (429) với exponential backoff

Tham khảo:
    https://ai.google.dev/gemini-api/docs
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["LLMProvider", "GeminiProvider", "MockLLMProvider", "LLMResponse", "LLMError"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """
    Response từ một LLM call.

    Attributes:
        content      : Text response từ model
        tokens_used  : Số tokens thực tế đã dùng (input + output)
        model        : Tên model đã dùng
        finish_reason: Lý do model dừng ("stop", "max_tokens", "error")
        latency_ms   : Thời gian phản hồi tính bằng millisecond
    """
    content: str
    tokens_used: int = 0
    model: str = "unknown"
    finish_reason: str = "stop"
    latency_ms: float = 0.0


class LLMError(Exception):
    """Lỗi khi gọi LLM API."""


# ---------------------------------------------------------------------------
# 3.1 Abstract LLM Provider Interface
# ---------------------------------------------------------------------------

class LLMProvider(ABC):
    """
    Interface chung cho tất cả LLM providers.

    Implement class này để hỗ trợ thêm providers mới (OpenAI, Claude, ...).

    Design principle: Dependency Inversion (DIP) — high-level modules (Agent)
    không phụ thuộc vào low-level details (Gemini SDK specifics).
    """

    @abstractmethod
    def complete(self, prompt: str, system: str | None = None, **kwargs) -> LLMResponse:
        """
        Gọi LLM với prompt và trả về response.

        Args:
            prompt : User message / prompt cần LLM xử lý
            system : System instruction (định hướng behavior của model)
            **kwargs: Provider-specific options (temperature, max_tokens, ...)

        Returns:
            LLMResponse với content và metadata

        Raises:
            LLMError: Khi API call thất bại sau retry
        """

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """
        Ước tính số tokens trong text.

        Args:
            text: Text cần đếm tokens

        Returns:
            Số tokens (ước tính)
        """

    @property
    @abstractmethod
    def max_context_tokens(self) -> int:
        """Token limit tối đa của model."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Tên model đang dùng."""


# ---------------------------------------------------------------------------
# 3.2 Google Gemini Provider
# ---------------------------------------------------------------------------

class GeminiProvider(LLMProvider):
    """
    Google Gemini API implementation.

    Sử dụng `google-generativeai` SDK với:
    - Gemini 2.0 Flash (mặc định) — nhanh, tiết kiệm, phù hợp cho tool use
    - Retry tự động với exponential backoff (tenacity)
    - Token budget tracking

    Setup:
        pip install google-generativeai
        export GEMINI_API_KEY="your-key-here"

        provider = GeminiProvider()  # Đọc key từ ENV
        # hoặc
        provider = GeminiProvider(api_key="your-key")

    Pricing (tham khảo):
        Gemini 2.0 Flash: $0.075/1M input tokens, $0.30/1M output tokens
        (jauh rẻ hơn GPT-4o: $5/$15 per 1M tokens)
    """

    # Model mặc định — balance giữa speed và capability
    DEFAULT_MODEL = "gemini-2.0-flash"
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 1.0  # seconds

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
    ) -> None:
        """
        Khởi tạo Gemini provider.

        Args:
            api_key          : Gemini API key (mặc định đọc từ GEMINI_API_KEY env)
            model            : Tên model (mặc định: gemini-2.0-flash)
            temperature      : 0.0 = deterministic, 1.0 = creative (mặc định: 0.2)
            max_output_tokens: Giới hạn tokens trong response
        """
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._model_name = model or self.DEFAULT_MODEL
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._client = None
        self._total_tokens_used = 0

        if not self._api_key:
            logger.warning(
                "⚠️  GEMINI_API_KEY not set — GeminiProvider will fail on actual calls. "
                "Set via: export GEMINI_API_KEY='your-key' or pass api_key parameter."
            )

    def _get_client(self):
        """Lazy initialization của Gemini client."""
        if self._client is None:
            try:
                import google.generativeai as genai  # noqa: PLC0415
            except ImportError as e:
                raise LLMError(
                    "google-generativeai not installed. Run: pip install google-generativeai"
                ) from e

            if not self._api_key:
                raise LLMError(
                    "Gemini API key is required. Set GEMINI_API_KEY environment variable "
                    "or pass api_key to GeminiProvider(api_key='...')"
                )

            genai.configure(api_key=self._api_key)
            self._client = genai.GenerativeModel(
                model_name=self._model_name,
                generation_config={
                    "temperature": self._temperature,
                    "max_output_tokens": self._max_output_tokens,
                    "response_mime_type": "text/plain",
                },
            )
        return self._client

    def complete(self, prompt: str, system: str | None = None, **kwargs) -> LLMResponse:
        """
        Gọi Gemini API với retry tự động.

        Exponential backoff: 1s → 2s → 4s khi gặp rate limit.
        Tương đương Polly retry policy trong .NET.

        Args:
            prompt : User message
            system : System instruction
            **kwargs: Ignored (for interface compatibility)

        Returns:
            LLMResponse

        Raises:
            LLMError: Sau khi hết số lần retry
        """
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        start_time = time.time()

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                client = self._get_client()
                response = client.generate_content(full_prompt)
                latency = (time.time() - start_time) * 1000

                # Extract token usage nếu có
                tokens_used = 0
                if hasattr(response, "usage_metadata") and response.usage_metadata:
                    tokens_used = (
                        getattr(response.usage_metadata, "total_token_count", 0) or 0
                    )
                self._total_tokens_used += tokens_used

                content = response.text if response.text else ""
                logger.debug(
                    "Gemini response: %d chars, %d tokens, %.0fms",
                    len(content), tokens_used, latency,
                )

                return LLMResponse(
                    content=content,
                    tokens_used=tokens_used,
                    model=self._model_name,
                    finish_reason="stop",
                    latency_ms=latency,
                )

            except Exception as e:
                last_error = e
                error_str = str(e).lower()

                # Rate limit (429) → đợi rồi retry
                if "429" in error_str or "quota" in error_str or "rate" in error_str:
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(
                        "Gemini rate limited (attempt %d/%d). Retrying in %.1fs...",
                        attempt + 1, self.MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    continue

                # Lỗi khác → không retry
                break

        raise LLMError(f"Gemini API call failed after {self.MAX_RETRIES} attempts: {last_error}") from last_error

    def count_tokens(self, text: str) -> int:
        """
        Ước tính số tokens (không gọi API).

        Quy tắc đơn giản: ~4 chars/token (English), ~2 chars/token (Vietnamese + CJK).
        Đây là ước tính — sai số ~15% so với actual tokenizer.

        Để chính xác tuyệt đối, dùng client.count_tokens() nhưng tốn API call.
        """
        # Rough estimate: avg 3.5 chars per token
        return max(1, len(text) // 3)

    @property
    def max_context_tokens(self) -> int:
        """Gemini 2.0 Flash context: 1M tokens."""
        return 1_000_000

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def total_tokens_used(self) -> int:
        """Tổng tokens đã dùng từ khi khởi tạo."""
        return self._total_tokens_used


# ---------------------------------------------------------------------------
# 3.3 Mock Provider (cho testing)
# ---------------------------------------------------------------------------

class MockLLMProvider(LLMProvider):
    """
    Mock LLM Provider cho unit tests — không gọi API thật.

    Cho phép define responses trước, kiểm tra prompts đã gửi.

    Usage:
        mock = MockLLMProvider(responses=["Answer 1", "Answer 2"])
        agent = MigrationAgent(llm_provider=mock)
        result = agent.run("question")
        assert mock.call_count == 1
        assert "question" in mock.last_prompt
    """

    def __init__(self, responses: list[str] | None = None, default_response: str = "OK") -> None:
        """
        Args:
            responses       : List responses theo thứ tự (round-robin)
            default_response: Response mặc định khi hết list
        """
        self._responses = responses or []
        self._default = default_response
        self._call_count = 0
        self._call_history: list[dict[str, Any]] = []

    def complete(self, prompt: str, system: str | None = None, **kwargs) -> LLMResponse:
        """Trả về response từ predefined list."""
        call_idx = self._call_count
        self._call_count += 1
        self._call_history.append({"prompt": prompt, "system": system})

        if self._responses and call_idx < len(self._responses):
            content = self._responses[call_idx]
        else:
            content = self._default

        return LLMResponse(content=content, tokens_used=len(prompt) // 4, model="mock")

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    @property
    def max_context_tokens(self) -> int:
        return 100_000

    @property
    def model_name(self) -> str:
        return "mock-llm"

    @property
    def call_count(self) -> int:
        return self._call_count

    @property
    def last_prompt(self) -> str:
        if self._call_history:
            return self._call_history[-1]["prompt"]
        return ""

    @property
    def call_history(self) -> list[dict[str, Any]]:
        return self._call_history
