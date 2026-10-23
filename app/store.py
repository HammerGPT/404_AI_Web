import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.getenv('DB_PATH', '/data/tester.db' if Path('/data').exists() else './tester.db'))
LOCK = threading.Lock()


def init():
    DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB) as cx:
        cx.execute('CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, model TEXT NOT NULL, label TEXT, mode TEXT, protocol TEXT, status TEXT, payload TEXT NOT NULL)')
        cx.execute('CREATE INDEX IF NOT EXISTS reports_created_idx ON reports(created_at DESC)')


def save(r: dict):
    with LOCK, sqlite3.connect(DB) as cx:
        cx.execute('INSERT INTO reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (
            r['id'], r['created_at'], r['model'], r.get('label', ''), r['mode'], r['protocol'], r['status'], json.dumps(r, ensure_ascii=False)))


def list_reports(limit: int = 60):
    with LOCK, sqlite3.connect(DB) as cx:
        cx.row_factory = sqlite3.Row
        rows = cx.execute('SELECT id, created_at, model, label, mode, protocol, status FROM reports ORDER BY created_at DESC LIMIT ?', (limit,)).fetchall()
        return [dict(r) for r in rows]


def get(report_id: str):
    with LOCK, sqlite3.connect(DB) as cx:
        row = cx.execute('SELECT payload FROM reports WHERE id=?', (report_id,)).fetchone()
        return json.loads(row[0]) if row else None
