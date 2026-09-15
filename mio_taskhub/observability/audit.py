import time
import sqlite3
import os
import threading

_local = threading.local()

def _db_path():
    from mio_taskhub.db import DB_PATH
    return DB_PATH

def _conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(_db_path(), timeout=5)
        _local.conn.row_factory = sqlite3.Row
    return _local.conn

class AlertAudit:
    def log_fire(self, alert_name: str, severity: str, message: str = "", metric_value: float = None, threshold: float = None):
        conn = _conn()
        conn.execute(
            "INSERT INTO alertaudit (ts, alert_name, severity, action, message, metric_value, threshold) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), alert_name, severity, "fire", message, metric_value, threshold)
        )
        conn.commit()

    def log_resolve(self, alert_name: str, message: str = "", duration_seconds: float = None):
        conn = _conn()
        conn.execute(
            "INSERT INTO alertaudit (ts, alert_name, severity, action, message, duration_seconds) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), alert_name, "", "resolve", message, duration_seconds)
        )
        conn.commit()

    def recent(self, limit: int = 50) -> list:
        conn = _conn()
        rows = conn.execute("SELECT * FROM alertaudit ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def stats(self, hours: int = 24) -> dict:
        conn = _conn()
        cutoff = time.time() - hours * 3600
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(CASE WHEN action='fire' THEN 1 ELSE 0 END) as fires, SUM(CASE WHEN action='resolve' THEN 1 ELSE 0 END) as resolves FROM alertaudit WHERE ts > ?",
            (cutoff,)
        ).fetchone()
        return dict(row)
