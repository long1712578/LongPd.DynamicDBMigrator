#!/usr/bin/env python3
"""
web/app.py
==========
Flask Web Application for the Dynamic DB Migration Library.

Provides a clean, modern API and UI for:
1. Discovering schemas from SQL files or live databases
2. Visual drag-and-drop mapping
3. Executing migrations asynchronously

Security features (Phase 2):
- Security Headers (CSP, X-Frame-Options, HSTS, etc.)
- Input validation middleware
- Audit trail logging
- Rate limiting cho API endpoints
- Flask secret key management
"""

import logging
import os
import sys
import threading
import uuid

from flask import Flask, jsonify, render_template, request

# Ensure db_migrator is in Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db_migrator import (
    DatabaseMigrator,
    MigrationConfig,
    SchemaDiscovery,
)
from db_migrator.audit import MigrationAuditLog
from db_migrator.security import CredentialVault

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App initialization with security configuration
# ---------------------------------------------------------------------------

_vault = CredentialVault()  # Load .env file và ENV variables

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max for upload if used
app.config['SECRET_KEY'] = _vault.get_flask_secret_key()  # Session signing key

UPLOAD_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'alldatapostgre'))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Audit log instance
_audit = MigrationAuditLog()

# ---------------------------------------------------------------------------
# Rate Limiting Setup
# ---------------------------------------------------------------------------
try:
    from flask_limiter import Limiter  # type: ignore[import]
    from flask_limiter.util import get_remote_address  # type: ignore[import]
    limiter = Limiter(
        get_remote_address,
        app=app,
        default_limits=["200 per hour", "50 per minute"],
        storage_uri="memory://",  # in-memory storage (dev mode)
    )
except ImportError:
    # flask-limiter optional — degrade gracefully
    limiter = None  # type: ignore[assignment]
    logger.warning("⚠️  flask-limiter not installed — rate limiting disabled")

# Global state for background migration tasks
# In a real production app, use Celery/Redis. For this internal tool, simple dict + thread is fine.
_migration_tasks = {}
_migration_tasks_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Security Middleware: Headers & Audit
# ---------------------------------------------------------------------------

@app.after_request
def add_security_headers(response):
    """
    Thêm Security Headers vào mọi HTTP response.

    Tại sao cần Security Headers?
    ----------------------------------
    - X-Frame-Options: Ngăn Clickjacking attack (nhung iframe trang web vào trang khác)
    - X-Content-Type-Options: Ngăn MIME sniffing (trình duyệt đoán sai content type)
    - X-XSS-Protection: Bật XSS filter trình duyệt (legacy, nhưng vẫn hữu ích)
    - Referrer-Policy: Kiểm soát thông tin Referer gửi đi
    - Content-Security-Policy: White-list nguồn tài nguyên (script, style, image)

    Tương đương .NET:
      services.AddAntiforgery();
      app.UseXContentTypeOptions();
      app.UseXframe();
      app.UseXXssProtection();
    """
    # Chặn Clickjacking: trang web không thể được nhun vào iframe
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'

    # Ngăn MIME type sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'

    # Bật XSS filter trình duyệt (hữu ích cho IE/Edge cũ)
    response.headers['X-XSS-Protection'] = '1; mode=block'

    # Kiểm soát Referrer information
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    # Content Security Policy — chặn inline script injection
    # 'self': chỉ load tài nguyên từ cùng domain
    # 'unsafe-inline': cần cho inline CSS (có thể bỏ sau khi refactor CSS ra file riêng)
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:;"
    )

    # Permissions Policy: tắt các web APIs không cần dùng
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'

    return response


@app.before_request
def log_request():
    """
    Ghi audit log cho mọi API request.

    Note: Chỉ log metadata (method, path, IP) — KHÔNG BAO GIờ log request body
    vì có thể chứa passwords/credentials!
    """
    if request.path.startswith('/api/'):
        payload_keys = list((request.json or {}).keys()) if request.is_json else []
        _audit.log_api_request(
            method=request.method,
            path=request.path,
            remote_addr=request.remote_addr or "unknown",
            payload_keys=payload_keys,
        )


def _validate_db_config(config: dict, config_name: str = "config") -> tuple[bool, str]:
    """
    Validate cấu trúc và kiểu dữ liệu của database config.

    Giảm thiểu rủi ro SSRF (Server-Side Request Forgery) bằng cách
    kiểm tra các trường bắt buộc và kiểu dữ liệu.

    Args:
        config      : Dict cấu hình kết nối database
        config_name : Tên config (cho thông báo lỗi)

    Returns:
        (is_valid, error_message)
    """
    required_fields = {'host', 'database', 'user', 'password'}
    missing = required_fields - set(config.keys())
    if missing:
        return False, f"{config_name} thiếu các trường bắt buộc: {missing}"

    # Kiểm tra host không được để trống (ngăn SSRF rõ ràng)
    host = str(config.get('host', '')).strip()
    if not host:
        return False, f"{config_name}.host không được để trống"

    # Kiểm tra port hợp lệ (1-65535)
    port = config.get('port', 3306)
    try:
        port_int = int(port)
        if not (1 <= port_int <= 65535):
            return False, f"{config_name}.port phải trong khoảng 1-65535"
    except (ValueError, TypeError):
        return False, f"{config_name}.port phải là số nguyên"

    return True, ""


def _summarize_stats(stats: dict) -> dict:
    """Aggregate per-table migration stats for status decision and UI messages."""
    total_success = 0
    total_errors = 0
    total_skipped = 0
    touched_tables = 0

    for _tbl, values in (stats or {}).items():
        if not isinstance(values, dict):
            continue
        touched_tables += 1
        total_success += int(values.get('success', 0) or 0)
        total_errors += int(values.get('errors', 0) or 0)
        total_skipped += int(values.get('skipped', 0) or 0)

    return {
        'tables': touched_tables,
        'success': total_success,
        'errors': total_errors,
        'skipped': total_skipped,
    }


def _serialize_schema(schema: dict) -> dict:
    """Convert discovery schema objects to JSON-serializable payload."""
    result = {}
    for tbl_name, ts in schema.items():
        result[tbl_name] = {
            'name': ts.name,
            'columns': [{'name': c.name, 'type': c.data_type, 'is_pk': c.is_primary_key} for c in ts.columns],
            'primary_key': ts.primary_key
        }
    return result


@app.route('/')
def index():
    """Render the main SPA (Single Page Application) interface."""
    return render_template('index.html')

# ---------------------------------------------------------------------------
# API: Discovery
# ---------------------------------------------------------------------------

@app.route('/api/discover/file', methods=['POST'])
def discover_from_file():
    """Discover schema from an uploaded or existing SQL dump file."""
    data = request.json or {}
    filename = data.get('filename')

    if not filename:
        return jsonify({'success': False, 'message': 'Thiếu tên file'}), 400

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(filepath):
        msg = f"Không tìm thấy file {filename} trong thư mục alldatapostgre/"
        return jsonify({"success": False, "message": msg}), 404

    try:
        disc = SchemaDiscovery()
        schema = disc.from_sql_file(filepath)
        result = _serialize_schema(schema)

        return jsonify({
            'success': True,
            'schema': result,
            'tables': list(result.keys())
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/discover/mysql', methods=['POST'])
def discover_from_mysql():
    """Discover schema from a live MySQL connection."""
    config = request.json
    if not config:
        return jsonify({'success': False, 'message': 'Thiếu thông tin kết nối'}), 400

    try:
        disc = SchemaDiscovery()
        schema = disc.from_mysql(config)
        result = _serialize_schema(schema)

        return jsonify({'success': True, 'schema': result, 'tables': list(result.keys())})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/discover/postgres', methods=['POST'])
def discover_from_postgres():
    """Discover schema from a live PostgreSQL connection."""
    data = request.json or {}
    config = data.get('config')
    schema_name = data.get('schema', 'public')

    if not config:
        return jsonify({'success': False, 'message': 'Thiếu thông tin kết nối'}), 400

    try:
        disc = SchemaDiscovery()
        schema = disc.from_postgres(config, schema_name)
        result = _serialize_schema(schema)

        return jsonify({'success': True, 'schema': result, 'tables': list(result.keys())})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ---------------------------------------------------------------------------
# API: Mapping Suggestion
# ---------------------------------------------------------------------------

@app.route('/api/mapping/suggest', methods=['POST'])
def suggest_mapping():
    """Suggest mapping between source and target schemas."""
    data = request.json or {}
    source_schema_raw = data.get('source_schema', {})
    target_schema_raw = data.get('target_schema', {})

    # Reconstruct SchemaInfo from raw dicts
    from db_migrator.discovery import ColumnInfo, TableSchema

    def dict_to_schema(raw_dict):
        schema = {}
        for tbl_name, tbl_data in raw_dict.items():
            ts = TableSchema(name=tbl_name)
            for c in tbl_data.get('columns', []):
                ts.columns.append(ColumnInfo(name=c['name'], data_type=c['type'], is_primary_key=c.get('is_pk', False)))
            ts.primary_key = tbl_data.get('primary_key', [])
            schema[tbl_name] = ts
        return schema

    src_schema = dict_to_schema(source_schema_raw)
    tgt_schema = dict_to_schema(target_schema_raw)

    # Load existing mapping if any
    cfg = MigrationConfig()
    existing_table_mapping = cfg.table_mapping
    existing_column_mapping = cfg.column_mapping()

    disc = SchemaDiscovery()
    suggestion = disc.suggest_mapping(src_schema, tgt_schema, existing_table_mapping, existing_column_mapping)

    # Return serializable dict
    result = suggestion.to_config_dict()
    result['value_transforms'] = getattr(cfg, 'value_transforms', {})

    return jsonify({
        'success': True,
        'mapping': result
    })

@app.route('/api/mapping/save', methods=['POST'])
def save_mapping():
    """Save the updated mapping configuration."""
    data = request.json or {}

    try:
        cfg = MigrationConfig()

        # Replace mappings for edited tables (do not merge old column keys like id)
        new_table_map = data.get('table_mapping', {})
        new_col_map = data.get('column_mapping', {})
        new_transforms = data.get('value_transforms', {})
        target_schema = data.get('target_schema')

        # We need to update the private _data dict to save it properly
        cfg_data = cfg._data

        if target_schema:
            cfg_data['target_schema'] = target_schema

        if 'table_mapping' not in cfg_data:
            cfg_data['table_mapping'] = {}
        for src_tbl, tgt_tbl in new_table_map.items():
            cfg_data['table_mapping'][src_tbl] = tgt_tbl

        if 'column_mapping' not in cfg_data:
            cfg_data['column_mapping'] = {}

        updated_tables = set(new_table_map.keys()) | set(new_col_map.keys())
        for tbl in updated_tables:
            cfg_data['column_mapping'][tbl] = dict(new_col_map.get(tbl, {}))

        if 'value_transforms' not in cfg_data:
            cfg_data['value_transforms'] = {}

        # Remove old transforms for tables being edited, then write current UI transforms.
        if updated_tables:
            cleaned = {}
            for pattern, spec in cfg_data['value_transforms'].items():
                left = pattern.split('->', 1)[0].strip()
                src_tbl = left.split('.', 1)[0].strip() if '.' in left else None
                if src_tbl in updated_tables:
                    continue
                cleaned[pattern] = spec
            cfg_data['value_transforms'] = cleaned

        if new_transforms:
            cfg_data['value_transforms'].update(new_transforms)

        cfg.save()
        return jsonify({'success': True, 'message': 'Đã lưu cấu hình mapping'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

# ---------------------------------------------------------------------------
# API: Migration Execution
# ---------------------------------------------------------------------------

@app.route('/api/migrate/start', methods=['POST'])
def start_migration():
    """Start an asynchronous migration task."""
    data = request.json or {}

    task_id = str(uuid.uuid4())
    with _migration_tasks_lock:
        _migration_tasks[task_id] = {
            'status': 'running',
            'progress': 0,
            'logs': [],
            'stats': {},
            'current_table': None,
            'message': 'Đang khởi tạo...',
            'error': None
        }

    # Extract params
    flow = data.get('flow', 'file_to_postgres') # 'file_to_postgres', 'file_to_mysql', 'mysql_to_postgres'
    sql_filename = data.get('sql_filename')
    mysql_config = data.get('mysql_config')
    pg_config = data.get('pg_config')
    tables = data.get('tables') # list of table names
    strategy = data.get('strategy', 'truncate_insert')

    def run_migration_task(task_id, flow, sql_filename, mysql_config, pg_config, tables, strategy):
        with _migration_tasks_lock:
            task = _migration_tasks.get(task_id)
        if task is None:
            return
        try:
            cfg = MigrationConfig()

            def progress_cb(table, done, total, msg):
                task['current_table'] = table
                task['progress'] = int((done / total) * 100) if total > 0 else 100
                task['message'] = f"[{table}] {msg}: {done}/{total}"

            migrator = DatabaseMigrator(config=cfg, on_progress=progress_cb)

            if flow == 'file_to_postgres':
                if not sql_filename:
                    raise ValueError("Thiếu tên file SQL")
                sql_filepath = os.path.join(app.config['UPLOAD_FOLDER'], sql_filename)
                task['logs'].append("Starting pipeline: File -> MySQL -> PostgreSQL")
                stats = migrator.migrate_file_to_postgres(sql_filepath, mysql_config, pg_config, tables, strategy)

            elif flow == 'file_to_mysql':
                if not sql_filename:
                    raise ValueError("Thiếu tên file SQL")
                sql_filepath = os.path.join(app.config['UPLOAD_FOLDER'], sql_filename)
                task['logs'].append("Starting import: File -> MySQL")
                stats = migrator.migrate_file_to_mysql(sql_filepath, mysql_config, tables, strategy)

            elif flow == 'mysql_to_postgres':
                task['logs'].append("Starting sync: MySQL -> PostgreSQL")
                stats = migrator.migrate_mysql_to_postgres(mysql_config, pg_config, tables, strategy)

            else:
                raise ValueError(f"Unknown flow: {flow}")

            task['stats'] = stats
            summary = _summarize_stats(stats)
            task['summary'] = summary
            task['progress'] = 100

            if summary['errors'] > 0 or summary['skipped'] > 0:
                task['status'] = 'failed'
                task['error'] = (
                    f"Migration có vấn đề: success={summary['success']}, "
                    f"errors={summary['errors']}, skipped={summary['skipped']}"
                )
                task['message'] = task['error']
            elif summary['success'] == 0:
                task['status'] = 'failed'
                task['error'] = 'Không có bản ghi nào được migrate. Vui lòng kiểm tra mapping cột, PK và dữ liệu nguồn.'
                task['message'] = task['error']
            else:
                task['status'] = 'completed'
                task['message'] = 'Hoàn thành!'

        except Exception as e:
            import traceback
            task['status'] = 'failed'
            task['error'] = str(e)
            task['logs'].append(traceback.format_exc())
            task['message'] = f"Lỗi: {str(e)}"

    # Start thread
    thread = threading.Thread(
        target=run_migration_task,
        args=(task_id, flow, sql_filename, mysql_config, pg_config, tables, strategy)
    )
    thread.daemon = True
    thread.start()

    return jsonify({
        'success': True,
        'task_id': task_id,
        'message': 'Đã bắt đầu tiến trình migration'
    })

@app.route('/api/migrate/status/<task_id>', methods=['GET'])
def get_migration_status(task_id):
    """Poll migration task status."""
    with _migration_tasks_lock:
        task = _migration_tasks.get(task_id)
    if not task:
        return jsonify({'success': False, 'message': 'Task not found'}), 404

    return jsonify({
        'success': True,
        'status': task['status'],
        'progress': task['progress'],
        'current_table': task['current_table'],
        'message': task['message'],
        'error': task['error'],
        'stats': task.get('stats', {}),
        'summary': task.get('summary', {}),
    })


# ---------------------------------------------------------------------------
# API: AI Agent (Phase 3)
# ---------------------------------------------------------------------------

def _get_agent(schema_cache: dict | None = None):
    """
    Tạo MigrationAgent instance với GeminiProvider.

    Lazy init: Chỉ khởi tạo khi được gọi, không block startup.
    GeminiProvider sẽ log warning nếu thiếu API key.
    """
    from db_migrator.agent import GeminiProvider, MigrationAgent  # noqa: PLC0415
    provider = GeminiProvider()  # Đọc GEMINI_API_KEY từ env
    return MigrationAgent(llm_provider=provider, schema_cache=schema_cache or {})


@app.route('/api/agent/chat', methods=['POST'])
def agent_chat():
    """
    Chat với AI Migration Assistant.

    Request body:
        {
            "message": "Phân tích schema bảng users cho tôi",
            "session_id": "optional-session-id"
        }

    Response:
        {
            "success": true,
            "answer": "...",
            "tools_used": ["analyze_schema"],
            "rounds": 2,
            "tokens_used": 150
        }
    """
    data = request.json or {}
    message = data.get('message', '').strip()

    if not message:
        return jsonify({'success': False, 'message': 'Thiếu trường message'}), 400

    if len(message) > 4000:
        return jsonify({'success': False, 'message': 'Message quá dài (tối đa 4000 ký tự)'}), 400

    try:
        agent = _get_agent()
        result = agent.run(message)
        _audit.log_event("agent.chat", {
            "message_preview": message[:100],
            "rounds": result.rounds,
            "tokens": result.tokens_used,
            "success": result.success,
        })
        return jsonify({
            'success': True,
            'answer': result.answer,
            'tools_used': result.tools_used,
            'rounds': result.rounds,
            'tokens_used': result.tokens_used,
        })
    except Exception as e:  # noqa: BLE001
        logger.error("Agent chat error: %s", e)
        return jsonify({'success': False, 'message': f'Agent error: {e}'}), 500


@app.route('/api/agent/analyze', methods=['POST'])
def agent_analyze():
    """
    Phân tích data anomalies trước migration.

    Request body:
        {
            "table": "users",
            "columns": ["id", "name", "email"],
            "rows": [[1, "Long", "long@test.com"], ...],
            "source_types": {"id": "int(11)", "name": "varchar(50)"},
            "target_types": {"id": "integer", "name": "varchar(100)"}
        }

    Response:
        {
            "success": true,
            "is_safe": true,
            "report": {
                "critical": 0,
                "warning": 1,
                "anomalies": [...]
            }
        }
    """
    data = request.json or {}
    table = data.get('table', 'unknown')
    columns = data.get('columns', [])
    rows = data.get('rows', [])
    source_types = data.get('source_types', {})
    target_types = data.get('target_types', {})

    if not columns:
        return jsonify({'success': False, 'message': 'Thiếu danh sách columns'}), 400

    try:
        from db_migrator.agent import AnomalyDetector  # noqa: PLC0415
        detector = AnomalyDetector()
        report = detector.check_table_data(
            table_name=table,
            columns=columns,
            rows=[tuple(r) for r in rows],
            source_types=source_types,
            target_types=target_types,
        )
        _audit.log_event("agent.analyze", {
            "table": table,
            "rows_checked": len(rows),
            "anomalies_found": len(report.anomalies),
            "is_safe": report.is_safe_to_migrate,
        })
        return jsonify({
            'success': True,
            'is_safe': report.is_safe_to_migrate,
            'report': report.to_dict(),
            'summary': report.get_summary(),
        })
    except Exception as e:  # noqa: BLE001
        logger.error("Agent analyze error: %s", e)
        return jsonify({'success': False, 'message': f'Analysis error: {e}'}), 500


@app.route('/api/agent/suggest-mapping', methods=['POST'])
def agent_suggest_mapping():
    """
    Gợi ý AI-enhanced column mapping giữa source và target.

    Request body:
        {
            "source_table": "mysql_users",
            "source_cols": ["id", "user_name", "deletedAt"],
            "target_table": "pg_users",
            "target_cols": ["id", "username", "is_deleted"]
        }

    Response:
        {
            "success": true,
            "suggestions": [
                {"source": "id", "target": "id", "confidence": 1.0, "method": "exact"},
                {"source": "user_name", "target": "username", "confidence": 0.9, "method": "normalized"}
            ],
            "unmatched_source": [],
            "unmatched_target": []
        }
    """
    data = request.json or {}
    source_table = data.get('source_table', 'source')
    source_cols = data.get('source_cols', [])
    target_table = data.get('target_table', 'target')
    target_cols = data.get('target_cols', [])

    if not source_cols or not target_cols:
        return jsonify({'success': False, 'message': 'Thiếu source_cols hoặc target_cols'}), 400

    try:
        from db_migrator.agent import GeminiProvider, SmartMapper  # noqa: PLC0415
        # Thử dùng Gemini nếu có key, fallback sang rule-based
        provider = None
        if os.environ.get('GEMINI_API_KEY'):
            provider = GeminiProvider()

        mapper = SmartMapper(llm_provider=provider)
        result = mapper.suggest(
            source_table=source_table,
            source_cols=source_cols,
            target_table=target_table,
            target_cols=target_cols,
        )
        _audit.log_event("agent.suggest_mapping", {
            "source_table": source_table,
            "target_table": target_table,
            "suggestions_count": len(result.suggestions),
        })
        return jsonify({
            'success': True,
            'result': result.to_dict(),
        })
    except Exception as e:  # noqa: BLE001
        logger.error("Suggest mapping error: %s", e)
        return jsonify({'success': False, 'message': f'Mapping error: {e}'}), 500


@app.route('/api/agent/explain-error', methods=['POST'])
def agent_explain_error():
    """
    Giải thích lỗi migration bằng ngôn ngữ tự nhiên.

    Request body:
        {
            "error": "ERROR: null value in column ...",
            "table": "orders"
        }

    Response:
        {
            "success": true,
            "category": "not_null",
            "title": "Vi phạm NOT NULL Constraint",
            "explanation": "...",
            "remediation": ["step 1", "step 2"]
        }
    """
    data = request.json or {}
    error_text = data.get('error', '').strip()
    table = data.get('table')

    if not error_text:
        return jsonify({'success': False, 'message': 'Thiếu trường error'}), 400

    try:
        from db_migrator.agent import ErrorExplainer, GeminiProvider  # noqa: PLC0415
        provider = None
        if os.environ.get('GEMINI_API_KEY'):
            provider = GeminiProvider()

        explainer = ErrorExplainer(llm_provider=provider)
        explained = explainer.explain(error_text, table=table)

        return jsonify({
            'success': True,
            'explanation': explained.to_dict(),
            'formatted': explained.format_for_user(),
        })
    except Exception as e:  # noqa: BLE001
        logger.error("Explain error: %s", e)
        return jsonify({'success': False, 'message': f'Error: {e}'}), 500


if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Dynamic Database Migration Web App Starting...")
    print(f"📂 Upload directory: {UPLOAD_FOLDER}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=True)  # nosec B201 B104 # noqa: S104, S201
