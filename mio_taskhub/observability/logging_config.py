"""Structured JSON logging with request + trace correlation."""
import json
import logging
import sys
import threading
from collections import deque
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")
span_id_var: ContextVar[str] = ContextVar("span_id", default="-")


class LogBuffer:
    """In-memory ring buffer for recent log entries (last 1000)."""
    def __init__(self, maxsize: int = 1000):
        self._buf: deque = deque(maxlen=maxsize)
        self._lock = threading.Lock()

    def add(self, entry: dict):
        with self._lock:
            self._buf.append(entry)

    def query(self, level: str = None, logger: str = None, limit: int = 100) -> list:
        with self._lock:
            items = list(self._buf)
        if level:
            items = [e for e in items if e.get("level") == level.upper()]
        if logger:
            items = [e for e in items if logger in e.get("logger", "")]
        return items[-limit:]


# Global log buffer instance
log_buffer = LogBuffer()


def _get_trace_context() -> dict:
    """Extract trace context from OpenTelemetry current span."""
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return {
                "trace_id": format(ctx.trace_id, '032x'),
                "span_id": format(ctx.span_id, '016x'),
            }
    except Exception:
        pass
    return {"trace_id": trace_id_var.get(), "span_id": span_id_var.get()}


class JSONFormatter(logging.Formatter):
    def format(self, record):
        trace_ctx = _get_trace_context()
        log_entry = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", request_id_var.get()),
            "trace_id": trace_ctx["trace_id"],
            "span_id": trace_ctx["span_id"],
        }
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class HumanReadableFormatter(logging.Formatter):
    """Colored console output for development."""
    COLORS = {
        "DEBUG": "\033[36m",    # cyan
        "INFO": "\033[32m",     # green
        "WARNING": "\033[33m",  # yellow
        "ERROR": "\033[31m",    # red
        "CRITICAL": "\033[41m", # red bg
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, "")
        reset = self.RESET
        ts = self.formatTime(record, "%H:%M:%S")
        trace_ctx = _get_trace_context()
        tid = trace_ctx["trace_id"][:8] if trace_ctx["trace_id"] != "-" else "-"
        return f"{color}{ts} [{record.levelname:<7}] {reset}{record.name}: {record.getMessage()} [req={request_id_var.get()} tid={tid}]"


class BufferHandler(logging.Handler):
    """Log handler that feeds into the in-memory buffer."""
    def __init__(self):
        super().__init__()
        self._buffer = log_buffer
        self._fmt = logging.Formatter("%(asctime)s")

    def emit(self, record):
        try:
            entry = {
                "ts": self._fmt.formatTime(record, "%Y-%m-%d %H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
                "request_id": getattr(record, "request_id", "-"),
            }
            self._buffer.add(entry)
        except Exception:
            pass


def setup_logging(level: str = "INFO", json_format: bool = True):
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    if json_format:
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(HumanReadableFormatter())
    root.addHandler(handler)
    # Install buffer handler for /api/v1/logs
    buf_handler = BufferHandler()
    buf_handler.setFormatter(logging.Formatter("%(asctime)s"))
    root.addHandler(buf_handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_log_context() -> dict:
    """Get current log context for debugging."""
    trace_ctx = _get_trace_context()
    return {
        "request_id": request_id_var.get(),
        "trace_id": trace_ctx["trace_id"],
        "span_id": trace_ctx["span_id"],
    }
