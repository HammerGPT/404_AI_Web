import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.getenv('DB_PATH', '/data/tester.db' if Path('/data').exists() else './tester.db'))
LOCK = threading.Lock()


def init():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as cx:
        cx.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, model TEXT NOT NULL, label TEXT, mode TEXT, protocol TEXT, status TEXT, payload TEXT NOT NULL)')
        cx.execute('CREATE INDEX IF NOT EXISTS reports_created_idx ON reports(created_at DESC)')
        cx.execute('CREATE TABLE IF NOT EXISTS run_requests (request_id TEXT PRIMARY KEY, report_id TEXT UNIQUE NOT NULL)')
        cx.execute('CREATE TABLE IF NOT EXISTS model_presets (id TEXT PRIMARY KEY, name TEXT NOT NULL, model_id TEXT UNIQUE NOT NULL, brand TEXT NOT NULL, protocol TEXT NOT NULL, enabled INTEGER NOT NULL, sort_order INTEGER NOT NULL)')
        cx.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        cx.execute('CREATE TABLE IF NOT EXISTS model_reasoning (preset_id TEXT PRIMARY KEY, model_id TEXT NOT NULL, protocol TEXT NOT NULL, options TEXT NOT NULL, default_value TEXT NOT NULL)')
        presets = json.loads((Path(__file__).parent/'data/model-presets.json').read_text())
        if not cx.execute("SELECT 1 FROM settings WHERE key='model_presets_seeded'").fetchone():
            for order, model in enumerate(presets):
                cx.execute('INSERT OR IGNORE INTO model_presets VALUES (?,?,?,?,?,?,?)',
                           (uuid.uuid4().hex, model['name'], model['model_id'], model['brand'], model['protocol'], 1, order * 10))
            cx.execute("INSERT INTO settings VALUES ('model_presets_seeded','1')")
        seeded = {m['model_id']:m for m in presets}
        for preset_id, model_id, protocol in cx.execute('SELECT id,model_id,protocol FROM model_presets').fetchall():
            known = seeded.get(model_id,{})
            options = known.get('reasoning_options',['auto']) if known.get('protocol')==protocol else ['auto']
            cx.execute('INSERT OR IGNORE INTO model_reasoning VALUES (?,?,?,?,?)',
                       (preset_id,model_id,protocol,json.dumps(options),'auto'))
        billing=(Path(__file__).parent/'data/basic-settings.json').read_text()
        cx.execute("INSERT OR IGNORE INTO settings VALUES ('billing',?)",(billing,))


def save(r: dict, request_id: str | None = None):
    with LOCK, sqlite3.connect(DB) as cx:
        cx.execute('''INSERT INTO reports (id,created_at,model,label,mode,protocol,status,payload)
                      VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                      status=excluded.status,payload=excluded.payload''', (
            r['id'], r['created_at'], r['model'], r.get('label', ''), r['mode'], r['protocol'], r['status'], json.dumps(r, ensure_ascii=False)))
        if request_id is not None:
            cx.execute('INSERT INTO run_requests VALUES (?,?)',(request_id,r['id']))


def list_reports(limit: int = 60):
    with LOCK, sqlite3.connect(DB) as cx:
        cx.row_factory = sqlite3.Row
        rows = cx.execute('SELECT id, created_at, model, label, mode, protocol, status, payload FROM reports ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            data = json.loads(item.pop('payload'))
            item['run_state'] = data.get('run_state', 'completed')
            result.append(item)
        return result


def get(report_id: str):
    with LOCK, sqlite3.connect(DB) as cx:
        row = cx.execute('SELECT payload FROM reports WHERE id=?', (report_id,)).fetchone()
        return json.loads(row[0]) if row else None


def by_request(request_id: str):
    with LOCK, sqlite3.connect(DB) as cx:
        row = cx.execute('SELECT r.payload FROM reports r JOIN run_requests q ON q.report_id=r.id WHERE q.request_id=?', (request_id,)).fetchone()
        return json.loads(row[0]) if row else None


def interrupt_runs():
    """A restart cannot replay requests because credentials are never stored."""
    with LOCK, sqlite3.connect(DB) as cx:
        for report_id, payload in cx.execute("SELECT id,payload FROM reports WHERE status='running'").fetchall():
            data = json.loads(payload)
            data.update(run_state='interrupted', status='attention', current_case=None,
                        finished_at=datetime.now(timezone.utc).isoformat(),
                        stop_reason='服务重启，检测已中断；已完成的结果保留，不会自动重试。')
            data['revision'] = data.get('revision', 0) + 1
            cx.execute('UPDATE reports SET status=?,payload=? WHERE id=?',
                       ('attention', json.dumps(data, ensure_ascii=False), report_id))


def list_models():
    with LOCK, sqlite3.connect(DB) as cx:
        cx.row_factory = sqlite3.Row
        result = [dict(r) for r in cx.execute('''SELECT m.*, r.options AS reasoning_options, r.default_value AS reasoning_default,
                    r.model_id AS source_model, r.protocol AS source_protocol
                    FROM model_presets m LEFT JOIN model_reasoning r ON r.preset_id=m.id ORDER BY m.sort_order,m.name,m.id''')]
        for model in result:
            model['enabled'] = bool(model['enabled'])
            source_model,source_protocol=model.pop('source_model'),model.pop('source_protocol')
            matching = source_model==model['model_id'] and source_protocol==model['protocol']
            model['reasoning_options'] = json.loads(model['reasoning_options']) if matching and model['reasoning_options'] else ['auto']
            model['reasoning_default'] = model['reasoning_default'] if matching else 'auto'
        return result


def reasoning_profile(model_id, protocol):
    model = next((m for m in list_models() if m['enabled'] and m['model_id']==model_id and m['protocol']==protocol),None)
    return {'options':model['reasoning_options'],'default':model['reasoning_default']} if model else {'options':['auto'],'default':'auto'}


def billing_settings():
    with LOCK, sqlite3.connect(DB) as cx:
        return json.loads(cx.execute("SELECT value FROM settings WHERE key='billing'").fetchone()[0])


def save_billing(data):
    with LOCK, sqlite3.connect(DB) as cx:
        cx.execute("UPDATE settings SET value=? WHERE key='billing'",(json.dumps(data),))


def model_protocol(model_id: str):
    with LOCK, sqlite3.connect(DB) as cx:
        row = cx.execute('SELECT protocol FROM model_presets WHERE model_id=? AND enabled=1', (model_id,)).fetchone()
        return row[0] if row else None


def put_model(data: dict, model_id: str | None = None):
    with LOCK, sqlite3.connect(DB) as cx:
        if model_id is not None and not cx.execute('SELECT 1 FROM model_presets WHERE id=?', (model_id,)).fetchone():
            raise KeyError(model_id)
        model_id = model_id or uuid.uuid4().hex
        cx.execute('''INSERT INTO model_presets VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name,model_id=excluded.model_id,brand=excluded.brand,
                   protocol=excluded.protocol,enabled=excluded.enabled,sort_order=excluded.sort_order''',
                   (model_id, data['name'], data['model_id'], data['brand'], data['protocol'], data['enabled'], data['sort_order']))
        previous = cx.execute('SELECT model_id,protocol,options,default_value FROM model_reasoning WHERE preset_id=?',(model_id,)).fetchone()
        same = previous and previous[0]==data['model_id'] and previous[1]==data['protocol']
        choices = data.get('reasoning_options') or (json.loads(previous[2]) if same else ['auto'])
        default = data.get('reasoning_default') or (previous[3] if same else 'auto')
        from .catalog import validate_reasoning_profile
        validate_reasoning_profile(data['protocol'],choices,default)
        cx.execute('''INSERT INTO model_reasoning VALUES (?,?,?,?,?) ON CONFLICT(preset_id) DO UPDATE SET
                      model_id=excluded.model_id,protocol=excluded.protocol,options=excluded.options,default_value=excluded.default_value''',
                   (model_id,data['model_id'],data['protocol'],json.dumps(choices),default))
        return model_id
