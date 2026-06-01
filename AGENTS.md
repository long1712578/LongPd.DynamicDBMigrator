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
- Install dependencies (no committed requirements file at the moment):
  - `pip install flask mysql-connector-python psycopg2-binary python-dotenv`
- Start web app:
  - `python run_web.py`
- Default UI URL:
  - `http://localhost:5000`

## Architecture Boundaries
- `db_migrator/`: reusable migration library
  - `config.py`: load/save and query mapping config
  - `discovery.py`: schema discovery + mapping suggestions
  - `sql_parser.py`: SQL dump parsing into table data
  - `migrator.py`: migration engine (`truncate_insert`, `upsert`, `append`)
  - `type_mapper.py`, `value_converter.py`: type/value conversion logic
- `web/`: Flask app for discovery, mapping, and async migration execution
- `alldatapostgre/`: SQL dump input folder used by file-based flow

## Conventions
- Keep migration behavior config-driven; prefer editing `mapping_config.json` over hardcoding conversion rules.
- Preserve existing API payload keys and response shape in `web/app.py` unless asked to introduce a breaking change.
- Keep user-facing messages compatible with current language usage (Vietnamese is present across UI/API messages).
- Prefer incremental, minimal edits; avoid broad refactors unless explicitly requested.

## Safety and Pitfalls
- There is no test suite in the repo; validate changes with targeted manual checks.
- `_migration_tasks` in `web/app.py` is an in-memory dict for background tasks; treat it as single-process state.
- `run_web.py` currently starts Flask with `debug=True`; do not change runtime mode unless requested.
- Avoid changing mapping key formats such as `source.col -> target.col` in `value_transforms`.

## Quick Validation Checklist
- For web/API changes: start with `python run_web.py` and confirm index route loads.
- For mapping/engine changes: verify a small migration path using existing `mapping_config.json` conventions.
- For config writes: ensure `mapping_config.json` remains valid JSON and preserves existing keys.

## Where to Find Details
- Full user workflow and troubleshooting: `README.md`
- Live configuration examples: `mapping_config.json`
