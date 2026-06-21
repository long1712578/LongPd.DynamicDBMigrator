#!/usr/bin/env python3
"""
db_migrator/agent/memory.py
============================
Token-aware Conversation Memory — quản lý lịch sử hội thoại với AI.

Vấn đề cần giải quyết:
-----------------------
LLM có giới hạn "context window" (số tokens tối đa có thể đọc cùng lúc).
Nếu conversation quá dài → API lỗi hoặc chi phí tăng vọt.

Giải pháp — Sliding Window với 4 chiến lược:
1. **Keep All**: Giữ toàn bộ (cho sessions ngắn)
2. **Trim Old**: Xóa messages cũ nhất khi quá budget
3. **Summarize**: Tóm tắt messages cũ bằng LLM (chất lượng cao nhất)
4. **Fixed Window**: Chỉ giữ N messages gần nhất

Khái niệm tương đương .NET:
    Semantic Kernel → KernelMemory + ChatHistory
    Microsoft.SemanticKernel.ChatHistory.TrimMessages()

Tham khảo:
    "Lost in the Middle" paper: LLM attention kém ở giữa context
    → Luôn đặt thông tin quan trọng ở đầu hoặc cuối context
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["ConversationMemory", "Message", "Role", "TrimStrategy"]


class Role(str, Enum):
    """Vai trò trong hội thoại — chuẩn OpenAI/Gemini API."""
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"  # Kết quả từ tool call


@dataclass
class Message:
    """
    Một message trong conversation.

    Attributes:
        role    : Vai trò (system/user/assistant/tool)
        content : Nội dung message
        tokens  : Số tokens ước tính (None = chưa tính)
        metadata: Thông tin bổ sung (tool_name, tool_result, ...)
    """
    role: Role
    content: str
    tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def estimate_tokens(self) -> int:
        """Ước tính tokens nếu chưa có."""
        if self.tokens is None:
            self.tokens = max(1, len(self.content) // 3)
        return self.tokens

    def to_dict(self) -> dict:
        """Convert sang format dict cho API calls."""
        return {"role": self.role.value, "content": self.content}


class TrimStrategy(str, Enum):
    """Chiến lược khi context vượt quá budget."""
    TRIM_OLD = "trim_old"         # Xóa messages cũ nhất
    KEEP_SYSTEM = "keep_system"   # Luôn giữ system message, trim rest
    FIXED_WINDOW = "fixed_window" # Chỉ giữ N messages gần nhất


class ConversationMemory:
    """
    Quản lý lịch sử hội thoại với token budget awareness.

    Đảm bảo context luôn nằm trong giới hạn token của model,
    áp dụng chiến lược trim phù hợp khi vượt quá budget.

    Usage:
    ------
    .. code-block:: python

        memory = ConversationMemory(max_tokens=4000)

        memory.add_system("Bạn là expert về migration MySQL→PostgreSQL.")
        memory.add_user("Phân tích schema này cho tôi: ...")
        memory.add_assistant("Schema có 5 bảng, tôi thấy...")

        # Lấy context để gửi cho LLM
        messages = memory.get_context()
        # → [{"role": "system", ...}, {"role": "user", ...}, ...]

        # Thêm tool result
        memory.add_tool_result("analyze_schema", result_data)
    """

    def __init__(
        self,
        max_tokens: int = 8000,
        strategy: TrimStrategy = TrimStrategy.KEEP_SYSTEM,
        fixed_window_size: int = 20,
    ) -> None:
        """
        Args:
            max_tokens        : Token budget tối đa cho context
            strategy          : Chiến lược khi vượt quá budget
            fixed_window_size : Số messages giữ lại (cho FIXED_WINDOW strategy)
        """
        self._messages: list[Message] = []
        self._max_tokens = max_tokens
        self._strategy = strategy
        self._window_size = fixed_window_size
        self._system_message: Message | None = None

    # ------------------------------------------------------------------
    # Add messages
    # ------------------------------------------------------------------

    def add_system(self, content: str) -> None:
        """Thêm system instruction (giữ nguyên không trim)."""
        msg = Message(role=Role.SYSTEM, content=content)
        self._system_message = msg
        # System message luôn ở đầu
        self._messages = [m for m in self._messages if m.role != Role.SYSTEM]
        self._messages.insert(0, msg)
        self._trim_if_needed()

    def add_user(self, content: str) -> None:
        """Thêm user message."""
        self._messages.append(Message(role=Role.USER, content=content))
        self._trim_if_needed()

    def add_assistant(self, content: str, metadata: dict | None = None) -> None:
        """Thêm assistant response."""
        self._messages.append(
            Message(role=Role.ASSISTANT, content=content, metadata=metadata or {})
        )
        self._trim_if_needed()

    def add_tool_result(self, tool_name: str, result: Any) -> None:
        """
        Thêm kết quả từ tool call.

        Tool results được format đặc biệt để LLM hiểu là
        output từ một action, không phải input từ user.
        """
        content = f"[Tool: {tool_name}]\n{result!s}"
        self._messages.append(
            Message(
                role=Role.TOOL,
                content=content,
                metadata={"tool_name": tool_name},
            )
        )
        self._trim_if_needed()

    # ------------------------------------------------------------------
    # Retrieve context
    # ------------------------------------------------------------------

    def get_context(self) -> list[dict]:
        """
        Lấy danh sách messages để gửi cho LLM API.

        Tool messages được convert sang format "user" vì
        không phải tất cả LLM đều support role "tool".

        Returns:
            List of dicts: [{"role": "...", "content": "..."}, ...]
        """
        result = []
        for msg in self._messages:
            if msg.role == Role.TOOL:
                # Convert tool result sang user message (wider compatibility)
                result.append({"role": "user", "content": msg.content})
            else:
                result.append(msg.to_dict())
        return result

    def get_context_text(self) -> str:
        """
        Lấy toàn bộ context dưới dạng text (cho single-turn LLM calls).
        Dùng khi LLM provider không support multi-turn conversation.
        """
        parts = []
        for msg in self._messages:
            prefix = {
                Role.SYSTEM: "System",
                Role.USER: "User",
                Role.ASSISTANT: "Assistant",
                Role.TOOL: "Tool Result",
            }.get(msg.role, msg.role.value)
            parts.append(f"[{prefix}]: {msg.content}")
        return "\n\n".join(parts)

    @property
    def total_tokens(self) -> int:
        """Tổng tokens hiện tại trong memory."""
        return sum(m.estimate_tokens() for m in self._messages)

    @property
    def message_count(self) -> int:
        return len(self._messages)

    def clear(self, keep_system: bool = True) -> None:
        """
        Xóa lịch sử hội thoại.

        Args:
            keep_system: Nếu True, giữ lại system instruction
        """
        if keep_system and self._system_message:
            self._messages = [self._system_message]
        else:
            self._messages = []
            self._system_message = None

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _trim_if_needed(self) -> None:
        """Áp dụng trim strategy khi vượt quá token budget."""
        if self.total_tokens <= self._max_tokens:
            return

        logger.debug(
            "Memory token budget exceeded: %d/%d tokens. Applying %s strategy.",
            self.total_tokens, self._max_tokens, self._strategy.value,
        )

        if self._strategy == TrimStrategy.FIXED_WINDOW:
            self._apply_fixed_window()
        elif self._strategy == TrimStrategy.KEEP_SYSTEM:
            self._apply_keep_system_trim()
        else:
            self._apply_trim_old()

    def _apply_trim_old(self) -> None:
        """Xóa messages cũ nhất cho đến khi fit vào budget."""
        while self.total_tokens > self._max_tokens and len(self._messages) > 1:
            self._messages.pop(0)

    def _apply_keep_system_trim(self) -> None:
        """
        Giữ system message + trim oldest non-system messages.

        Chiến lược tốt nhất cho Agent:
        - System prompt luôn cần thiết (định hướng behavior)
        - Messages cũ thường ít relevant hơn
        """
        non_system = [m for m in self._messages if m.role != Role.SYSTEM]
        system = [m for m in self._messages if m.role == Role.SYSTEM]

        while self.total_tokens > self._max_tokens and len(non_system) > 1:
            non_system.pop(0)

        self._messages = system + non_system

    def _apply_fixed_window(self) -> None:
        """Chỉ giữ N messages gần nhất (+ system message)."""
        system = [m for m in self._messages if m.role == Role.SYSTEM]
        non_system = [m for m in self._messages if m.role != Role.SYSTEM]

        # Giữ N messages gần nhất
        kept = non_system[-self._window_size:]
        self._messages = system + kept
