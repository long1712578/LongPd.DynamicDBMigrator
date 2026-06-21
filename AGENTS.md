# AGENTS.md

Instructions for AI coding agents working in this repository.

## Project Snapshot
- Python tool for dynamic migration from MySQL to PostgreSQL.
- Main usage is via Flask web UI and JSON-based mapping config.
- Core behavior is configuration-driven through `mapping_config.json`.

## Read First
- `README.md` (overview, workflow, troubleshooting; Vietnamese)
- `mapping_config.json` (real mapping + transform examples)
- `db_migrator/__init__.py` (public API surface)
- `db_migrator/migrator.py` (orchestration and strategies)
- `web/app.py` (HTTP API and background task execution)

## Setup and Run
- Python version: 3.8+
- Install dependencies:
  - `pip install -r requirements.txt`
- Start web app:
  - `python run_web.py`
- Default UI URL:
  - `http://localhost:5000`
- Dev tasks (Windows PowerShell):
  - `.\dev.ps1 test` (chạy test)
  - `.\dev.ps1 lint` (kiểm tra code)
  - `.\dev.ps1 format` (định dạng code)
  - `.\dev.ps1 all` (chạy tất cả kiểm tra)
- Dev tasks (Linux/macOS):
  - `make test`, `make lint`, `make all`

## Architecture Boundaries
- `db_migrator/`: reusable migration library
  - `config.py`: load/save and query mapping config
  - `discovery.py`: schema discovery + mapping suggestions
  - `sql_parser.py`: SQL dump parsing into table data
  - `migrator.py`: migration engine (`truncate_insert`, `upsert`, `append`)
  - `type_mapper.py`, `value_converter.py`: type/value conversion logic
- `web/`: Flask app for discovery, mapping, and async migration execution
- `alldatapostgre/`: SQL dump input folder used by file-based flow

## Skills & Framework
- Lập kế hoạch trước khi code: Xem thêm [.skills/SKILL-planning.md](file:///d:/Projects/MY-Dragon-agent/LongPd.DynamicDBMigrator/.skills/SKILL-planning.md)
- Phát triển TDD: Xem thêm [.skills/SKILL-tdd.md](file:///d:/Projects/MY-Dragon-agent/LongPd.DynamicDBMigrator/.skills/SKILL-tdd.md)
- Kiểm duyệt bảo mật: Xem thêm [.skills/SKILL-security-review.md](file:///d:/Projects/MY-Dragon-agent/LongPd.DynamicDBMigrator/.skills/SKILL-security-review.md)
- Gỡ lỗi migration: Xem thêm [.skills/SKILL-migration-debugging.md](file:///d:/Projects/MY-Dragon-agent/LongPd.DynamicDBMigrator/.skills/SKILL-migration-debugging.md)

## Conventions
- Keep migration behavior config-driven; prefer editing `mapping_config.json` over hardcoding conversion rules.
- Preserve existing API payload keys and response shape in `web/app.py` unless asked to introduce a breaking change.
- Keep user-facing messages compatible with current language usage (Vietnamese is present across UI/API messages).
- Prefer incremental, minimal edits; avoid broad refactors unless explicitly requested.

## Safety and Pitfalls
- **Test suite**: `pytest` tests are available in `tests/`. Luôn chạy `make test` để kiểm thử code.
- **Code Quality**: Chạy `make lint` để check Ruff và Bandit. Mọi lỗi hoặc cảnh báo an ninh (Bandit) phải được xử lý triệt để.
- `_migration_tasks` in `web/app.py` is an in-memory dict for background tasks; treat it as single-process state.
- `run_web.py` currently starts Flask with `debug=True`; do not change runtime mode unless requested.
- Avoid changing mapping key formats such as `source.col -> target.col` in `value_transforms`.

## Quick Validation Checklist
- Chạy `make all` trước khi commit PR. Đảm bảo toàn bộ test case vượt qua và độ bao phủ ≥80%.
- For web/API changes: start with `python run_web.py` and confirm index route loads.
- For mapping/engine changes: verify a small migration path using existing `mapping_config.json` conventions.
- For config writes: ensure `mapping_config.json` remains valid JSON and preserves existing keys.

## Where to Find Details
- Full user workflow and troubleshooting: `README.md`
- Live configuration examples: `mapping_config.json`
- Thuật ngữ chuyên môn: `CONTEXT.md`

