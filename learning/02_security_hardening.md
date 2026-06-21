# 🔒 02. Security Hardening — Bảo mật ứng dụng Database Migration

> **Mục tiêu**: Hiểu và áp dụng các kỹ thuật bảo mật enterprise-grade cho ứng dụng Python làm việc với database. Kiến thức này áp dụng trực tiếp cho cả Python và .NET projects.

---

## Mục lục

1. [SQL Injection — Mối đe dọa số 1](#1-sql-injection)
2. [Parameterized Queries & Identifier Sanitization](#2-parameterized-queries)
3. [Credential Management — Quản lý mật khẩu an toàn](#3-credential-management)
4. [Audit Trail — Nhật ký bất biến](#4-audit-trail)
5. [Security Headers cho Web API](#5-security-headers)
6. [Path Traversal Prevention](#6-path-traversal)
7. [Checklist Enterprise Security](#7-checklist)
8. [So sánh Python vs .NET](#8-python-vs-net)

---

## 1. SQL Injection — Mối đe dọa số 1

### 🔴 Vấn đề: f-string SQL

SQL Injection là **OWASP Top 10** — lỗ hổng bảo mật nguy hiểm nhất trong lịch sử lập trình.

```python
# ❌ SAI — SQL INJECTION RISK
table_name = request.json["table"]  # Attacker kiểm soát giá trị này!
sql = f"SELECT * FROM {table_name}"

# Attacker gửi: table_name = "users; DROP TABLE users; --"
# SQL thực thi: SELECT * FROM users; DROP TABLE users; --
```

### 🟢 Giải pháp 1: Parameterized Queries (Values)

```python
# ✅ ĐÚNG — Values (dữ liệu) luôn dùng %s placeholder
cursor.execute(
    "INSERT INTO users (name, email) VALUES (%s, %s)",
    (user_name, user_email)  # psycopg2 tự escape
)
```

### 🟢 Giải pháp 2: psycopg2.sql cho Identifiers

**Lý do**: Python DB-API (`%s`) chỉ parameterize VALUES, không parameterize tên bảng/cột.
Dùng `psycopg2.sql.Identifier` để escape identifier một cách an toàn.

```python
from psycopg2 import sql

# ✅ ĐÚNG — Identifier escape cho tên bảng/cột
schema = "public"
table = "users"
columns = ["id", "name", "email"]

insert_sql = sql.SQL("INSERT INTO {schema}.{table} ({cols}) VALUES ({vals})").format(
    schema=sql.Identifier(schema),
    table=sql.Identifier(table),
    cols=sql.SQL(", ").join(sql.Identifier(c) for c in columns),
    vals=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
)
cursor.execute(insert_sql, (1, "Long", "long@example.com"))
# Tự động tạo: INSERT INTO "public"."users" ("id","name","email") VALUES (%s,%s,%s)
```

### 🟢 Giải pháp 3: Whitelist Identifier Validation

```python
import re

_SAFE_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

def sanitize_identifier(name: str) -> str:
    """Chỉ cho phép ký tự an toàn — tất cả còn lại bị từ chối."""
    if not _SAFE_PATTERN.match(name):
        raise ValueError(f"Invalid identifier: '{name}' — SQL injection attempt?")
    return name

# Test:
sanitize_identifier("users")         # ✅ OK
sanitize_identifier("my_table_v2")   # ✅ OK
sanitize_identifier("users; DROP")   # ❌ ValueError!
sanitize_identifier("../etc/passwd") # ❌ ValueError!
```

### 💡 Tại sao Whitelist > Blacklist?

| Phương pháp | Ví dụ | Vấn đề |
|:---|:---|:---|
| **Blacklist** (chặn ký tự nguy hiểm) | Chặn `'`, `;`, `--` | Attacker tìm ký tự bỏ sót |
| **Whitelist** (chỉ cho phép ký tự an toàn) | Chỉ `[a-zA-Z0-9_]` | Mọi thứ khác đều bị chặn |

**Nguyên tắc**: Luôn dùng Whitelist cho security validation!

---

## 2. Parameterized Queries

### Tóm tắt bảng kỹ thuật

| Loại dữ liệu | Kỹ thuật | Ví dụ |
|:---|:---|:---|
| **Values** (dữ liệu thường) | `%s` placeholder | `execute("INSERT ... VALUES (%s)", (value,))` |
| **Identifiers** (tên bảng/cột) | `psycopg2.sql.Identifier` | `sql.Identifier("users")` → `"users"` |
| **Dynamic SQL** | `psycopg2.sql.SQL` | `sql.SQL("SELECT * FROM {t}").format(t=Identifier("users"))` |

### Batch Insert với execute_values (5x nhanh hơn!)

```python
from psycopg2.extras import execute_values

# ✅ ĐÚNG + NHANH — execute_values thay vì loop cursor.execute
insert_sql = sql.SQL("INSERT INTO {s}.{t} ({cols}) VALUES %s").format(
    s=sql.Identifier("public"),
    t=sql.Identifier("users"),
    cols=sql.SQL(", ").join(sql.Identifier(c) for c in ["id", "name"]),
)
execute_values(cursor, insert_sql, [(1, "Long"), (2, "Nam")])
# Tự động batch thành: INSERT INTO "public"."users" ("id","name") VALUES (1,'Long'),(2,'Nam')
```

---

## 3. Credential Management — Quản lý mật khẩu an toàn

### ❌ Sai lầm phổ biến

```python
# ❌ NEVER — Hardcode credentials trong code
conn = psycopg2.connect(host="localhost", password="mypassword123")

# ❌ NEVER — Commit credentials vào Git
# config.json: { "password": "mypassword123" }

# ❌ NEVER — In credentials ra log
logger.info(f"Connecting to {host} with password {password}")
```

### ✅ Kiến trúc 3 lớp (Priority Order)

```
Layer 1: Environment Variables    ← Production, Docker, CI/CD
Layer 2: .env File               ← Local development
Layer 3: Encrypted Vault File    ← Backup / offline
```

### Layer 1: Environment Variables

```bash
# Linux/macOS
export PG_HOST=localhost
export PG_PASSWORD=supersecret

# Windows PowerShell
$env:PG_HOST = "localhost"
$env:PG_PASSWORD = "supersecret"

# Docker
docker run -e PG_HOST=localhost -e PG_PASSWORD=supersecret myapp
```

```python
import os

config = {
    "host": os.environ["PG_HOST"],      # KeyError nếu không có
    "host": os.environ.get("PG_HOST"),  # None nếu không có
}
```

### Layer 2: .env File với python-dotenv

```bash
# .env (KHÔNG commit lên Git!)
PG_HOST=localhost
PG_PORT=5432
PG_PASSWORD=supersecret
```

```python
from dotenv import load_dotenv
load_dotenv(".env")  # Load vào os.environ
config = {"host": os.environ.get("PG_HOST")}
```

### Layer 3: Fernet Encryption (AES-128-CBC + HMAC-SHA256)

```python
from cryptography.fernet import Fernet

# Bước 1: Tạo key (chỉ làm 1 lần, lưu vào VAULT_KEY env)
key = Fernet.generate_key()  # b'...' — 44 bytes base64
print(key.decode())  # Lưu vào .env: VAULT_KEY=...

# Bước 2: Mã hóa
fernet = Fernet(key)
plaintext = b'{"password": "supersecret"}'
encrypted = fernet.encrypt(plaintext)  # bytes — safe to store

# Bước 3: Giải mã
decrypted = fernet.decrypt(encrypted)  # → plaintext bytes
```

**Fernet đảm bảo**:
- 🔐 **Confidentiality**: AES-128-CBC — không đọc được nếu không có key
- 🛡️ **Integrity**: HMAC-SHA256 — phát hiện nếu data bị sửa
- ⏱️ **Freshness**: Timestamp — phát hiện replay attacks

---

## 4. Audit Trail — Nhật ký bất biến

### Tại sao cần Audit Trail?

Audit Trail (nhật ký kiểm toán) là yêu cầu bắt buộc trong:
- **ISO 27001**: Information Security Management
- **PCI DSS**: Payment Card Industry Data Security
- **GDPR Article 30**: Records of Processing Activities
- **SOC 2 Type II**: Service Organization Control

### Thiết kế JSON Lines Format

```jsonl
{"timestamp":"2026-06-21T08:00:00Z","log_id":"a1b2c3d4","event":"migration.start","task_id":"abc-123","tables":["users"],"strategy":"upsert"}
{"timestamp":"2026-06-21T08:00:15Z","log_id":"e5f6g7h8","event":"migration.complete","task_id":"abc-123","duration_seconds":15.3,"total_rows_migrated":1000}
```

**Tại sao JSON Lines > JSON Array?**

| | JSON Array | JSON Lines (NDJSON) |
|:---|:---|:---|
| **Append** | Phải đọc rồi rewrite toàn file | Chỉ `f.write(line + "\n")` |
| **Crash safety** | Corrupt nếu crash giữa chừng | Các dòng trước vẫn valid |
| **Memory** | Load cả file vào RAM | Stream từng dòng |
| **Tools** | Custom parser | `jq`, Elasticsearch, Splunk |

### Nguyên tắc Logging an toàn

```python
# ❌ NEVER — Log credentials hoặc sensitive data
logger.info(f"Connecting with password={password}")
audit.log({"event": "connect", "password": password})

# ✅ ĐÚNG — Che thông tin nhạy cảm
logger.info(f"Connecting to {host}:{port}/{database}")
audit.log({"event": "connect", "host": host, "user": user})

# ✅ ĐÚNG — Sanitize trước khi log error messages
def sanitize_error(msg: str) -> str:
    import re
    return re.sub(r"(password|key|token)=\S+", r"\1=***", msg, flags=re.I)
```

---

## 5. Security Headers cho Web API

### Tại sao cần Security Headers?

HTTP Security Headers là lớp bảo vệ thứ 2 (defense in depth):

```python
@app.after_request
def add_security_headers(response):
    # Ngăn Clickjacking: trang web không thể bị nhúng vào iframe
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'

    # Ngăn MIME type sniffing (trình duyệt đoán sai loại file)
    response.headers['X-Content-Type-Options'] = 'nosniff'

    # Content Security Policy — chặn inline script injection (XSS)
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline';"
    )

    # Kiểm soát thông tin Referrer gửi đi
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    # Tắt Web APIs không cần thiết
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

    return response
```

### Headers giải thích

| Header | Mục đích | Tấn công ngăn chặn |
|:---|:---|:---|
| `X-Frame-Options` | Chặn iframe embedding | Clickjacking |
| `X-Content-Type-Options` | Chặn MIME sniffing | Content injection |
| `Content-Security-Policy` | Whitelist nguồn tài nguyên | XSS |
| `Referrer-Policy` | Kiểm soát Referer header | Information leakage |
| `Permissions-Policy` | Tắt Web APIs | Browser feature abuse |

---

## 6. Path Traversal Prevention

### ❌ Tấn công Path Traversal

```python
# ❌ SAI — Attacker có thể gửi filename = "../../etc/passwd"
filename = request.json["filename"]
filepath = os.path.join("/uploads", filename)
with open(filepath) as f:
    content = f.read()  # Đọc /etc/passwd!
```

### ✅ Giải pháp: resolve() + relative_to()

```python
from pathlib import Path

def safe_open_file(filename: str, allowed_dir: str) -> str:
    """Đảm bảo file nằm trong allowed_dir."""
    resolved = Path(allowed_dir, filename).resolve()
    base = Path(allowed_dir).resolve()

    try:
        resolved.relative_to(base)  # Throw ValueError nếu ngoài base
    except ValueError as e:
        raise PermissionError(f"Path traversal detected: {filename}") from e

    return resolved.read_text()

# Test:
safe_open_file("backup.sql", "/uploads")          # ✅ OK
safe_open_file("../../etc/passwd", "/uploads")    # ❌ PermissionError
safe_open_file("../secret.txt", "/uploads")       # ❌ PermissionError
```

---

## 7. Checklist Enterprise Security ✅

### Trước khi deploy (Pre-deployment Checklist)

- [ ] Không có f-string SQL trong code
- [ ] Tất cả identifiers đều qua `sanitize_identifier()`
- [ ] Credentials không hardcode — dùng env vars hoặc vault
- [ ] File `.env` có trong `.gitignore`
- [ ] Audit logging được kích hoạt
- [ ] Security headers được cấu hình
- [ ] Input validation cho tất cả API endpoints
- [ ] Bandit scan pass với 0 HIGH/MEDIUM issues
- [ ] `pytest` pass với coverage ≥ 80%

### Mỗi PR (Code Review Checklist)

- [ ] Kiểm tra SQL queries có dùng parameterized không?
- [ ] Kiểm tra error messages không chứa credentials?
- [ ] Kiểm tra file paths có được validate không?
- [ ] Kiểm tra có log gì nhạy cảm không?

---

## 8. So sánh Python vs .NET

### SQL Security

| Feature | Python (psycopg2) | .NET (EF Core / Dapper) |
|:---|:---|:---|
| **Parameterized Values** | `cursor.execute(sql, (val,))` | `cmd.Parameters.AddWithValue("@val", val)` |
| **Identifier Escape** | `sql.Identifier("users")` | `[users]` hoặc `"users"` (bracket notation) |
| **ORM** | SQLAlchemy | Entity Framework Core |
| **Raw SQL** | psycopg2 + sql.SQL | Dapper |

```csharp
// .NET Dapper — parameterized query
var users = conn.Query<User>(
    "SELECT * FROM Users WHERE Id = @Id",
    new { Id = userId }  // ✅ Parameterized — safe
);

// .NET — identifier escape (không có built-in như psycopg2.sql)
// Dùng SqlCommandBuilder.QuoteIdentifier:
var quotedTable = new SqlCommandBuilder().QuoteIdentifier(tableName);
// Hoặc tự validate: [a-zA-Z_][a-zA-Z0-9_]*
```

### Credential Management

| | Python | .NET |
|:---|:---|:---|
| **ENV vars** | `os.environ.get()` | `Environment.GetEnvironmentVariable()` |
| **Config file** | `.env` + python-dotenv | `appsettings.json` + `IConfiguration` |
| **Secrets (dev)** | `.env` (gitignored) | `dotnet user-secrets` |
| **Secrets (prod)** | Environment vars / Vault | Azure Key Vault / AWS Secrets Manager |
| **Encryption** | `cryptography.fernet` | `System.Security.Cryptography` / DPAPI |

```csharp
// .NET — IConfiguration (3-layer pattern)
var builder = WebApplication.CreateBuilder(args);
builder.Configuration
    .AddJsonFile("appsettings.json")       // Layer 2: config file
    .AddEnvironmentVariables()             // Layer 1: ENV (overrides json)
    .AddUserSecrets<Program>();            // Layer 3: dev secrets

var dbPassword = builder.Configuration["Database:Password"];
```

### Audit Logging

| | Python | .NET |
|:---|:---|:---|
| **Structured logs** | JSON Lines (custom) | Serilog + JSON formatter |
| **Log levels** | `logging.INFO/WARNING/ERROR` | `ILogger.LogInformation/Warning/Error` |
| **Correlation ID** | UUID tự tạo | `Activity.Current.TraceId` (W3C Trace Context) |
| **Destinations** | File / stdout | File / Azure Monitor / Splunk |

```csharp
// .NET Serilog structured logging
Log.ForContext("TaskId", taskId)
   .Information("Migration started {Tables} strategy={Strategy}",
                tables, strategy);
// Output: {"@t":"...","@l":"Information","TaskId":"abc","Tables":["users"],"Strategy":"upsert"}
```

---

## Tóm tắt

```
SQL Injection Prevention:
  ├── Values      → %s placeholder (DB-API)
  ├── Identifiers → psycopg2.sql.Identifier
  └── Validation  → whitelist [a-zA-Z0-9_] only

Credential Management:
  ├── Layer 1: Environment Variables (production)
  ├── Layer 2: .env file (development)
  └── Layer 3: Fernet encrypted vault (backup)

Audit Trail:
  ├── Format: JSON Lines (append-only)
  ├── Content: timestamp, event, task_id, details
  └── Safety: sanitize sensitive data before logging

Web Security:
  ├── Security Headers (CSP, X-Frame-Options, ...)
  ├── Input Validation (required fields, type checking)
  └── Rate Limiting (prevent DoS)

Path Security:
  └── resolve() + relative_to() (prevent path traversal)
```

---

*Tài liệu này là một phần của chuỗi học tập LongPd.DynamicDBMigrator. Xem thêm: [01_testing_and_tdd.md](01_testing_and_tdd.md)*
