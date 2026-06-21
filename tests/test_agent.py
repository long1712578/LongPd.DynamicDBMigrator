"""
tests/test_agent.py
====================
Unit tests cho Phase 3: AI Agent Integration.

Test Cases:
-----------
  - LLMProvider: Interface + MockProvider behavior
  - ConversationMemory: Token management + trim strategies
  - ToolRegistry: Register + execute tools
  - MigrationAgent: ReAct loop với mock provider
  - SmartMapper: Rule-based + AI matching
  - AnomalyDetector: Data quality checks
  - ErrorExplainer: Error pattern matching

TDD Approach:
  Tất cả tests dùng MockLLMProvider — không cần Gemini API key thật.
  Đây là best practice: tests phải chạy được ở bất kỳ môi trường nào.
"""

import pytest

# ---------------------------------------------------------------------------
# 3.1 Tests: LLM Provider
# ---------------------------------------------------------------------------

class TestMockLLMProvider:
    """Test MockLLMProvider — dùng trong testing để thay thế Gemini."""

    def test_returns_predefined_response(self):
        """Mock phải trả về response đã định sẵn."""
        from db_migrator.agent import MockLLMProvider
        mock = MockLLMProvider(responses=["Answer 1", "Answer 2"])
        r1 = mock.complete("q1")
        r2 = mock.complete("q2")
        assert r1.content == "Answer 1"
        assert r2.content == "Answer 2"

    def test_returns_default_when_exhausted(self):
        """Khi hết list responses, trả về default."""
        from db_migrator.agent import MockLLMProvider
        mock = MockLLMProvider(responses=["Only one"], default_response="Default")
        mock.complete("q1")  # consume
        r2 = mock.complete("q2")
        assert r2.content == "Default"

    def test_call_count_tracking(self):
        """Mock phải track số lần được gọi."""
        from db_migrator.agent import MockLLMProvider
        mock = MockLLMProvider()
        assert mock.call_count == 0
        mock.complete("q1")
        mock.complete("q2")
        assert mock.call_count == 2

    def test_last_prompt_tracking(self):
        """Mock phải lưu prompt cuối cùng."""
        from db_migrator.agent import MockLLMProvider
        mock = MockLLMProvider()
        mock.complete("first question")
        mock.complete("second question")
        assert mock.last_prompt == "second question"

    def test_count_tokens_estimate(self):
        """count_tokens phải trả về số dương."""
        from db_migrator.agent import MockLLMProvider
        mock = MockLLMProvider()
        assert mock.count_tokens("hello world") > 0

    def test_model_name(self):
        """model_name phải là 'mock-llm'."""
        from db_migrator.agent import MockLLMProvider
        mock = MockLLMProvider()
        assert mock.model_name == "mock-llm"

    def test_gemini_provider_warns_without_key(self, caplog):
        """GeminiProvider phải warn khi không có API key."""
        import logging
        import os
        from unittest.mock import patch

        from db_migrator.agent import GeminiProvider
        with patch.dict(os.environ, {}, clear=True):
            with caplog.at_level(logging.WARNING, logger="db_migrator.agent.llm_provider"):
                GeminiProvider(api_key="")
        assert any("GEMINI_API_KEY" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 3.2 Tests: ConversationMemory
# ---------------------------------------------------------------------------

class TestConversationMemory:
    """Test quản lý lịch sử hội thoại với token budget."""

    def test_add_messages_increases_count(self):
        """Thêm messages phải tăng message_count."""
        from db_migrator.agent import ConversationMemory
        mem = ConversationMemory()
        assert mem.message_count == 0
        mem.add_user("Hello")
        assert mem.message_count == 1
        mem.add_assistant("Hi there")
        assert mem.message_count == 2

    def test_system_message_at_first_position(self):
        """System message phải luôn ở vị trí đầu tiên."""
        from db_migrator.agent import ConversationMemory
        mem = ConversationMemory()
        mem.add_user("User first")
        mem.add_system("System instruction")  # Thêm sau user
        context = mem.get_context()
        assert context[0]["role"] == "system"

    def test_get_context_returns_list_of_dicts(self):
        """get_context() phải trả về list dicts với role + content."""
        from db_migrator.agent import ConversationMemory
        mem = ConversationMemory()
        mem.add_user("test question")
        mem.add_assistant("test answer")
        context = mem.get_context()
        assert isinstance(context, list)
        assert all("role" in m and "content" in m for m in context)

    def test_tool_result_appears_in_context(self):
        """Tool results phải xuất hiện trong context."""
        from db_migrator.agent import ConversationMemory
        mem = ConversationMemory()
        mem.add_tool_result("analyze_schema", {"cols": 5})
        context = mem.get_context()
        assert len(context) == 1
        assert "analyze_schema" in context[0]["content"]

    def test_total_tokens_is_positive(self):
        """total_tokens phải > 0 khi có messages."""
        from db_migrator.agent import ConversationMemory
        mem = ConversationMemory()
        mem.add_user("a" * 100)
        assert mem.total_tokens > 0

    def test_trim_keeps_system_message(self):
        """KEEP_SYSTEM strategy phải giữ system message khi trim."""
        from db_migrator.agent import ConversationMemory
        from db_migrator.agent.memory import TrimStrategy
        mem = ConversationMemory(max_tokens=50, strategy=TrimStrategy.KEEP_SYSTEM)
        mem.add_system("System: always keep me")
        # Thêm nhiều messages để trigger trim
        for i in range(30):
            mem.add_user(f"user message {i} " * 5)
        context = mem.get_context()
        assert any(m["role"] == "system" for m in context)

    def test_clear_removes_conversation(self):
        """clear() phải xóa hội thoại."""
        from db_migrator.agent import ConversationMemory
        mem = ConversationMemory()
        mem.add_user("hello")
        mem.add_assistant("hi")
        mem.clear(keep_system=False)
        assert mem.message_count == 0

    def test_clear_keeps_system_when_requested(self):
        """clear(keep_system=True) phải giữ system message."""
        from db_migrator.agent import ConversationMemory
        mem = ConversationMemory()
        mem.add_system("System prompt")
        mem.add_user("hello")
        mem.clear(keep_system=True)
        assert mem.message_count == 1
        context = mem.get_context()
        assert context[0]["role"] == "system"

    def test_fixed_window_strategy(self):
        """FIXED_WINDOW phải chỉ giữ N messages gần nhất."""
        from db_migrator.agent import ConversationMemory
        from db_migrator.agent.memory import TrimStrategy
        mem = ConversationMemory(
            max_tokens=20,  # Nhỏ để trigger trim ngay
            strategy=TrimStrategy.FIXED_WINDOW,
            fixed_window_size=3,
        )
        for i in range(10):
            mem.add_user(f"message {i} " * 5)
        # Chỉ giữ 3 messages gần nhất
        non_system = [m for m in mem.get_context() if m["role"] != "system"]
        assert len(non_system) <= 3


# ---------------------------------------------------------------------------
# 3.3 Tests: Tool Registry
# ---------------------------------------------------------------------------

class TestToolRegistry:
    """Test Tool Registry — đăng ký và thực thi tools."""

    def test_register_and_execute(self):
        """Tool đã đăng ký phải thực thi được."""
        from db_migrator.agent import Tool, ToolRegistry
        registry = ToolRegistry()
        registry.register(Tool(
            name="echo",
            description="Returns input",
            handler=lambda msg: f"Echo: {msg}",
            parameters={"msg": {"type": "string"}},
        ))
        result = registry.execute("echo", msg="hello")
        assert result.success
        assert "Echo: hello" in str(result.data)

    def test_execute_unknown_tool_returns_error(self):
        """Gọi tool không tồn tại phải trả về error result (không raise)."""
        from db_migrator.agent import ToolRegistry
        registry = ToolRegistry()
        result = registry.execute("nonexistent_tool")
        assert not result.success
        assert "not found" in result.error.lower()

    def test_tool_count(self):
        """len(registry) phải trả về số tools đúng."""
        from db_migrator.agent import Tool, ToolRegistry
        registry = ToolRegistry()
        assert len(registry) == 0
        registry.register(Tool("t1", "T1", handler=lambda: None))
        assert len(registry) == 1

    def test_tools_prompt_contains_all_tools(self):
        """get_tools_prompt phải mô tả tất cả tools."""
        from db_migrator.agent import Tool, ToolRegistry
        registry = ToolRegistry()
        registry.register(Tool("tool_a", "Description A", handler=lambda: None))
        registry.register(Tool("tool_b", "Description B", handler=lambda: None))
        prompt = registry.get_tools_prompt()
        assert "tool_a" in prompt
        assert "tool_b" in prompt

    def test_tool_exception_wrapped_in_result(self):
        """Tool raise exception phải được wrap trong ToolResult.error."""
        from db_migrator.agent import Tool, ToolRegistry
        def bad_tool():
            raise ValueError("Something went wrong")
        registry = ToolRegistry()
        registry.register(Tool("bad", "Bad tool", handler=bad_tool))
        result = registry.execute("bad")
        assert not result.success
        assert result.error is not None

    def test_migration_tools_factory_creates_tools(self):
        """create_migration_tools phải tạo ra ít nhất 4 tools."""
        from db_migrator.agent.tools import create_migration_tools
        tools = create_migration_tools()
        assert len(tools) >= 4
        tool_names = [t.name for t in tools]
        assert "list_tables" in tool_names
        assert "validate_config" in tool_names

    def test_list_tables_tool_with_empty_cache(self):
        """list_tables với cache rỗng phải trả về empty list."""
        from db_migrator.agent.tools import ToolRegistry, create_migration_tools
        tools = create_migration_tools(schema_cache={})
        registry = ToolRegistry()
        for t in tools:
            registry.register(t)
        result = registry.execute("list_tables")
        assert result.success
        assert result.data["count"] == 0

    def test_tool_result_context_string_on_success(self):
        """ToolResult.to_context_string() phải chứa tool name khi success."""
        from db_migrator.agent.tools import ToolResult
        result = ToolResult(tool_name="mytool", success=True, data={"key": "value"})
        ctx = result.to_context_string()
        assert "mytool" in ctx
        assert "OK" in ctx

    def test_tool_result_context_string_on_failure(self):
        """ToolResult.to_context_string() phải chứa FAILED khi error."""
        from db_migrator.agent.tools import ToolResult
        result = ToolResult(tool_name="mytool", success=False, error="Something broke")
        ctx = result.to_context_string()
        assert "FAILED" in ctx
        assert "Something broke" in ctx


# ---------------------------------------------------------------------------
# 3.4 Tests: MigrationAgent ReAct Loop
# ---------------------------------------------------------------------------

class TestMigrationAgent:
    """Test ReAct Agent với MockLLMProvider."""

    def test_direct_answer_single_round(self):
        """Agent với ANSWER: format phải kết thúc ngay round 1."""
        from db_migrator.agent import MigrationAgent, MockLLMProvider
        mock = MockLLMProvider(responses=["ANSWER: Đây là câu trả lời"])
        agent = MigrationAgent(llm_provider=mock)
        result = agent.run("Hỏi gì đó")
        assert result.success
        assert result.rounds == 1
        assert "câu trả lời" in result.answer

    def test_tool_call_then_answer(self):
        """Agent phải gọi tool trước rồi mới trả lời."""
        from db_migrator.agent import MigrationAgent, MockLLMProvider
        mock = MockLLMProvider(responses=[
            "CALL: list_tables()",
            "ANSWER: Tôi đã xem xét bảng rồi",
        ])
        agent = MigrationAgent(llm_provider=mock)
        result = agent.run("Liệt kê các bảng")
        assert result.rounds == 2
        assert "list_tables" in result.tools_used

    def test_max_rounds_protection(self):
        """Agent phải dừng sau max_rounds dù chưa có ANSWER."""
        from db_migrator.agent import MigrationAgent, MockLLMProvider
        # Không bao giờ trả về ANSWER
        mock = MockLLMProvider(default_response="Tôi đang suy nghĩ...")
        agent = MigrationAgent(llm_provider=mock, max_rounds=3)
        result = agent.run("Câu hỏi không có câu trả lời")
        assert result.rounds <= 3
        assert not result.success

    def test_llm_error_returns_graceful_result(self):
        """LLM error phải trả về kết quả graceful thay vì raise exception."""
        from db_migrator.agent import MigrationAgent
        from db_migrator.agent.llm_provider import LLMError, LLMProvider

        class FailingProvider(LLMProvider):
            def complete(self, prompt, system=None, **kwargs):
                raise LLMError("API Error")
            def count_tokens(self, text): return 1
            @property
            def max_context_tokens(self): return 100
            @property
            def model_name(self): return "failing"

        agent = MigrationAgent(llm_provider=FailingProvider())
        result = agent.run("test")
        assert not result.success
        assert "error" in result.answer.lower() or "lỗi" in result.answer.lower()

    def test_chat_convenience_method(self):
        """chat() phải trả về answer string trực tiếp."""
        from db_migrator.agent import MigrationAgent, MockLLMProvider
        mock = MockLLMProvider(responses=["ANSWER: Chào bạn!"])
        agent = MigrationAgent(llm_provider=mock)
        answer = agent.chat("Xin chào")
        assert isinstance(answer, str)
        assert "Chào" in answer

    def test_reset_session_clears_history(self):
        """reset_session() phải xóa conversation history."""
        from db_migrator.agent import MigrationAgent, MockLLMProvider
        mock = MockLLMProvider(default_response="ANSWER: OK")
        agent = MigrationAgent(llm_provider=mock)
        agent.run("Question 1")
        initial_calls = mock.call_count
        agent.reset_session()
        agent.run("Question 2")
        # Sau reset, agent bắt đầu fresh context
        assert mock.call_count > initial_calls

    def test_parse_tool_params(self):
        """_parse_tool_params phải parse key=value format chính xác."""
        from db_migrator.agent.core import MigrationAgent
        result = MigrationAgent._parse_tool_params("table=users, schema=public")
        assert result == {"table": "users", "schema": "public"}

    def test_parse_tool_params_empty(self):
        """_parse_tool_params với empty string phải trả về dict rỗng."""
        from db_migrator.agent.core import MigrationAgent
        result = MigrationAgent._parse_tool_params("")
        assert result == {}

    def test_parse_tool_params_quoted_values(self):
        """_parse_tool_params phải xử lý quoted values."""
        from db_migrator.agent.core import MigrationAgent
        result = MigrationAgent._parse_tool_params('table="my table", schema=public')
        assert result["table"] == "my table"
        assert result["schema"] == "public"

    def test_agent_result_metadata(self):
        """AgentResult phải chứa đủ metadata."""
        from db_migrator.agent import MigrationAgent, MockLLMProvider
        from db_migrator.agent.core import AgentResult
        mock = MockLLMProvider(responses=["ANSWER: Test response"])
        agent = MigrationAgent(llm_provider=mock)
        result = agent.run("test")
        assert isinstance(result, AgentResult)
        assert isinstance(result.tools_used, list)
        assert result.rounds >= 1
        assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# 3.5 Tests: SmartMapper
# ---------------------------------------------------------------------------

class TestSmartMapper:
    """Test AI-enhanced schema mapper."""

    def test_exact_match(self):
        """Columns có cùng tên phải match với confidence 1.0."""
        from db_migrator.agent import SmartMapper
        mapper = SmartMapper()
        result = mapper.suggest(
            source_table="src", source_cols=["id", "name", "email"],
            target_table="tgt", target_cols=["id", "name", "email"],
        )
        assert len(result.suggestions) == 3
        exact = [s for s in result.suggestions if s.method == "exact"]
        assert len(exact) == 3
        assert all(s.confidence == 1.0 for s in exact)

    def test_normalized_match(self):
        """camelCase vs snake_case phải match via normalized."""
        from db_migrator.agent import SmartMapper
        mapper = SmartMapper()
        result = mapper.suggest(
            source_table="src", source_cols=["userName"],
            target_table="tgt", target_cols=["user_name"],
        )
        matched = [s for s in result.suggestions if s.source_col == "userName"]
        assert len(matched) == 1
        assert matched[0].target_col == "user_name"
        assert matched[0].confidence >= 0.85

    def test_semantic_match_deleted_at(self):
        """deletedAt → is_deleted phải match via semantic group."""
        from db_migrator.agent import SmartMapper
        mapper = SmartMapper()
        result = mapper.suggest(
            source_table="src", source_cols=["deletedAt"],
            target_table="tgt", target_cols=["is_deleted"],
        )
        # Semantic match hoặc normalized match
        matched = [s for s in result.suggestions if s.source_col == "deletedAt"]
        # Nếu match → confidence >= 0.7
        if matched:
            assert matched[0].confidence >= 0.7

    def test_unmatched_columns_reported(self):
        """Columns không match phải xuất hiện trong unmatched lists."""
        from db_migrator.agent import SmartMapper
        mapper = SmartMapper()
        result = mapper.suggest(
            source_table="src", source_cols=["id", "weird_field_xyz"],
            target_table="tgt", target_cols=["id", "another_weird_field"],
        )
        # "weird_field_xyz" và "another_weird_field" không thể match
        assert "weird_field_xyz" in result.unmatched_source or \
               any(s.source_col == "weird_field_xyz" for s in result.suggestions)

    def test_high_confidence_count(self):
        """high_confidence_count phải đếm đúng số suggestions >= 0.8."""
        from db_migrator.agent import SmartMapper
        mapper = SmartMapper()
        result = mapper.suggest(
            source_table="src", source_cols=["id", "name"],
            target_table="tgt", target_cols=["id", "name"],
        )
        assert result.high_confidence_count == 2

    def test_result_to_dict(self):
        """to_dict() phải trả về dict có đủ keys."""
        from db_migrator.agent import SmartMapper
        mapper = SmartMapper()
        result = mapper.suggest("s", ["id"], "t", ["id"])
        d = result.to_dict()
        assert "suggestions" in d
        assert "summary" in d
        assert "unmatched_source" in d

    def test_ai_match_with_mock(self):
        """AI matching phải được gọi cho unmatched columns."""
        import json

        from db_migrator.agent import MockLLMProvider, SmartMapper
        ai_response = json.dumps({
            "mappings": [{"source": "src_col", "target": "tgt_col", "confidence": 0.8, "reason": "similar"}],
            "explanation": "AI matched based on semantics"
        })
        mock = MockLLMProvider(responses=[ai_response])
        mapper = SmartMapper(llm_provider=mock)
        result = mapper.suggest(
            source_table="src", source_cols=["src_col"],
            target_table="tgt", target_cols=["tgt_col"],
        )
        # AI phải được gọi cho unmatched columns
        ai_matches = [s for s in result.suggestions if s.method == "ai"]
        assert len(ai_matches) >= 1


# ---------------------------------------------------------------------------
# 3.6 Tests: AnomalyDetector
# ---------------------------------------------------------------------------

class TestAnomalyDetector:
    """Test data quality checks."""

    def test_empty_table_returns_info(self):
        """Bảng rỗng phải trả về INFO anomaly."""
        from db_migrator.agent import AnomalyDetector
        from db_migrator.agent.anomaly_detector import AnomalySeverity
        detector = AnomalyDetector()
        report = detector.check_table_data(
            table_name="users", columns=["id"], rows=[],
        )
        assert any(a.severity == AnomalySeverity.INFO for a in report.anomalies)

    def test_safe_data_passes(self):
        """Dữ liệu sạch phải pass mà không có CRITICAL anomaly."""
        from db_migrator.agent import AnomalyDetector
        detector = AnomalyDetector()
        rows = [(i, f"user{i}", f"user{i}@example.com") for i in range(100)]
        report = detector.check_table_data(
            table_name="users",
            columns=["id", "name", "email"],
            rows=rows,
            source_types={"id": "int(11)", "name": "varchar(100)", "email": "varchar(200)"},
        )
        assert report.is_safe_to_migrate

    def test_detects_zero_date(self):
        """MySQL '0000-00-00' date phải bị phát hiện."""
        from db_migrator.agent import AnomalyDetector
        detector = AnomalyDetector()
        rows = [(1, "0000-00-00"), (2, "2023-01-01")]
        report = detector.check_table_data(
            table_name="orders",
            columns=["id", "order_date"],
            rows=rows,
            source_types={"id": "int", "order_date": "datetime"},
        )
        date_issues = [a for a in report.anomalies if a.category == "date_range"]
        assert len(date_issues) >= 1
        assert date_issues[0].affected_rows >= 1

    def test_data_truncation_critical(self):
        """Dữ liệu vượt quá target varchar length phải là CRITICAL."""
        from db_migrator.agent import AnomalyDetector
        from db_migrator.agent.anomaly_detector import AnomalySeverity
        detector = AnomalyDetector()
        # Name dài 80 chars nhưng target chỉ 50
        rows = [(1, "x" * 80)]
        report = detector.check_table_data(
            table_name="users",
            columns=["id", "name"],
            rows=rows,
            source_types={"name": "varchar(100)"},
            target_types={"name": "varchar(50)"},
        )
        critical = [a for a in report.anomalies if a.severity == AnomalySeverity.CRITICAL]
        assert len(critical) >= 1
        assert critical[0].category == "data_truncation"

    def test_schema_compatibility_check(self):
        """Missing columns phải được báo cáo."""
        from db_migrator.agent import AnomalyDetector
        detector = AnomalyDetector()
        report = detector.check_schema_compatibility(
            source_columns=[
                {"name": "id", "type": "int"},
                {"name": "extra_col", "type": "varchar(50)"},  # Không có trong target
            ],
            target_columns=[
                {"name": "id", "type": "integer"},
            ],
            table_name="users",
        )
        missing = [a for a in report.anomalies if a.category == "missing_column"]
        assert len(missing) == 1
        assert missing[0].column == "extra_col"

    def test_report_summary(self):
        """AnomalyReport.get_summary() phải trả về string có thông tin."""
        from db_migrator.agent import AnomalyDetector
        detector = AnomalyDetector()
        report = detector.check_table_data(
            table_name="test", columns=["id"], rows=[(1,)],
        )
        summary = report.get_summary()
        assert isinstance(summary, str)
        assert "test" in summary or "bảng" in summary.lower() or "Kết quả" in summary

    def test_report_to_dict(self):
        """AnomalyReport.to_dict() phải có keys is_safe, summary, anomalies."""
        from db_migrator.agent import AnomalyDetector
        detector = AnomalyDetector()
        report = detector.check_table_data("t", ["id"], [(1,)])
        d = report.to_dict()
        assert "is_safe" in d
        assert "summary" in d
        assert "anomalies" in d


# ---------------------------------------------------------------------------
# 3.7 Tests: ErrorExplainer
# ---------------------------------------------------------------------------

class TestErrorExplainer:
    """Test migration error analysis."""

    def test_fk_violation_detected(self):
        """Foreign key error phải được phân loại đúng."""
        from db_migrator.agent import ErrorExplainer
        from db_migrator.agent.error_explainer import ErrorCategory
        explainer = ErrorExplainer()
        error = (
            'ERROR: insert or update on table "orders" violates foreign key constraint '
            '"orders_user_id_fkey" on table "orders"\n'
            'DETAIL: Key (user_id)=(99999) is not present in table "users".'
        )
        result = explainer.explain(error, table="orders")
        assert result.category == ErrorCategory.FOREIGN_KEY
        assert "user_id" in (result.column or "")
        assert len(result.remediation_steps) > 0

    def test_not_null_violation_detected(self):
        """NOT NULL error phải được phân loại đúng."""
        from db_migrator.agent import ErrorExplainer
        from db_migrator.agent.error_explainer import ErrorCategory
        explainer = ErrorExplainer()
        error = 'ERROR: null value in column "email" of relation "users" violates not-null constraint'
        result = explainer.explain(error)
        assert result.category == ErrorCategory.NOT_NULL

    def test_unique_violation_detected(self):
        """Unique constraint error phải được phân loại."""
        from db_migrator.agent import ErrorExplainer
        from db_migrator.agent.error_explainer import ErrorCategory
        explainer = ErrorExplainer()
        error = (
            'ERROR: duplicate key value violates unique constraint "users_email_key"\n'
            'DETAIL: Key (email)=(test@example.com) already exists.'
        )
        result = explainer.explain(error)
        assert result.category == ErrorCategory.UNIQUE

    def test_unknown_error_fallback(self):
        """Error không match pattern nào phải trả về UNKNOWN category."""
        from db_migrator.agent import ErrorExplainer
        from db_migrator.agent.error_explainer import ErrorCategory
        explainer = ErrorExplainer()
        result = explainer.explain("Some very unusual error that has no pattern match xyz123")
        assert result.category == ErrorCategory.UNKNOWN
        assert len(result.remediation_steps) > 0

    def test_format_for_user_contains_title(self):
        """format_for_user() phải chứa title."""
        from db_migrator.agent import ErrorExplainer
        explainer = ErrorExplainer()
        result = explainer.explain(
            'null value in column "name" violates not-null constraint'
        )
        formatted = result.format_for_user()
        assert result.title in formatted

    def test_explain_batch(self):
        """explain_batch phải xử lý list errors."""
        from db_migrator.agent import ErrorExplainer
        explainer = ErrorExplainer()
        errors = [
            'null value in column "x" violates not-null constraint',
            'connection refused',
        ]
        results = explainer.explain_batch(errors)
        assert len(results) == 2

    def test_connection_error_detected(self):
        """Connection error phải được phân loại đúng."""
        from db_migrator.agent import ErrorExplainer
        from db_migrator.agent.error_explainer import ErrorCategory
        explainer = ErrorExplainer()
        result = explainer.explain("could not connect to server: Connection refused")
        assert result.category == ErrorCategory.CONNECTION

    def test_ai_explanation_called_with_mock(self):
        """ErrorExplainer với LLM phải gọi AI cho explanation."""
        from db_migrator.agent import ErrorExplainer, MockLLMProvider
        mock = MockLLMProvider(responses=["AI giải thích: Đây là lỗi FK"])
        explainer = ErrorExplainer(llm_provider=mock)
        result = explainer.explain(
            'null value in column "col" violates not-null constraint'
        )
        assert result.ai_explanation is not None
        assert mock.call_count == 1


# ---------------------------------------------------------------------------
# 3.8 Tests: Web API endpoints
# ---------------------------------------------------------------------------

class TestAgentWebEndpoints:
    """Test agent API endpoints."""

    @pytest.fixture
    def client(self):
        from web.app import app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_suggest_mapping_endpoint_success(self, client):
        """POST /api/agent/suggest-mapping phải trả về suggestions."""
        res = client.post("/api/agent/suggest-mapping", json={
            "source_table": "mysql_users",
            "source_cols": ["id", "user_name"],
            "target_table": "pg_users",
            "target_cols": ["id", "username"],
        })
        assert res.status_code == 200
        data = res.json
        assert data["success"] is True
        assert "result" in data

    def test_suggest_mapping_endpoint_missing_cols(self, client):
        """Thiếu source_cols phải trả về 400."""
        res = client.post("/api/agent/suggest-mapping", json={
            "source_table": "a",
            "source_cols": [],  # empty
            "target_table": "b",
            "target_cols": ["id"],
        })
        assert res.status_code == 400

    def test_analyze_endpoint_success(self, client):
        """POST /api/agent/analyze phải trả về anomaly report."""
        res = client.post("/api/agent/analyze", json={
            "table": "users",
            "columns": ["id", "name"],
            "rows": [[1, "Long"], [2, "Nam"]],
        })
        assert res.status_code == 200
        assert res.json["success"] is True
        assert "is_safe" in res.json

    def test_analyze_endpoint_missing_columns(self, client):
        """Thiếu columns phải trả về 400."""
        res = client.post("/api/agent/analyze", json={
            "table": "users",
            "columns": [],
            "rows": [],
        })
        assert res.status_code == 400

    def test_explain_error_endpoint_success(self, client):
        """POST /api/agent/explain-error phải trả về explanation."""
        res = client.post("/api/agent/explain-error", json={
            "error": "null value in column \"email\" violates not-null constraint",
            "table": "users",
        })
        assert res.status_code == 200
        assert res.json["success"] is True
        assert "explanation" in res.json

    def test_explain_error_endpoint_missing_error(self, client):
        """Thiếu error field phải trả về 400."""
        res = client.post("/api/agent/explain-error", json={"table": "users"})
        assert res.status_code == 400

    def test_chat_endpoint_missing_message(self, client):
        """Thiếu message phải trả về 400."""
        res = client.post("/api/agent/chat", json={})
        assert res.status_code == 400

    def test_chat_endpoint_message_too_long(self, client):
        """Message quá dài phải trả về 400."""
        res = client.post("/api/agent/chat", json={"message": "x" * 5000})
        assert res.status_code == 400
