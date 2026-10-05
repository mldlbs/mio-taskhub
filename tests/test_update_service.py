# -*- coding: utf-8 -*-
import pytest

from mio_taskhub.update.manifest import manifest_from_dict
from mio_taskhub.update.service import UpdateState, UpdateService

MANIFEST = {
    "schema": 1, "version": "0.4.0", "channel": "stable",
    "released_at": "2026-09-22T10:00:00Z", "min_supported": "0.3.0",
    "mandatory": False, "notes": "n",
    "assets": [{
        "os": "windows", "arch": "x64",
        "url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip",
        "sha256": "c" * 64, "size": 5, "format": "zip",
    }],
}


class FakeSource:
    def __init__(self, m): self.m = m; self.raised = None
    def fetch_manifest(self):
        if self.raised:
            raise self.raised
        return self.m


def _svc(tmp_path, m, current="0.3.0", prefs=None):
    return UpdateService(
        source=FakeSource(m),
        downloader=lambda url, dest, sha, size=None, progress=None: dest,
        install_dir=tmp_path / "app",
        current_version=current,
        prefs_path=prefs or (tmp_path / "prefs.json"),
        apply_runner=lambda *a, **k: 0,
    )


def test_check_available(tmp_path):
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST))
    st = svc.check()
    assert st["state"] == UpdateState.AVAILABLE.value
    assert st["latest"] == "0.4.0"


def test_check_up_to_date(tmp_path):
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST), current="0.4.0")
    assert svc.check()["state"] == UpdateState.UP_TO_DATE.value


def test_check_needs_manual(tmp_path):
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST), current="0.2.0")
    assert svc.check()["state"] == UpdateState.NEEDS_MANUAL.value


def test_check_failed_is_silent(tmp_path):
    svc = _svc(tmp_path, None)
    svc.source.raised = RuntimeError("net")
    st = svc.check()
    assert st["state"] == UpdateState.CHECK_FAILED.value
    assert st["error"]


def test_dismiss_without_check_is_noop_event(tmp_path):
    events = []
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST))
    svc._on_event = lambda ev: events.append(ev["kind"])
    svc.dismiss()  # 未 check → 不应发 dismissed 事件
    assert svc.status()["state"] == UpdateState.IDLE.value
    assert "update_dismissed" not in events


def test_dismiss_persists(tmp_path):
    prefs = tmp_path / "prefs.json"
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST), prefs=prefs)
    svc.check()
    svc.dismiss()
    assert svc.status()["state"] == UpdateState.DISMISSED.value
    # 新实例读取同一 prefs → 仍 dismissed
    svc2 = _svc(tmp_path, manifest_from_dict(MANIFEST), prefs=prefs)
    svc2.check()
    assert svc2.status()["state"] == UpdateState.DISMISSED.value


def test_get_service_constructs_without_install_dir(tmp_path, monkeypatch):
    """回归：构造参数 install_dir 不得遮蔽 version.install_dir()。"""
    from mio_taskhub.update import service as svc_mod
    monkeypatch.setattr(svc_mod, "_SERVICE", None)
    monkeypatch.setenv("MIO_UPDATE_DISABLED", "1")
    s = svc_mod.get_service()
    assert s.install is not None
    assert str(s.install)  # 非空路径
    monkeypatch.setattr(svc_mod, "_SERVICE", None)


def test_apply_dev_mode_fails(tmp_path, monkeypatch):
    """非 frozen（开发态）→ apply() 必须 FAILED，且不调用 apply_runner。"""
    called = []
    svc = UpdateService(
        source=FakeSource(manifest_from_dict(MANIFEST)),
        downloader=lambda *a, **k: a[1],
        install_dir=tmp_path / "app",
        current_version="0.3.0",
        prefs_path=tmp_path / "prefs.json",
        apply_runner=lambda *a, **k: called.append(a) or 0,
    )
    monkeypatch.setattr("mio_taskhub.version.is_frozen", lambda: False)
    st = svc.apply()
    assert st["state"] == UpdateState.FAILED.value
    assert "开发模式" in st["error"]
    assert called == []


def test_apply_frozen_ready_calls_runner(tmp_path, monkeypatch):
    """frozen + READY → 调 apply_runner，rc=0 → DONE，rc=1 → FAILED。"""
    svc = UpdateService(
        source=FakeSource(manifest_from_dict(MANIFEST)),
        downloader=lambda *a, **k: a[1],
        install_dir=tmp_path / "app",
        current_version="0.3.0",
        prefs_path=tmp_path / "prefs.json",
        update_dir=tmp_path / "updates",
    )
    svc._manifest = manifest_from_dict(MANIFEST)
    svc._asset = svc._manifest.asset_for("windows", "x64")
    svc._state = UpdateState.READY
    monkeypatch.setattr("mio_taskhub.version.is_frozen", lambda: True)

    rc = {"v": 0}
    svc._apply_runner = lambda install, zip_path, manifest, pid: rc["v"]
    assert svc.apply()["state"] == UpdateState.DONE.value

    svc._state = UpdateState.READY
    rc["v"] = 1
    st = svc.apply()
    assert st["state"] == UpdateState.FAILED.value
    assert st["error"]


def test_get_service_has_apply_runner(monkeypatch):
    """回归：生产单例必须注入真实 apply_runner，否则 apply() 永远不可用。"""
    from mio_taskhub.update import service as svc_mod
    monkeypatch.setattr(svc_mod, "_SERVICE", None)
    monkeypatch.setenv("MIO_UPDATE_DISABLED", "1")
    s = svc_mod.get_service()
    assert s._apply_runner is not None
    monkeypatch.setattr(svc_mod, "_SERVICE", None)


def test_check_ignores_other_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_UPDATE_CHANNEL", "stable")
    m = manifest_from_dict(dict(MANIFEST, channel="prerelease"))
    svc = _svc(tmp_path, m, current="0.3.0")
    assert svc.check()["state"] == UpdateState.UP_TO_DATE.value


def test_apply_refuses_weak_verify(tmp_path, monkeypatch):
    weak = dict(MANIFEST, assets=[dict(MANIFEST["assets"][0], sha256="")])
    m = manifest_from_dict(weak, require_sha=False)
    svc = _svc(tmp_path, m, current="0.3.0")
    monkeypatch.setattr("mio_taskhub.version.is_frozen", lambda: True)
    svc._manifest = m
    svc._asset = m.asset_for("windows", "x64")
    svc._state = UpdateState.READY
    st = svc.apply()
    assert st["state"] == UpdateState.FAILED.value
    assert "弱校验" in st["error"]


def test_dismiss_emits_outside_lock(tmp_path):
    """dismiss 仍会发 dismissed 事件（emit 已移出锁，语义不变）。"""
    events = []
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST))
    svc._on_event = lambda ev: events.append(ev["kind"])
    svc.check()
    svc.dismiss()
    assert "update_dismissed" in events
    assert svc.status()["state"] == UpdateState.DISMISSED.value
