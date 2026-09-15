import time
import logging
from mio_taskhub.db import engine
from sqlalchemy import text

logger = logging.getLogger(__name__)


class DepMetricsPersist:
    def snapshot(self):
        """Capture current dep metrics to DB."""
        try:
            from mio_taskhub.observability.dep_metrics import DepMetrics
            now = time.time()
            rows = []
            for dep, ops in DepMetrics.get_all().items():
                for op, info in ops.items():
                    rows.append((now, dep, op, info["count"], info["errors"], info["avg_ms"], info["p50_ms"], info["p90_ms"], info["p99_ms"], info["max_ms"], info["error_rate"]))

            if rows:
                with engine.connect() as conn:
                    for row in rows:
                        conn.execute(
                            text("INSERT INTO depmetricssnapshot (ts, dep, op, count, errors, avg_ms, p50_ms, p90_ms, p99_ms, max_ms, error_rate) VALUES (:ts, :dep, :op, :count, :errors, :avg_ms, :p50, :p90, :p99, :max_ms, :error_rate)"),
                            {"ts": row[0], "dep": row[1], "op": row[2], "count": row[3], "errors": row[4], "avg_ms": row[5], "p50": row[6], "p90": row[7], "p99": row[8], "max_ms": row[9], "error_rate": row[10]}
                        )
                    conn.commit()
        except Exception:
            logger.exception("Failed to snapshot dep metrics")

    def recent(self, limit: int = 50) -> list:
        try:
            with engine.connect() as conn:
                result = conn.execute(text("SELECT * FROM depmetricssnapshot ORDER BY ts DESC LIMIT :limit"), {"limit": limit})
                return [dict(row._mapping) for row in result]
        except Exception:
            logger.exception("Failed to query dep metrics")
            return []

    def cleanup(self, days: int = 7):
        try:
            cutoff = time.time() - days * 86400
            with engine.connect() as conn:
                conn.execute(text("DELETE FROM depmetricssnapshot WHERE ts < :cutoff"), {"cutoff": cutoff})
                conn.commit()
        except Exception:
            logger.exception("Failed to cleanup dep metrics")
