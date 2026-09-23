# tests/test_update_api.py
from fastapi.testclient import TestClient
import pytest

from mio_taskhub.main import app

client = TestClient(app)


@pytest.fixture()
def fake_service(tmp_path):
    from mio_taskhub.update.service import UpdateService
    from mio_taskhub.update.manifest import manifest_from_dict

    M = {
        "schema": 1, "version": "0.4.0", "channel": "stable",
        "released_at": "x", "min_supported": "0.3.0", "mandatory": False, "notes": "n",
        "assets": [{
            "os": "windows", "arch": "x64",
            "url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip",
            "sha256": "d" * 64, "size": 5, "format": "zip",
        }],
    }

    class S:
        def fetch_manifest(self): return manifest_from_dict(M)

    svc = UpdateService(source=S(), downloader=lambda *a, **k: a[1],
                        current_version="0.3.0",
                        prefs_path=tmp_path / "prefs.json",
                        update_dir=str(tmp_path / "updates"), install_dir=".")
    import mio_taskhub.api.update as u
    prev = u._service_override
    u._service_override = svc
    yield svc
    u._service_override = prev


def test_update_status_and_check(fake_service):
    r = client.get("/api/v1/update/status")
    assert r.status_code == 200
    assert r.json()["current"] == "0.3.0"
    c = client.post("/api/v1/update/check")
    assert c.status_code == 200
    assert c.json()["state"] == "available"


def test_update_dismiss(fake_service):
    client.post("/api/v1/update/check")
    d = client.post("/api/v1/update/dismiss")
    assert d.json()["state"] == "dismissed"


def test_update_apply_dev_mode_fails(fake_service):
    client.post("/api/v1/update/check")
    a = client.post("/api/v1/update/apply")
    assert a.status_code == 200
    assert a.json()["state"] == "failed"
    assert "开发模式" in a.json()["error"]


def test_runtime_state_written_when_enabled(tmp_path, monkeypatch):
    from mio_taskhub.update import runtime
    p = tmp_path / "runtime.json"
    monkeypatch.setenv("MIO_UPDATE_STATE_PATH", str(p))
    runtime.write_runtime_state(version="9.9.9", port=12345)
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["version"] == "9.9.9"
    assert data["port"] == 12345
    assert data["pid"] > 0


def test_recover_residual_called_on_startup(tmp_path, monkeypatch):
    from mio_taskhub.update import runtime
    calls = []
    monkeypatch.setattr(runtime, "recover_residual", lambda *a, **k: calls.append(a))
    runtime.startup_recovery(install_dir=str(tmp_path), _frozen=lambda: True)
    assert calls


def test_lifespan_starts_update_service(tmp_path, monkeypatch):
    """回归：lifespan 必须真正把 update service 挂上 app.state（此前被 except 吞掉）。"""
    monkeypatch.setenv("MIO_UPDATE_DISABLED", "1")
    from fastapi.testclient import TestClient
    from mio_taskhub.main import app
    with TestClient(app) as c:
        assert getattr(app.state, "update_service", None) is not None
