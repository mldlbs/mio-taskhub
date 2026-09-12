"""Dependency latency and error metrics for SQLite, Git, and MCP calls.

Usage:
    with DepMetrics.track("sqlite", "query"):
        result = session.execute(...)

    DepMetrics.record("git", "push", 0.5, success=True)
"""
import time
import threading
from contextlib import contextmanager
from typing import Optional

_lock = threading.Lock()

# Store: {dep: {op: {"count": N, "errors": N, "total_ms": X, "max_ms": Y, "latencies": [...]}}}
_data: dict[str, dict[str, dict]] = {}
_MAX_SAMPLES = 1000  # keep last N latencies per op for percentile calculation


class DepMetrics:
    """Track latency and error metrics for external dependencies."""

    @staticmethod
    def record(dep: str, op: str, duration_ms: float, success: bool = True) -> None:
        with _lock:
            if dep not in _data:
                _data[dep] = {}
            if op not in _data[dep]:
                _data[dep][op] = {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0, "latencies": []}
            d = _data[dep][op]
            d["count"] += 1
            d["total_ms"] += duration_ms
            if duration_ms > d["max_ms"]:
                d["max_ms"] = duration_ms
            if not success:
                d["errors"] += 1
            if len(d["latencies"]) < _MAX_SAMPLES:
                d["latencies"].append(duration_ms)

    @staticmethod
    @contextmanager
    def track(dep: str, op: str):
        """Context manager to track latency of a block."""
        start = time.perf_counter()
        success = True
        try:
            yield
        except Exception:
            success = False
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            DepMetrics.record(dep, op, elapsed_ms, success)

    @staticmethod
    def get_all() -> dict:
        """Return all collected metrics as a structured dict."""
        with _lock:
            result = {}
            for dep, ops in _data.items():
                result[dep] = {}
                for op, d in ops.items():
                    lats = sorted(d["latencies"])
                    n = len(lats)
                    avg = d["total_ms"] / d["count"] if d["count"] > 0 else 0
                    p50 = lats[n // 2] if n > 0 else 0
                    p90 = lats[int(n * 0.9)] if n > 0 else 0
                    p99 = lats[int(n * 0.99)] if n > 0 else 0
                    result[dep][op] = {
                        "count": d["count"],
                        "errors": d["errors"],
                        "avg_ms": round(avg, 2),
                        "max_ms": round(d["max_ms"], 2),
                        "p50_ms": round(p50, 2),
                        "p90_ms": round(p90, 2),
                        "p99_ms": round(p99, 2),
                        "error_rate": round(d["errors"] / d["count"], 4) if d["count"] > 0 else 0,
                    }
            return result

    @staticmethod
    def render() -> str:
        """Render dependency metrics in Prometheus format."""
        lines = []
        all_data = DepMetrics.get_all()

        for dep, ops in all_data.items():
            for op, d in ops.items():
                labels = f'dep="{dep}",op="{op}"'
                lines.append(f'taskhub_dep_latency_count{{{labels}}} {d["count"]}')
                lines.append(f'taskhub_dep_latency_errors{{{labels}}} {d["errors"]}')
                lines.append(f'taskhub_dep_latency_avg_ms{{{labels}}} {d["avg_ms"]}')
                lines.append(f'taskhub_dep_latency_max_ms{{{labels}}} {d["max_ms"]}')
                lines.append(f'taskhub_dep_latency_p50_ms{{{labels}}} {d["p50_ms"]}')
                lines.append(f'taskhub_dep_latency_p90_ms{{{labels}}} {d["p90_ms"]}')
                lines.append(f'taskhub_dep_latency_p99_ms{{{labels}}} {d["p99_ms"]}')
                lines.append(f'taskhub_dep_error_rate{{{labels}}} {d["error_rate"]}')

        return "\n".join(lines)

    @staticmethod
    def reset() -> None:
        """Reset all collected metrics."""
        with _lock:
            _data.clear()