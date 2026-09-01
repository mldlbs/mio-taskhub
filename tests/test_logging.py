"""Structured logging configuration tests."""
import json
import logging
from mio_taskhub.logging_config import setup_logging, RequestFilter

def test_setup_logging_configures_root():
    setup_logging()
    root = logging.getLogger()
    assert root.level == logging.INFO
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)

def test_request_filter_adds_request_id():
    f = RequestFilter()
    record = logging.LogRecord("test", logging.INFO, "", 0, "msg", (), None)
    record.request_id = "abc-123"
    f.filter(record)
    assert record.request_id == "abc-123"

def test_json_formatter_output():
    from mio_taskhub.logging_config import JSONFormatter
    fmt = JSONFormatter()
    record = logging.LogRecord(
        name="test", level=logging.INFO, pathname="test.py",
        lineno=1, msg="hello %s", args=("world",), exc_info=None,
    )
    record.request_id = "req-1"
    output = fmt.format(record)
    data = json.loads(output)
    assert data["msg"] == "hello world"
    assert data["request_id"] == "req-1"
    assert data["level"] == "INFO"
