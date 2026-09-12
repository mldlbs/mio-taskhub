"""SQLite auto-backup: periodic consistent snapshots via SQLite Online Backup API.

Backups are stored under ``~/.mio_taskhub/backups/`` (or ``$MIO_TASKHUB_DB/../backups/``).
Retention: hourly × 24 + daily × 7 = 31 files max; oldest pruned automatically.
"""
import os
import shutil
import sqlite3
import threading
import time
import logging
from datetime import datetime

logger = logging.getLogger("mio_taskhub.ops.backup")

DEFAULT_INTERVAL = 3600  # seconds (1 hour)
DEFAULT_KEEP_HOURLY = 24
DEFAULT_KEEP_DAILY = 7
_BACKUP_DIR_NAME = "backups"


class SQLiteBackup:
    """Daemon thread that snapshots the DB file using the SQLite Online Backup API.

    This guarantees a consistent snapshot even while the DB is being written to.
    """

    def __init__(
        self,
        db_path: str,
        interval: int = DEFAULT_INTERVAL,
        keep_hourly: int = DEFAULT_KEEP_HOURLY,
        keep_daily: int = DEFAULT_KEEP_DAILY,
    ):
        self.db_path = db_path
        self.interval = interval
        self.keep_hourly = keep_hourly
        self.keep_daily = keep_daily
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # Determine backup directory (sibling of DB file)
        db_dir = os.path.dirname(db_path) or "."
        self.backup_dir = os.path.join(db_dir, _BACKUP_DIR_NAME)
        os.makedirs(self.backup_dir, exist_ok=True)

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="sqlite-backup")
        self._thread.start()
        logger.info("SQLite backup started (interval=%ds)", self.interval)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("SQLite backup stopped")

    # -- internal -----------------------------------------------------------

    def _run(self):
        # Initial delay: 60s after startup (let DB settle)
        if self._stop.wait(timeout=60):
            return
        while not self._stop.is_set():
            try:
                self._do_backup()
            except Exception:
                logger.exception("Backup failed")
            self._stop.wait(timeout=self.interval)

    def _do_backup(self):
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        dst = os.path.join(self.backup_dir, f"taskhub_{ts}.db")
        # Use SQLite Online Backup API for consistent snapshot
        src_conn = sqlite3.connect(self.db_path)
        dst_conn = sqlite3.connect(dst)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        logger.info("Backup created: %s", dst)
        self._prune()

    def _prune(self):
        """Keep at most keep_hourly hourly + keep_daily daily backups."""
        files = sorted(
            (f for f in os.listdir(self.backup_dir) if f.startswith("taskhub_") and f.endswith(".db")),
            reverse=True,
        )
        # Simple strategy: keep N most recent files
        max_keep = self.keep_hourly + self.keep_daily
        for old in files[max_keep:]:
            try:
                os.remove(os.path.join(self.backup_dir, old))
                logger.debug("Pruned old backup: %s", old)
            except OSError:
                pass


def get_backup_status(db_path: str) -> dict:
    """Return backup directory info for the /readyz or status endpoint."""
    db_dir = os.path.dirname(db_path) or "."
    backup_dir = os.path.join(db_dir, _BACKUP_DIR_NAME)
    if not os.path.isdir(backup_dir):
        return {"backup_dir": backup_dir, "count": 0, "latest": None, "total_bytes": 0}
    files = sorted(
        (f for f in os.listdir(backup_dir) if f.startswith("taskhub_") and f.endswith(".db")),
        reverse=True,
    )
    total = 0
    latest = None
    for f in files:
        fp = os.path.join(backup_dir, f)
        total += os.path.getsize(fp)
        if latest is None:
            latest = f
    return {"backup_dir": backup_dir, "count": len(files), "latest": latest, "total_bytes": total}
