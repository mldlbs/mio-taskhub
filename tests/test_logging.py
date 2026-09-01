"""Structured logging configuration tests."""
import json
import logging
from mio_taskhub.logging_config import setup_logging

def test_setup_logging_configures_root():
    setup_logging()
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)

def test_json_formatter_output():
    from mio_taskhub.logging_config import JSONFormatter
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
    from mio_taskhub.logging_config import request_id_var
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
    from mio_taskhub.logging_config import JSONFormatter
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
