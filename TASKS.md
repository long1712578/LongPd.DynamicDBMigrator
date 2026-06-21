# Danh sách Tác vụ Phát triển (Zero → Pro DB Migrator)

> [!NOTE]
> File này dùng để theo dõi tiến độ chi tiết từng step trong kế hoạch. Trạng thái ký hiệu:
> - `[ ]` Chưa bắt đầu
> - `[/]` Đang thực hiện
> - `[x]` Đã hoàn thành

---

## Phase 1: Nền tảng & Engineering Discipline 🏗️
- [x] **1.1 Cấu trúc hạ tầng kiểm thử (Testing Infrastructure)**
  - [x] Tạo file `requirements.txt` định nghĩa thư viện (bao gồm `google-generativeai`)
  - [x] Tạo file `tests/__init__.py` và `tests/conftest.py` với Mock DB connections
  - [x] Viết bộ test suite cho `MigrationConfig` (`tests/test_config.py`)
  - [x] Viết bộ test suite cho `SQLFileParser` (`tests/test_sql_parser.py`)
  - [x] Viết bộ test suite cho `TypeMapper` (`tests/test_type_mapper.py`)
  - [x] Viết bộ test suite cho `ValueConverter` (`tests/test_value_converter.py`)
  - [x] Viết bộ test suite cho `SchemaDiscovery` (`tests/test_discovery.py`)
  - [x] Viết bộ test suite cho Flask API (`tests/test_web_api.py`)
  - [x] Viết bộ test suite cho `DatabaseMigrator` (`tests/test_migrator.py`)
- [x] **1.2 Công cụ chất lượng mã nguồn (Code Quality Tooling)**
  - [x] Tạo file cấu hình Ruff, Pytest và Bandit (`pyproject.toml`)
  - [x] Thiết lập `Makefile` chạy nhanh cho dev: `make test`, `make lint`, `make security`
- [x] **1.3 Khung Kỹ năng Agent (Skills Framework)**
  - [x] Tạo thư mục `.skills/` và các kỹ năng: `SKILL-planning.md`, `SKILL-tdd.md`, `SKILL-security-review.md`, `SKILL-migration-debugging.md`
  - [x] Cập nhật file `AGENTS.md` giới thiệu framework mới
  - [x] Tạo file `CONTEXT.md` định nghĩa thuật ngữ toàn bộ dự án
- [x] **1.4 Tài liệu tự học Phase 1**
  - [x] Tạo file `learning/01_testing_and_tdd.md` hướng dẫn step-by-step TDD, viết mock test, cấu hình ruff/bandit

---

## Phase 2: Thắt chặt bảo mật (Cyber Security) 🔒
- [ ] **2.1 Chống SQL Injection**
  - [ ] Chuyển đổi các câu lệnh SQL tĩnh/f-string trong `migrator.py` sang parameterized queries với `psycopg2.sql`
  - [ ] Viết hàm sanitize và validate định danh bảng/cột (`_sanitize_identifier`)
  - [ ] Chuyển đổi MySQL query & SQL Parser sang parameterized input
- [ ] **2.2 Quản lý Credential**
  - [ ] Tạo module Vault `db_migrator/security.py` sử dụng mã hóa đối xứng Fernet
  - [ ] Viết template `.env.example` và cập nhật `.gitignore` ẩn các file nhạy cảm
- [ ] **2.3 Ghi nhật ký hệ thống (Audit Trail & Logging)**
  - [ ] Tạo file `db_migrator/audit.py` hỗ trợ log sự kiện dạng JSON Lines (`audit.jsonl`)
  - [ ] Tích hợp Logging Middleware vào Flask `web/app.py`, cấu hình CORS và các Security Headers
- [ ] **2.4 Tài liệu tự học Phase 2**
  - [ ] Tạo file `learning/02_security_hardening.md` giải thích SQL Injection, Fernet encryption, và thiết kế Audit Trail

---

## Phase 3: Tích hợp AI Agent (Smart Migration Assistant) 🤖
- [ ] **3.1 Kiến trúc cốt lõi của Agent**
  - [ ] Tạo module `db_migrator/agent/core.py` với ReAct agent loop
  - [ ] Xây dựng Tool Registry `db_migrator/agent/tools.py` cung cấp các API nội bộ cho agent
  - [ ] Cài đặt `GeminiProvider` sử dụng thư viện SDK `google-generativeai`
  - [ ] Thiết kế cơ chế tối ưu hóa context và nén token trong `db_migrator/agent/memory.py`
- [ ] **3.2 Tính năng thông minh**
  - [ ] Viết tool gợi ý ánh xạ tự động `db_migrator/agent/smart_mapper.py`
  - [ ] Viết tool phát hiện bất thường dữ liệu trước di chuyển `db_migrator/agent/anomaly_detector.py`
  - [ ] Viết tool phân tích và sửa lỗi di trú `db_migrator/agent/error_explainer.py`
- [ ] **3.3 Tích hợp Flask API**
  - [ ] Tạo các API endpoints tương tác agent: `/api/agent/chat`, `/api/agent/analyze`, `/api/agent/suggest-fix`
- [ ] **3.4 Tài liệu tự học Phase 3**
  - [ ] Tạo file `learning/03_ai_agent_integration.md` giải thích cơ chế ReAct Loop, Tool call và Token Optimization

---

## Phase 4: Quy trình tự vá lỗi SonarQube CI/CD 🔍
- [ ] **4.1 GitHub Workflows**
  - [ ] Thiết lập CI Pipeline chạy kiểm thử tự động (`.github/workflows/ci.yml`)
  - [ ] Thiết lập workflow tự động sửa lỗi dựa trên cron/event (`.github/workflows/sonar-autofix.yml`)
  - [ ] Thiết lập workflow sửa lỗi qua comment `/sonar` (`.github/workflows/sonar-fix-comment.yml`)
  - [ ] Thiết lập workflow đồng bộ hóa sau khi merge PR (`.github/workflows/sonar-fix-merged.yml`)
- [ ] **4.2 Cấu hình SonarQube**
  - [ ] Tạo file cấu hình `sonar-project.properties` cho Python
- [ ] **4.3 Tác nhân tự động vá lỗi (Sonar Fix Agent)**
  - [ ] Xây dựng Fixer Agent thực thi sửa đổi code (`tools/sonar_agent/fixer.py`)
  - [ ] Xây dựng Reviewer Agent đánh giá kết quả sửa lỗi (`tools/sonar_agent/reviewer.py`)
  - [ ] Xây dựng Reconciler đồng bộ hóa giữa SonarQube và GitHub (`tools/sonar_agent/reconciler.py`)
- [ ] **4.4 Tài liệu tự học Phase 4**
  - [ ] Tạo file `learning/04_sonarqube_cicd.md` hướng dẫn thiết lập SonarQube cục bộ qua Docker và cơ chế Auto-Fix agent

---

## Phase 5: Nâng cao năng lực Agent & Tối ưu hóa Token 🧠
- [ ] **5.1 Hệ thống kỹ năng tự động kết nối (Composable Skills)**
  - [ ] Định nghĩa lớp cơ sở `Skill` và bộ quản lý đăng ký `SkillRegistry`
  - [ ] Xây dựng các Skill: phân tích schema, profile dữ liệu, lập kế hoạch di chuyển, chiến lược rollback, tối ưu hiệu suất
- [ ] **5.2 Tối ưu hóa Token nâng cao**
  - [ ] Xây dựng bộ tối ưu hóa token `db_migrator/agent/token_optimizer.py` (nén schema, fingerprint, delta context)
- [ ] **5.3 Tài liệu tự học Phase 5**
  - [ ] Tạo file `learning/05_skills_and_tokens.md` giải thích cấu trúc Composable Skills và các thuật toán tối ưu hóa Token

---

## Phase 6: Đóng gói ứng dụng & Sẵn sàng cho cộng đồng 🌍
- [ ] **6.1 Tài liệu chuẩn Open Source**
  - [ ] Viết lại `README.md` chất lượng cao bằng 2 ngôn ngữ Việt-Anh kèm sơ đồ kiến trúc Mermaid
  - [ ] Tạo các file: `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `LICENSE`, `CHANGELOG.md`
- [ ] **6.2 Giao diện dòng lệnh CLI**
  - [ ] Xây dựng script `cli.py` cung cấp đầy đủ commands thay thế Web UI
- [ ] **6.3 Đóng gói Docker & Compose**
  - [ ] Tạo `Dockerfile` tối ưu hóa môi trường chạy python
  - [ ] Cấu hình `docker-compose.yml` liên kết MySQL, PostgreSQL và SonarQube Community Edition
- [ ] **6.4 Cấu hình Packaging**
  - [ ] Cấu hình `pyproject.toml` để sẵn sàng đẩy lên PyPI
- [ ] **6.5 Tài liệu tự học Phase 6**
  - [ ] Tạo file `learning/06_cli_and_docker.md` hướng dẫn viết CLI bằng argparse, đóng gói Docker và chuẩn bị deploy VPS

---

## Phase 7: Giao diện Pro Dashboard & Trực quan hóa 🎨
- [ ] **7.1 Nâng cấp giao diện Web**
  - [ ] Tái cấu trúc CSS/HTML với Glassmorphism và Dark Mode cao cấp (`web/templates/index.html`)
  - [ ] Bổ sung biểu đồ trực quan hóa tiến trình thời gian thực, bảng phân tích schema kéo thả, khung chat AI Agent
- [ ] **7.2 Tương tác thời gian thực với WebSockets**
  - [ ] Tích hợp SocketIO vào Flask API truyền tải thông điệp chat AI dạng streaming
- [ ] **7.3 Tài liệu tự học Phase 7**
  - [ ] Tạo file `learning/07_dashboard_ui.md` hướng dẫn vẽ UI glassmorphism, biểu đồ js, và truyền tải WebSocket streaming
