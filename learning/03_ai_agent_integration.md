# 🤖 03. AI Agent Integration — Smart Migration Assistant

> **Mục tiêu**: Hiểu sâu về AI Agent patterns — ReAct Loop, Tool Use, Token Management, và cách tích hợp LLM vào ứng dụng Python. Kiến thức này áp dụng trực tiếp cho mọi loại AI-powered application.

---

## Mục lục

1. [Tại sao cần AI Agent?](#1-tai-sao-can-ai-agent)
2. [ReAct Pattern — Não bộ của Agent](#2-react-pattern)
3. [Tool Use — Mắt và tay của Agent](#3-tool-use)
4. [LLM Provider Abstraction](#4-llm-provider-abstraction)
5. [Token Management — Quản lý "bộ nhớ"](#5-token-management)
6. [SmartMapper — Gợi ý mapping thông minh](#6-smart-mapper)
7. [AnomalyDetector — Kiểm tra trước khi migrate](#7-anomaly-detector)
8. [ErrorExplainer — AI giải thích lỗi](#8-error-explainer)
9. [Tích hợp vào Flask API](#9-flask-integration)
10. [So sánh Python vs .NET Semantic Kernel](#10-python-vs-net)

---

## 1. Tại sao cần AI Agent?

### Sự khác biệt giữa AI Chatbot và AI Agent

| | AI Chatbot | AI Agent |
|:---|:---|:---|
| **Kiến thức** | Chỉ biết từ training data | Có thể lấy thông tin thực tế qua Tools |
| **Hành động** | Chỉ trả lời text | Gọi functions, thực thi code |
| **Vòng lặp** | Single-turn (câu hỏi → câu trả lời) | Multi-turn (reason → act → observe → repeat) |
| **Accuracy** | Có thể hallucinate | Verify bằng kết quả thực tế từ tools |
| **Ví dụ** | ChatGPT chat thông thường | GitHub Copilot, Cursor, Claude Computer Use |

### Ví dụ trong Migration:

```
❌ Chatbot: "Hãy analyze schema bảng users"
→ LLM đoán mò, có thể sai vì không biết schema thực tế

✅ Agent:  "Hãy analyze schema bảng users"
→ THINK: "Tôi cần gọi tool để lấy schema thực tế"
→ ACT:   CALL: analyze_schema(table=users)
→ OBSERVE: {"columns": [{"name": "id", "type": "int"}, ...]}
→ ANSWER: "Bảng users có 5 cột, primary key là id..."
```

---

## 2. ReAct Pattern — Não bộ của Agent

### Paper gốc

**"ReAct: Synergizing Reasoning and Acting in Language Models"** (Yao et al., 2022)
- Link: https://arxiv.org/abs/2210.03629
- Được dùng trong: LangChain, AutoGen, Semantic Kernel, CrewAI

### Vòng lặp ReAct

```
┌─────────────────────────────────────────────────────┐
│                    ReAct Loop                        │
│                                                      │
│  Input ──→ [REASON] ──→ [ACT] ──→ [OBSERVE]        │
│                ↑                       │             │
│                └─────────────────────←┘             │
│                                                      │
│  Kết thúc khi: ANSWER hoặc max_rounds               │
└─────────────────────────────────────────────────────┘
```

### Code implementation:

```python
for round_num in range(max_rounds):
    # REASON: LLM suy nghĩ
    response = llm.complete(context)

    # Parse LLM output
    if "ANSWER:" in response:
        return AgentResult(answer=extract_answer(response))

    if "CALL:" in response:
        # ACT: Gọi tool
        tool_name, params = parse_tool_call(response)
        result = tool_registry.execute(tool_name, **params)

        # OBSERVE: Thêm kết quả vào context
        memory.add_tool_result(tool_name, result)
        continue  # Next round

# Hết max_rounds → graceful fallback
```

### Tại sao cần max_rounds?

Không có giới hạn → infinite loop nếu LLM bị "stuck".
Production systems thường dùng 5-10 rounds.

---

## 3. Tool Use — Mắt và tay của Agent

### Khái niệm

Tool Use (hay Function Calling) là cơ chế cho phép LLM:
1. Nhận danh sách tools với description
2. Quyết định gọi tool nào khi cần
3. Nhận kết quả và tiếp tục reasoning

```python
# Định nghĩa tool
tool = Tool(
    name="analyze_schema",
    description="Phân tích cấu trúc bảng database",
    handler=lambda table: get_schema(table),
    parameters={"table": {"type": "string"}},
)

# LLM nhận description và quyết định gọi khi cần
# → "CALL: analyze_schema(table=users)"
```

### Command Pattern

Tool Registry sử dụng **Command Pattern (GoF)**:

```python
# GoF Command Interface
class Tool:
    def execute(self, **kwargs) -> ToolResult:
        return ToolResult(success=True, data=self.handler(**kwargs))

# Registry = Invoker
class ToolRegistry:
    def execute(self, tool_name, **kwargs) -> ToolResult:
        tool = self._tools.get(tool_name)
        return tool.execute(**kwargs)
```

### Tại sao không return exception?

```python
# ✅ ĐÚNG — Tool luôn return ToolResult (không raise)
def execute(self, tool_name, **kwargs) -> ToolResult:
    try:
        result = tool.handler(**kwargs)
        return ToolResult(success=True, data=result)
    except Exception as e:
        return ToolResult(success=False, error=str(e))  # Agent tiếp tục reasoning

# ❌ SAI — Tool raise exception → Agent crash
def execute_bad(self, tool_name, **kwargs):
    return tool.handler(**kwargs)  # Raise → Agent loop bị break
```

---

## 4. LLM Provider Abstraction

### Strategy Pattern

Sử dụng **Strategy Pattern** để tách biệt Agent logic với LLM provider cụ thể:

```python
class LLMProvider(ABC):  # Interface
    @abstractmethod
    def complete(self, prompt: str) -> LLMResponse: ...

class GeminiProvider(LLMProvider):  # Concrete Strategy A
    def complete(self, prompt): return call_gemini_api(prompt)

class OpenAIProvider(LLMProvider):  # Concrete Strategy B
    def complete(self, prompt): return call_openai_api(prompt)

class MockLLMProvider(LLMProvider):  # Test Strategy
    def complete(self, prompt): return LLMResponse(content=self.responses.pop(0))

# Agent chỉ biết interface, không biết implementation
agent = MigrationAgent(llm_provider=GeminiProvider())  # Production
agent = MigrationAgent(llm_provider=MockLLMProvider())  # Testing
```

### Gemini Flash 2.0 — Tại sao chọn?

| Model | Speed | Cost | Context | Capability |
|:---|:---|:---|:---|:---|
| **Gemini 2.0 Flash** | ⚡⚡⚡ | 💰 rẻ | 1M tokens | ✅ Đủ cho tool use |
| GPT-4o | ⚡⚡ | 💰💰💰 đắt | 128K | ✅ Mạnh hơn |
| Claude 3.5 Sonnet | ⚡⚡⚡ | 💰💰 | 200K | ✅ Code rất tốt |
| Gemini 2.0 Pro | ⚡⚡ | 💰💰 | 2M tokens | ✅ Mạnh nhất |

**Chọn Flash cho MVP**: Nhanh nhất, rẻ nhất, đủ dùng cho tool use.

### Retry với Exponential Backoff

```python
for attempt in range(MAX_RETRIES):  # 0, 1, 2
    try:
        response = gemini.generate(prompt)
        return response
    except RateLimitError:
        delay = 1.0 * (2 ** attempt)  # 1s, 2s, 4s
        time.sleep(delay)
        continue
    except Exception:
        break  # Lỗi khác → không retry

raise LLMError("Failed after 3 attempts")
```

Tương đương .NET Polly:
```csharp
var policy = Policy
    .Handle<HttpRequestException>()
    .WaitAndRetryAsync(3,
        retryAttempt => TimeSpan.FromSeconds(Math.Pow(2, retryAttempt)));
```

---

## 5. Token Management

### Tại sao phải quản lý tokens?

```
LLM context window = "bộ nhớ ngắn hạn" của AI
→ Gemini Flash: 1,000,000 tokens
→ Nhưng gửi nhiều → chi phí tăng, latency tăng
→ Best practice: Giới hạn context budget per request
```

### Sliding Window Memory

```python
class ConversationMemory:
    def __init__(self, max_tokens=8000):
        self._messages = []
        self._max_tokens = max_tokens

    def add_user(self, content: str):
        self._messages.append(Message(Role.USER, content))
        self._trim_if_needed()  # Tự động trim khi vượt budget

    def _trim_if_needed(self):
        while self.total_tokens > self._max_tokens:
            # Xóa message cũ nhất (trừ system message)
            remove_oldest_non_system()
```

### Thứ tự ưu tiên trong context:

```
[SYSTEM PROMPT]      ← Luôn giữ (định hướng behavior)
[RECENT MESSAGES]    ← Giữ N messages gần nhất
[TOOL RESULTS]       ← Chỉ giữ relevant results

→ "Lost in the Middle" problem: LLM ít chú ý giữa context
→ Đặt thông tin quan trọng ở ĐẦU và CUỐI context
```

### Token estimation

```python
# Rough estimation (không cần API call)
def estimate_tokens(text: str) -> int:
    return len(text) // 3  # ~3 chars/token (EN+VI mixed)

# Accurate counting (cần API call — tốn tiền)
tokens = genai.count_tokens(text)
```

---

## 6. SmartMapper — Gợi ý mapping thông minh

### 4-Pass Algorithm

```
Pass 1: Exact Match     (id → id)                    confidence: 1.0
Pass 2: Normalized Match (userName → user_name)       confidence: 0.9
Pass 3: Semantic Match   (deletedAt → is_deleted)     confidence: 0.75
Pass 4: AI Match         (unknown_col → target_col)   confidence: varies
```

### Semantic Groups

```python
# Rule-based semantic equivalents
_SEMANTIC_MAP = {
    frozenset({"deletedat", "is_deleted", "softdelete"}): "soft_delete",
    frozenset({"createdat", "created_at", "createtime"}): "created_at",
    frozenset({"username", "user_name", "login"}): "username",
    ...
}
```

### AI Enhancement (chỉ cho unmatched)

```python
# Chỉ gọi AI cho columns CHƯA match được
# Tiết kiệm API calls = tiết kiệm tiền
if remaining_source and remaining_target and self._llm:
    ai_matches = self._ai_match(remaining_source, remaining_target)
```

### Confidence Score

| Score | Ý nghĩa | Khuyến nghị |
|:---|:---|:---|
| 1.0 | Exact match | Auto-apply |
| ≥ 0.85 | High confidence | Review briefly |
| ≥ 0.7 | Medium confidence | Review carefully |
| < 0.7 | Low confidence | Manual mapping |

---

## 7. AnomalyDetector — Kiểm tra trước khi migrate

### "Fail Fast" Principle

```
Phát hiện vấn đề TRƯỚC migration:
  ✅ Tiết kiệm thời gian (không cần rollback)
  ✅ Bảo vệ target database
  ✅ Rõ ràng: biết chính xác vấn đề ở đâu

Phát hiện vấn đề SAU migration (migrate rồi mới biết lỗi):
  ❌ Phải rollback tốn thời gian
  ❌ Target database có thể đã corrupt
  ❌ Khó debug hơn nhiều
```

### Các loại anomaly:

```python
# 1. Data Truncation Risk
# MySQL varchar(200) → PG varchar(50) nhưng có row dài 180 chars
detector.check_table_data(...)
# → CRITICAL: 5 rows sẽ bị truncate

# 2. NULL Violations
# PG column NOT NULL nhưng MySQL có 100 NULL rows
# → CRITICAL: 100 rows sẽ fail NOT NULL constraint

# 3. Date Issues
# MySQL 0000-00-00 không hợp lệ trong PostgreSQL
# → WARNING: 25 rows có invalid date

# 4. Encoding Issues (Mojibake)
# MySQL latin1 data được đọc như UTF-8 → garbage characters
# → WARNING: 10 rows có thể bị encoding error
```

### Severity levels:

```
CRITICAL → Sẽ gây lỗi migration (must fix)
WARNING  → Có thể gây lỗi (should fix)
INFO     → Thông tin tham khảo (nice to know)
```

---

## 8. ErrorExplainer — AI giải thích lỗi

### Pattern Matching + AI Enhancement

```python
# 1. Rule-based (fast, offline)
patterns = [
    _ErrorPattern(
        pattern=re.compile(r"null value in column.*violates not-null"),
        category=ErrorCategory.NOT_NULL,
        explanation="Cột bị NOT NULL nhưng có NULL trong source...",
        remediation=["Thêm DEFAULT value", "Remove NOT NULL constraint"]
    ),
    ...
]

# 2. AI Enhancement (cho lỗi phức tạp hoặc UNKNOWN)
if not matched or category == UNKNOWN:
    ai_explanation = llm.complete(
        f"Giải thích lỗi này: {error_text}"
    )
```

### PostgreSQL Error Categories:

| Category | Trigger | Fix |
|:---|:---|:---|
| `FOREIGN_KEY` | FK reference không tồn tại | Migrate parent table trước |
| `NOT_NULL` | NULL vào NOT NULL column | Thêm DEFAULT hoặc transform |
| `UNIQUE` | Duplicate key | Dùng UPSERT strategy |
| `TYPE_MISMATCH` | Wrong data type | Thêm value_transform |
| `OVERFLOW` | Value quá lớn | Tăng column length |
| `CONNECTION` | Cannot connect | Check host/port/credentials |
| `PERMISSION` | No access | GRANT permissions |

---

## 9. Tích hợp vào Flask API

### 4 Agent Endpoints:

```
POST /api/agent/chat           → Chat với AI Assistant
POST /api/agent/analyze        → Phân tích anomalies
POST /api/agent/suggest-mapping → Gợi ý column mapping
POST /api/agent/explain-error   → Giải thích lỗi migration
```

### Lazy Initialization Pattern:

```python
def _get_agent(schema_cache=None):
    """Tạo agent mỗi request — không cache state."""
    from db_migrator.agent import MigrationAgent, GeminiProvider
    provider = GeminiProvider()  # Đọc GEMINI_API_KEY từ env
    return MigrationAgent(llm_provider=provider)
```

**Tại sao không singleton?**
- Request độc lập nhau → không nên share state
- GeminiProvider lightweight → tạo mới rất nhanh
- Tránh race conditions trong multi-threading

### Graceful Degradation:

```python
# Nếu không có GEMINI_API_KEY → dùng rule-based
provider = None
if os.environ.get('GEMINI_API_KEY'):
    provider = GeminiProvider()

mapper = SmartMapper(llm_provider=provider)  # Fallback sang rule-based
```

---

## 10. So sánh Python vs .NET Semantic Kernel

### Kiến trúc tương đồng

| Concept | Python (chúng ta) | .NET Semantic Kernel |
|:---|:---|:---|
| **Agent** | `MigrationAgent` | `ChatCompletionAgent` |
| **Tool** | `Tool` + `ToolRegistry` | `[KernelFunction]` + Plugin |
| **Memory** | `ConversationMemory` | `ChatHistory` |
| **LLM Provider** | `LLMProvider` ABC | `ITextGenerationService` |
| **ReAct Loop** | Manual implementation | `AutoFunctionInvocationFilter` |

### .NET Code tương đương:

```csharp
// 1. Khai báo Plugin (= Tool trong Python)
public class MigrationPlugin
{
    [KernelFunction]
    [Description("Phân tích schema của một bảng database")]
    public async Task<string> AnalyzeSchemaAsync(
        [Description("Tên bảng cần phân tích")] string tableName)
    {
        var schema = await _discoveryService.GetSchemaAsync(tableName);
        return JsonSerializer.Serialize(schema);
    }
}

// 2. Setup Kernel (= ToolRegistry + LLMProvider)
var builder = Kernel.CreateBuilder();
builder.AddGoogleAIGeminiChatCompletion("gemini-2.0-flash", apiKey);
builder.Plugins.AddFromType<MigrationPlugin>("migration");
var kernel = builder.Build();

// 3. Chat với auto function calling (= ReAct Loop)
var chatHistory = new ChatHistory();
chatHistory.AddSystemMessage("Bạn là Migration Assistant...");
chatHistory.AddUserMessage("Phân tích bảng users");

var settings = new GeminiPromptExecutionSettings
{
    ToolCallBehavior = ToolCallBehavior.AutoInvokeKernelFunctions,
    MaxTokens = 2048,
};

var response = await chat.GetChatMessageContentAsync(
    chatHistory,
    settings,
    kernel
);
Console.WriteLine(response.Content);
```

### So sánh chi tiết:

| Feature | Python | .NET SK |
|:---|:---|:---|
| **Token counting** | Manual (~3 chars/token) | `kernel.CountTokensAsync()` |
| **Memory trimming** | Custom sliding window | `ChatHistory.TrimMessages()` |
| **Streaming** | Manual | `IAsyncEnumerable<StreamingChatMessageContent>` |
| **Vector memory** | Custom | `KernelMemory` (Qdrant, Azure AI Search) |
| **Planners** | Manual ReAct | `FunctionCallingStepwisePlanner` |

### Khi nào dùng cái nào?

```
Python:
  ✅ Team Python, data-heavy workloads
  ✅ Cần flexibility, custom tools
  ✅ Integration với pandas, numpy, SQLAlchemy

.NET Semantic Kernel:
  ✅ Team .NET, enterprise applications
  ✅ Integration với Azure services
  ✅ Blazor/MAUI UI với streaming
```

---

## Tóm tắt

```
AI Agent Architecture:
  ├── LLMProvider (Strategy Pattern)
  │   ├── GeminiProvider (Production)
  │   └── MockLLMProvider (Testing)
  │
  ├── ToolRegistry (Command Pattern)
  │   ├── analyze_schema
  │   ├── list_tables
  │   ├── get_column_mapping
  │   └── validate_config
  │
  ├── ConversationMemory (Sliding Window)
  │   ├── Token budget management
  │   └── Trim strategies
  │
  └── MigrationAgent (ReAct Loop)
      ├── Reason (LLM thinks)
      ├── Act (Execute tool)
      └── Observe (Add result to context)

Smart Features (Rule-based + AI):
  ├── SmartMapper: 4-pass matching (exact/norm/semantic/ai)
  ├── AnomalyDetector: Pre-migration quality checks
  └── ErrorExplainer: Pattern matching + AI explanation
```

---

*Tài liệu này là một phần của chuỗi học tập LongPd.DynamicDBMigrator.*
*Xem thêm: [02_security_hardening.md](02_security_hardening.md)*
