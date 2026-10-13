"""Structured logging configuration tests."""
import json
import logging
from mio_taskhub.observability.logging_config import setup_logging

def test_setup_logging_configures_root():
    setup_logging()
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)

def test_json_formatter_output():
    from mio_taskhub.observability.logging_config import JSONFormatter
    fmt = JSONFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="test.py",
        lineno=1, msg="hello %s", args=("world",), exc_info=None,
    )
    output = fmt.format(record)
    data = json.loads(output)
    assert data["msg"] == "hello world"
    assert data["request_id"] == "-"
    assert data["level"] == "INFO"

def test_request_id_context_isolation():
    """ContextVar should isolate request_id across different contexts."""
    from mio_taskhub.observability.logging_config import request_id_var
    token_a = request_id_var.set("req-A")
    try:
        assert request_id_var.get() == "req-A"
        token_b = request_id_var.set("req-B")
        try:
            assert request_id_var.get() == "req-B"
        finally:
            request_id_var.reset(token_b)
        assert request_id_var.get() == "req-A"
    finally:
        request_id_var.reset(token_a)

def test_json_formatter_exception():
    from mio_taskhub.observability.logging_config import JSONFormatter
    fmt = JSONFormatter()
    try:
        raise ValueError("boom")
    except ValueError:
        import sys
        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="test.py",
            lineno=1, msg="error occurred", args=(), exc_info=sys.exc_info(),
        )
    record.request_id = "req-err"
    output = fmt.format(record)
    data = json.loads(output)
    assert "exception" in data
    assert "ValueError: boom" in data["exception"]


def test_json_formatter_passes_through_extra_fields():
    """F3：extra 字段（path/status/duration_ms）必须透传，旧键保留只新增。"""
    from mio_taskhub.observability.logging_config import JSONFormatter
    fmt = JSONFormatter()
    logger = logging.getLogger("t05.extra")
    record = logging.LogRecord(
        name="t05.extra", level=logging.ERROR, pathname="x.py",
        lineno=1, msg="request_error", args=(), exc_info=None,
    )
    record.request_id = "rid-1"
    record.path = "/api/v1/tasks"
    record.status = 500
    record.duration_ms = 12.3
    data = json.loads(fmt.format(record))
    assert data["msg"] == "request_error"
    assert data["request_id"] == "rid-1"   # 旧键保留
    assert data["path"] == "/api/v1/tasks"
    assert data["status"] == 500
    assert data["duration_ms"] == 12.3


def test_json_formatter_unserializable_extra_does_not_crash():
    """default=str：不可序列化的 extra 值降级为字符串，不炸日志。"""
    from mio_taskhub.observability.logging_config import JSONFormatter
    fmt = JSONFormatter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="x.py",
        lineno=1, msg="m", args=(), exc_info=None,
    )
    record.obj = object()
    data = json.loads(fmt.format(record))
    assert "obj" in data


def test_buffer_handler_passes_through_extra():
    """F3：/api/v1/logs 的 LogBuffer entry 也要含 extra 字段。"""
    from mio_taskhub.observability.logging_config import BufferHandler, log_buffer
    h = BufferHandler()
    before = len(log_buffer._buf)
    record = logging.LogRecord(
        name="t05.buf", level=logging.ERROR, pathname="x.py",
        lineno=1, msg="request_error", args=(), exc_info=None,
    )
    record.path = "/api/v1/x"
    record.status = 502
    record.duration_ms = 7.7
    h.emit(record)
    entry = log_buffer._buf[-1]
    assert len(log_buffer._buf) == before + 1
    assert entry["path"] == "/api/v1/x"
    assert entry["status"] == 502
    assert entry["duration_ms"] == 7.7
