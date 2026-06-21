#!/usr/bin/env python3
"""
db_migrator/agent/tools.py
===========================
Tool Registry — Hệ thống công cụ cho AI Agent.

Khái niệm "Tool Use" (Function Calling):
-----------------------------------------
Thay vì LLM chỉ trả về text, Tool Use cho phép LLM:
1. Nhận biết nó cần thêm thông tin
2. Gọi một hàm Python cụ thể (tool) để lấy thông tin đó
3. Dùng kết quả để tiếp tục reasoning

Ví dụ flow:
    User: "Phân tích bảng users và gợi ý mapping"
    LLM:  "Tôi cần xem schema trước. [CALL: analyze_schema(table=users)]"
    Tool: returns {"columns": [...], "row_count": 5000}
    LLM:  "Dựa vào schema, tôi gợi ý mapping sau: ..."

Tương đương .NET Semantic Kernel:
    [KernelFunction]
    public async Task<string> AnalyzeSchemaAsync(string tableName) { ... }
    kernel.AddPlugin(new MigrationPlugin(), "migration");

Design Pattern:
    Command Pattern (GoF) — mỗi Tool là một Command với execute()
    Registry Pattern — ToolRegistry là catalog của các Commands

Tham khảo:
    https://ai.google.dev/gemini-api/docs/function-calling
    https://platform.openai.com/docs/guides/function-calling
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)

__all__ = ["Tool", "ToolRegistry", "ToolResult", "ToolError", "create_migration_tools"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """
    Kết quả từ một tool call.

    Attributes:
        tool_name: Tên tool đã gọi
        success  : True nếu tool chạy thành công
        data     : Dữ liệu trả về (dict/list/str)
        error    : Thông báo lỗi (nếu success=False)
        summary  : Tóm tắt ngắn cho LLM (giảm token)
    """
    tool_name: str
    success: bool
    data: Any = None
    error: str | None = None
    summary: str = ""

    def to_context_string(self) -> str:
        """Format kết quả thành text dễ đọc cho LLM."""
        if not self.success:
            return f"[Tool {self.tool_name} FAILED]: {self.error}"

        if self.summary:
            return f"[Tool {self.tool_name} OK]: {self.summary}"

        # Truncate large data objects để tiết kiệm tokens
        data_str = json.dumps(self.data, ensure_ascii=False, default=str)
        if len(data_str) > 2000:
            data_str = data_str[:2000] + "... [truncated]"

        return f"[Tool {self.tool_name} OK]: {data_str}"


class ToolError(Exception):
    """Lỗi khi tool không thể thực thi."""


@dataclass
class Tool:
    """
    Định nghĩa một tool cho AI Agent.

    Attributes:
        name       : Tên tool (snake_case, unique)
        description: Mô tả ngắn cho LLM biết khi nào dùng tool này
        handler    : Hàm Python thực thi tool
        parameters : JSON Schema mô tả parameters
        examples   : Ví dụ cách dùng (giúp LLM dùng đúng)
    """
    name: str
    description: str
    handler: Callable[..., Any]
    parameters: dict[str, Any] = field(default_factory=dict)
    examples: list[str] = field(default_factory=list)

    def execute(self, **kwargs) -> ToolResult:
        """
        Thực thi tool với parameters.

        Returns:
            ToolResult với success/error status
        """
        try:
            logger.debug("Executing tool '%s' with args: %s", self.name, kwargs)
            result = self.handler(**kwargs)
            return ToolResult(
                tool_name=self.name,
                success=True,
                data=result,
                summary=self._summarize(result),
            )
        except ToolError as e:
            return ToolResult(tool_name=self.name, success=False, error=str(e))
        except Exception as e:
            logger.warning("Tool '%s' raised unexpected error: %s", self.name, e)
            return ToolResult(
                tool_name=self.name, success=False, error=f"Unexpected error: {e}"
            )

    def _summarize(self, result: Any) -> str:
        """Tạo summary ngắn từ result để tiết kiệm tokens."""
        if isinstance(result, dict):
            keys = list(result.keys())[:5]
            return f"Dict with keys: {keys}"
        if isinstance(result, list):
            return f"List with {len(result)} items"
        if isinstance(result, str) and len(result) > 200:
            return result[:200] + "..."
        return str(result)[:200]

    def to_prompt_description(self) -> str:
        """Format tool description cho system prompt của agent."""
        lines = [f"**{self.name}**: {self.description}"]
        if self.parameters:
            params = ", ".join(
                f"{k}: {v.get('type', 'any')}"
                for k, v in self.parameters.items()
            )
            lines.append(f"  Parameters: {params}")
        if self.examples:
            lines.append(f"  Example: {self.examples[0]}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    Registry quản lý tất cả tools của agent.

    Agent gọi tools thông qua registry:
    1. LLM quyết định cần dùng tool nào
    2. Agent parse tool name + parameters từ LLM response
    3. Registry dispatch tới tool handler phù hợp

    Usage:
    ------
    .. code-block:: python

        registry = ToolRegistry()
        registry.register(Tool(
            name="analyze_schema",
            description="Phân tích schema của một bảng",
            handler=lambda table: {"columns": [...]}
        ))

        result = registry.execute("analyze_schema", table="users")
        print(result.to_context_string())
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Đăng ký một tool vào registry."""
        if tool.name in self._tools:
            logger.warning("Tool '%s' already registered — overwriting", tool.name)
        self._tools[tool.name] = tool
        logger.debug("Tool registered: %s", tool.name)

    def execute(self, tool_name: str, **kwargs) -> ToolResult:
        """
        Tìm và thực thi tool theo tên.

        Args:
            tool_name: Tên tool cần gọi
            **kwargs : Parameters cho tool

        Returns:
            ToolResult (luôn return, không raise exception)
        """
        if tool_name not in self._tools:
            available = list(self._tools.keys())
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool '{tool_name}' not found. Available tools: {available}",
            )

        return self._tools[tool_name].execute(**kwargs)

    def get_tools_prompt(self) -> str:
        """
        Tạo danh sách tools cho system prompt của agent.

        Mô tả này giúp LLM biết:
        - Có những tools nào
        - Mỗi tool làm gì
        - Cách gọi mỗi tool

        Returns:
            Formatted string mô tả tất cả tools
        """
        if not self._tools:
            return "No tools available."

        lines = ["## Available Tools\n"]
        for tool in self._tools.values():
            lines.append(tool.to_prompt_description())
            lines.append("")

        lines.append("\n## How to use tools")
        lines.append(
            "To call a tool, respond with EXACTLY this format on its own line:\n"
            "CALL: tool_name(param1=value1, param2=value2)\n\n"
            "After receiving the tool result, continue your reasoning.\n"
            "When you have a final answer, respond with:\n"
            "ANSWER: your final answer here"
        )
        return "\n".join(lines)

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)


# ---------------------------------------------------------------------------
# Built-in Migration Tools Factory
# ---------------------------------------------------------------------------

def create_migration_tools(config=None, schema_cache: dict | None = None) -> list[Tool]:
    """
    Tạo danh sách built-in tools cho Migration Agent.

    Trả về các tools:
    - analyze_schema    : Phân tích thông tin schema table
    - list_tables       : Liệt kê các bảng trong config
    - get_column_mapping: Xem column mapping hiện tại
    - validate_config   : Kiểm tra config có hợp lệ không

    Args:
        config      : MigrationConfig instance (optional)
        schema_cache: Dict cache schema đã discover (optional)

    Returns:
        List of Tool objects ready to register
    """
    _schema_cache = schema_cache or {}

    def analyze_schema(table: str, schema: str | None = None) -> dict:
        """Phân tích schema của một bảng từ cache hoặc config."""
        if table in _schema_cache:
            table_info = _schema_cache[table]
            cols = getattr(table_info, "columns", [])
            return {
                "table": table,
                "column_count": len(cols),
                "columns": [
                    {
                        "name": getattr(c, "name", str(c)),
                        "type": getattr(c, "data_type", getattr(c, "type", "unknown")),
                        "is_pk": getattr(c, "is_primary_key", False),
                    }
                    for c in cols
                ],
                "primary_key": getattr(table_info, "primary_key", []),
            }
        raise ToolError(f"Table '{table}' not found in schema cache. Run schema discovery first.")

    def list_tables() -> dict:
        """Liệt kê tất cả bảng đã discover."""
        if _schema_cache:
            tables = list(_schema_cache.keys())
            return {"tables": tables, "count": len(tables)}

        if config is not None:
            try:
                mappings = config.get_all_table_mappings() if hasattr(config, "get_all_table_mappings") else []
                tables = [m.get("target_table", m.get("source_table", "?")) for m in mappings]
                return {"tables": tables, "count": len(tables), "source": "config"}
            except Exception as e:  # noqa: BLE001
                logger.debug("Could not get table mappings from config: %s", e)

        return {"tables": [], "count": 0, "note": "No schema discovered yet. Run discovery first."}

    def get_column_mapping(table: str) -> dict:
        """Lấy column mapping cho một bảng từ config."""
        if config is None:
            raise ToolError("Config not available. MigrationAgent was created without config.")

        try:
            mapping = config.get_column_mapping(table) if hasattr(config, "get_column_mapping") else {}
            return {"table": table, "mapping": mapping or {}, "note": "Use this to understand current mapping rules"}
        except Exception as e:
            raise ToolError(f"Cannot get mapping for table '{table}': {e}") from e

    def validate_config(check: str = "all") -> dict:
        """Kiểm tra config có hợp lệ không."""
        if config is None:
            return {"valid": False, "error": "No config provided"}

        issues = []
        warnings = []

        # Check basic structure
        try:
            has_tables = hasattr(config, "get_all_table_mappings")
            if has_tables:
                mappings = config.get_all_table_mappings()
                if not mappings:
                    warnings.append("No table mappings defined in config")
        except Exception as e:
            issues.append(f"Config validation error: {e}")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "warnings": warnings,
            "summary": f"{len(issues)} issues, {len(warnings)} warnings",
        }

    return [
        Tool(
            name="analyze_schema",
            description="Phân tích chi tiết schema của một bảng (columns, types, primary keys). "
                        "Gọi tool này để hiểu cấu trúc bảng trước khi gợi ý mapping.",
            handler=analyze_schema,
            parameters={
                "table": {"type": "string", "description": "Tên bảng cần phân tích"},
                "schema": {"type": "string", "description": "Schema name (optional, mặc định: public)"},
            },
            examples=["CALL: analyze_schema(table=users)", "CALL: analyze_schema(table=orders, schema=public)"],
        ),
        Tool(
            name="list_tables",
            description="Liệt kê tất cả các bảng đã được discover. "
                        "Gọi đầu tiên để biết có những bảng nào trước khi phân tích.",
            handler=list_tables,
            parameters={},
            examples=["CALL: list_tables()"],
        ),
        Tool(
            name="get_column_mapping",
            description="Xem cấu hình mapping cột hiện tại cho một bảng. "
                        "Dùng để kiểm tra mapping rules đã được cấu hình.",
            handler=get_column_mapping,
            parameters={
                "table": {"type": "string", "description": "Tên bảng cần xem mapping"},
            },
            examples=["CALL: get_column_mapping(table=users)"],
        ),
        Tool(
            name="validate_config",
            description="Kiểm tra tính hợp lệ của cấu hình migration. "
                        "Gọi khi cần xác nhận config trước khi chạy migration.",
            handler=validate_config,
            parameters={
                "check": {"type": "string", "description": "Loại check: 'all', 'mapping', 'transform'"},
            },
            examples=["CALL: validate_config(check=all)"],
        ),
    ]
