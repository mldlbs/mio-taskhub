"""Request ID middleware tests."""
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
