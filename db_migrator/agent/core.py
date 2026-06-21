#!/usr/bin/env python3
"""
db_migrator/agent/core.py
==========================
ReAct Agent Loop — Não bộ của Smart Migration Assistant.

ReAct Pattern (Yao et al., 2022):
-----------------------------------
ReAct = **Re**asoning + **Act**ing

Vòng lặp:
    1. OBSERVE: Nhận input từ user
    2. THINK:   LLM suy nghĩ về bước tiếp theo (reasoning)
    3. ACT:     Gọi tool hoặc trả lời cuối
    4. OBSERVE: Nhận kết quả tool
    5. THINK:   Tiếp tục reasoning với thông tin mới
    6. Lặp lại đến khi có ANSWER hoặc hết max_rounds

Lợi ích so với single-turn LLM:
    ✅ Có thể gọi nhiều tools theo sequence
    ✅ Tự sửa lỗi khi tool fail
    ✅ Reasoning rõ ràng, traceable
    ✅ Dừng khi đã đủ thông tin (không hallucinate)

So sánh với .NET Semantic Kernel:
    SK có AutoFunctionInvocationFilter — tương tự max_rounds
    SK AgentChat — tương tự MigrationAgent
    SK ChatCompletionAgent — tương tự GeminiProvider

Sơ đồ luồng:
    ┌─────────────────────────────────────────┐
    │            MigrationAgent               │
    │                                         │
    │  input → [Memory] → [LLM: Reason] ──┐  │
    │                         ↑            │  │
    │                    [Observe]     [Parse]│
    │                         │            │  │
    │              [ToolRegistry] ← [Act]  │  │
    │                                      │  │
    │                    ← → ← → ← → ← ←  │  │
    │                                      │  │
    │               [ANSWER] ← ──────────── │  │
    └─────────────────────────────────────────┘

Tham khảo:
    Paper: https://arxiv.org/abs/2210.03629
    "ReAct: Synergizing Reasoning and Acting in Language Models"
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .llm_provider import LLMError, LLMProvider
from .memory import ConversationMemory, TrimStrategy
from .tools import ToolRegistry, create_migration_tools

logger = logging.getLogger(__name__)

__all__ = ["MigrationAgent", "AgentResult", "AgentError"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """
    Kết quả sau khi Agent hoàn thành task.

    Attributes:
        answer      : Câu trả lời cuối cùng cho user
        tools_used  : Danh sách tools đã gọi (để debug)
        rounds      : Số vòng lặp ReAct đã thực hiện
        tokens_used : Tổng tokens đã dùng
        success     : True nếu có câu trả lời rõ ràng
        duration_ms : Thời gian thực thi (milliseconds)
    """
    answer: str
    tools_used: list[str] = field(default_factory=list)
    rounds: int = 0
    tokens_used: int = 0
    success: bool = True
    duration_ms: float = 0.0
    reasoning_trace: list[str] = field(default_factory=list)


class AgentError(Exception):
    """Lỗi trong quá trình agent loop."""


# ---------------------------------------------------------------------------
# System Prompt (định hướng behavior của Agent)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TEMPLATE = """\
Bạn là Smart Migration Assistant — chuyên gia tư vấn migration database MySQL → PostgreSQL.

## Vai trò của bạn
- Phân tích schema, mapping, và dữ liệu để đưa ra gợi ý chính xác
- Giải thích các vấn đề kỹ thuật bằng ngôn ngữ dễ hiểu
- Sử dụng tools để lấy thông tin thực tế trước khi trả lời

## Nguyên tắc
1. LUÔN dùng tools để lấy thông tin thực tế thay vì đoán mò
2. Nếu tool fail, thông báo rõ ràng và gợi ý bước tiếp theo
3. Trả lời bằng tiếng Việt, thuật ngữ kỹ thuật có thể để tiếng Anh
4. Ngắn gọn, súc tích — không dài dòng không cần thiết

{tools_section}

## Quy tắc Response
Khi cần gọi tool:
    CALL: tool_name(param1=value1, param2=value2)

Khi đã có đủ thông tin và sẵn sàng trả lời:
    ANSWER: nội dung câu trả lời chi tiết ở đây

Chỉ dùng một trong hai format trên. Không kết hợp.
"""

# Regex để parse tool calls từ LLM response
_TOOL_CALL_RE = re.compile(
    r"CALL:\s*(\w+)\s*\(([^)]*)\)",
    re.IGNORECASE,
)
_ANSWER_RE = re.compile(
    r"ANSWER:\s*(.*)",
    re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# MigrationAgent — ReAct implementation
# ---------------------------------------------------------------------------

class MigrationAgent:
    """
    AI-powered migration assistant với ReAct agent loop.

    Kết hợp LLM reasoning với tool execution để:
    - Trả lời câu hỏi về migration
    - Phân tích schema và gợi ý mapping
    - Giải thích lỗi và đề xuất fix

    Usage:
    ------
    .. code-block:: python

        # Với Gemini API (production)
        from db_migrator.agent import MigrationAgent, GeminiProvider
        provider = GeminiProvider()  # đọc GEMINI_API_KEY từ env
        agent = MigrationAgent(llm_provider=provider, config=migration_config)
        result = agent.run("Phân tích bảng users và gợi ý mapping")
        print(result.answer)

        # Với Mock (testing)
        from db_migrator.agent import MigrationAgent, MockLLMProvider
        mock = MockLLMProvider(responses=["ANSWER: Tôi đã phân tích..."])
        agent = MigrationAgent(llm_provider=mock)
        result = agent.run("test question")
    """

    DEFAULT_MAX_ROUNDS = 8
    DEFAULT_MAX_TOKENS = 6000  # Context budget per call

    def __init__(
        self,
        llm_provider: LLMProvider,
        tool_registry: ToolRegistry | None = None,
        config: Any = None,
        schema_cache: dict | None = None,
        max_rounds: int = DEFAULT_MAX_ROUNDS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        session_id: str | None = None,
    ) -> None:
        """
        Khởi tạo Migration Agent.

        Args:
            llm_provider  : LLM provider (GeminiProvider hoặc MockLLMProvider)
            tool_registry : Custom ToolRegistry (nếu None, sẽ dùng defaults)
            config        : MigrationConfig instance (cho tools)
            schema_cache  : Dict schema đã discover (cho analyze_schema tool)
            max_rounds    : Số vòng ReAct tối đa (tránh infinite loop)
            max_tokens    : Token budget cho mỗi LLM call
            session_id    : ID để track session (optional)
        """
        self._llm = llm_provider
        self._max_rounds = max_rounds
        self._session_id = session_id

        # Setup tool registry
        self._tools = tool_registry or self._create_default_registry(config, schema_cache)

        # Setup conversation memory
        self._memory = ConversationMemory(
            max_tokens=max_tokens,
            strategy=TrimStrategy.KEEP_SYSTEM,
        )

        # Initialize system prompt với tool descriptions
        tools_section = self._tools.get_tools_prompt()
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(tools_section=tools_section)
        self._memory.add_system(system_prompt)

        self._total_tokens = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, user_input: str) -> AgentResult:
        """
        Chạy ReAct agent loop cho một user request.

        Args:
            user_input: Câu hỏi hoặc task từ user

        Returns:
            AgentResult với answer và metadata

        Raises:
            AgentError: Nếu LLM fail nghiêm trọng (không phải soft errors)
        """
        start_time = time.time()
        tools_used: list[str] = []
        reasoning_trace: list[str] = []

        logger.info("Agent received input: %.100s...", user_input)
        self._memory.add_user(user_input)

        for round_num in range(1, self._max_rounds + 1):
            logger.debug("Agent round %d/%d", round_num, self._max_rounds)

            # --- THINK: Gọi LLM để reasoning ---
            try:
                context = self._memory.get_context_text()
                response = self._llm.complete(prompt=context)
                self._total_tokens += response.tokens_used
                llm_text = response.content.strip()
            except LLMError as e:
                logger.error("LLM call failed in round %d: %s", round_num, e)
                return AgentResult(
                    answer=f"Xin lỗi, tôi gặp lỗi khi kết nối AI: {e}. "
                           "Vui lòng kiểm tra GEMINI_API_KEY và thử lại.",
                    tools_used=tools_used,
                    rounds=round_num,
                    tokens_used=self._total_tokens,
                    success=False,
                    duration_ms=(time.time() - start_time) * 1000,
                )

            reasoning_trace.append(f"Round {round_num}: {llm_text[:200]}")

            # --- PARSE: Kiểm tra LLM muốn làm gì ---

            # Case 1: LLM có ANSWER → kết thúc
            answer_match = _ANSWER_RE.search(llm_text)
            if answer_match:
                final_answer = answer_match.group(1).strip()
                self._memory.add_assistant(llm_text)

                duration = (time.time() - start_time) * 1000
                logger.info(
                    "Agent completed in %d rounds, %.0fms, %d tokens used",
                    round_num, duration, self._total_tokens,
                )
                return AgentResult(
                    answer=final_answer,
                    tools_used=tools_used,
                    rounds=round_num,
                    tokens_used=self._total_tokens,
                    success=True,
                    duration_ms=duration,
                    reasoning_trace=reasoning_trace,
                )

            # Case 2: LLM muốn gọi tool
            tool_match = _TOOL_CALL_RE.search(llm_text)
            if tool_match:
                tool_name = tool_match.group(1).strip()
                params_raw = tool_match.group(2).strip()

                # Parse parameters: "key1=val1, key2=val2"
                params = self._parse_tool_params(params_raw)

                logger.debug("Calling tool: %s(%s)", tool_name, params)
                tools_used.append(tool_name)

                # --- ACT: Thực thi tool ---
                tool_result = self._tools.execute(tool_name, **params)

                # --- OBSERVE: Thêm kết quả vào memory ---
                self._memory.add_assistant(llm_text)
                self._memory.add_tool_result(tool_name, tool_result.to_context_string())

                continue  # Next round

            # Case 3: LLM không theo format → xử lý gracefully
            # Treat response như một phần reasoning, tiếp tục
            self._memory.add_assistant(llm_text)

            # Nếu round cuối mà vẫn chưa có ANSWER
            if round_num == self._max_rounds:
                # Dùng response cuối làm answer
                duration = (time.time() - start_time) * 1000
                return AgentResult(
                    answer=llm_text if llm_text else "Tôi không thể trả lời câu hỏi này lúc này.",
                    tools_used=tools_used,
                    rounds=round_num,
                    tokens_used=self._total_tokens,
                    success=False,
                    duration_ms=duration,
                    reasoning_trace=reasoning_trace,
                )

        # Hết max_rounds
        duration = (time.time() - start_time) * 1000
        return AgentResult(
            answer="Tôi đã hết số vòng suy nghĩ cho phép. Xin hãy hỏi cụ thể hơn.",
            tools_used=tools_used,
            rounds=self._max_rounds,
            tokens_used=self._total_tokens,
            success=False,
            duration_ms=duration,
            reasoning_trace=reasoning_trace,
        )

    def chat(self, user_input: str) -> str:
        """
        Simplified interface: run() nhưng chỉ trả về answer string.

        Args:
            user_input: Câu hỏi từ user

        Returns:
            Answer string
        """
        return self.run(user_input).answer

    def reset_session(self) -> None:
        """Reset conversation history (giữ system prompt)."""
        self._memory.clear(keep_system=True)
        self._total_tokens = 0
        logger.info("Agent session reset")

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _create_default_registry(config: Any, schema_cache: dict | None) -> ToolRegistry:
        """Tạo ToolRegistry với built-in migration tools."""
        registry = ToolRegistry()
        tools = create_migration_tools(config=config, schema_cache=schema_cache or {})
        for tool in tools:
            registry.register(tool)
        return registry

    @staticmethod
    def _parse_tool_params(params_raw: str) -> dict[str, str]:
        """
        Parse "key1=val1, key2=val2" thành dict.

        Xử lý các trường hợp:
        - Giá trị có dấu phẩy trong quotes: key="a, b"
        - Khoảng trắng xung quanh: key = value
        - Không có params: ""
        - Params đơn: table=users

        Returns:
            Dict params hoặc dict rỗng nếu không parse được
        """
        if not params_raw.strip():
            return {}

        params: dict[str, str] = {}
        # Pattern: key=value hoặc key="value" hoặc key='value'
        pattern = re.compile(
            r'(\w+)\s*=\s*(?:"([^"]*?)"|\'([^\']*?)\'|([^,\s]+))'
        )
        for match in pattern.finditer(params_raw):
            key = match.group(1)
            # Lấy giá trị từ bất kỳ capture group nào có dữ liệu
            value = match.group(2) or match.group(3) or match.group(4) or ""
            params[key] = value.strip()

        return params
