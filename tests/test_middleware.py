"""Request ID middleware tests."""
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient
from mio_taskhub.middleware import RequestIDMiddleware
from mio_taskhub.logging_config import request_id_var

def test_request_id_in_response():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/test")
    def handler():
        return {"rid": request_id_var.get()}

    client = TestClient(app)
    resp = client.get("/test")
    assert resp.status_code == 200
    rid = resp.headers.get("X-Request-ID")
    assert rid, "X-Request-ID header missing"
    assert len(rid) == 36  # UUID format

def test_client_supplied_request_id():
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/test")
    def handler():
        return {"rid": request_id_var.get()}

    client = TestClient(app)
    resp = client.get("/test", headers={"X-Request-ID": "my-custom-id"})
    assert resp.headers.get("X-Request-ID") == "my-custom-id"

def test_error_logged_on_500(caplog):
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/fail")
    def handler():
        raise ValueError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        resp = client.get("/fail")
    assert resp.status_code == 500
    assert any("boom" in r.message for r in caplog.records)

def test_unhandled_exception_logged(caplog):
    """Unhandled exceptions should be logged with request_id."""
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)

    @app.get("/crash")
    def handler():
        raise RuntimeError("unexpected")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.ERROR):
        resp = client.get("/crash")
    assert resp.status_code == 500
    assert any("unexpected" in r.message for r in caplog.records)
    assert any("request_exception" in r.message for r in caplog.records)
