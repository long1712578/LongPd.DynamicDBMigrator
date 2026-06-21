#!/usr/bin/env python3
"""
db_migrator/agent/__init__.py
==============================
Public API cho AI Agent module (Phase 3).

Quick start:
    from db_migrator.agent import MigrationAgent, GeminiProvider

    provider = GeminiProvider(api_key="YOUR_KEY")
    agent = MigrationAgent(llm_provider=provider)

    result = agent.run("Phân tích schema và gợi ý mapping cho bảng users")
    print(result.answer)
"""

from .anomaly_detector import Anomaly, AnomalyDetector, AnomalyReport
from .core import AgentResult, MigrationAgent
from .error_explainer import ErrorExplainer, ExplainedError
from .llm_provider import GeminiProvider, LLMProvider, MockLLMProvider
from .memory import ConversationMemory, Message
from .smart_mapper import SmartMapper, SmartMappingResult
from .tools import Tool, ToolRegistry

__all__ = [
    "MigrationAgent",
    "AgentResult",
    "LLMProvider",
    "GeminiProvider",
    "MockLLMProvider",
    "ConversationMemory",
    "Message",
    "Tool",
    "ToolRegistry",
    "SmartMapper",
    "SmartMappingResult",
    "AnomalyDetector",
    "AnomalyReport",
    "Anomaly",
    "ErrorExplainer",
    "ExplainedError",
]
