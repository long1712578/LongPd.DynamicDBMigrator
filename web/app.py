#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web/app.py
==========
Flask Web Application for the Dynamic DB Migration Library.

Provides a clean, modern API and UI for:
1. Discovering schemas from SQL files or live databases
2. Visual drag-and-drop mapping
3. Executing migrations asynchronously
"""

import os
import sys
import threading
import uuid
from flask import Flask, render_template, request, jsonify

# Ensure db_migrator is in Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from db_migrator import (
    MigrationConfig,
    SchemaDiscovery,
    DatabaseMigrator,
)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max for upload if used
UPLOAD_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'alldatapostgre'))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Global state for background migration tasks
# In a real production app, use Celery/Redis. For this internal tool, simple dict + thread is fine.
_migration_tasks = {}
_migration_tasks_lock = threading.Lock()


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
        return jsonify({'success': False, 'message': f'Không tìm thấy file {filename} trong thư mục alldatapostgre/'}), 404
        
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
    from db_migrator.discovery import TableSchema, ColumnInfo
    
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
                task['logs'].append(f"Starting pipeline: File -> MySQL -> PostgreSQL")
                stats = migrator.migrate_file_to_postgres(sql_filepath, mysql_config, pg_config, tables, strategy)
                
            elif flow == 'file_to_mysql':
                if not sql_filename:
                    raise ValueError("Thiếu tên file SQL")
                sql_filepath = os.path.join(app.config['UPLOAD_FOLDER'], sql_filename)
                task['logs'].append(f"Starting import: File -> MySQL")
                stats = migrator.migrate_file_to_mysql(sql_filepath, mysql_config, tables, strategy)
                
            elif flow == 'mysql_to_postgres':
                task['logs'].append(f"Starting sync: MySQL -> PostgreSQL")
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

if __name__ == '__main__':
    print("=" * 60)
    print("🚀 Dynamic Database Migration Web App Starting...")
    print(f"📂 Upload directory: {UPLOAD_FOLDER}")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=True)