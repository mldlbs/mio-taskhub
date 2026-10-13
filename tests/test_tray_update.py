# -*- coding: utf-8 -*-
"""托盘更新点击：逐步日志 + 结果通知（回归「静默失败 / failed 态点击无反应」）。"""
import importlib.util
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_HUB = ROOT / "packaging" / "run_hub.py"


def _load_run_hub():
    spec = importlib.util.spec_from_file_location("run_hub_tray_test", RUN_HUB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Icon:
    def __init__(self):
        self.msgs = []

    def notify(self, msg, title=None):
        self.msgs.append(msg)


class FakeSvc:
    """按脚本推进状态的假 UpdateService。"""

    def __init__(self, initial="idle", script=None):
        self.st = {"state": initial, "error": "", "latest": "0.5.0",
                   "current": "0.4.0", "progress": 0}
        self.script = script or {}
        self.calls = []

    def status(self):
        return dict(self.st)

    def check(self):
        self.calls.append("check")
        self.st.update(self.script.get("check", {}))
        return dict(self.st)

    def download(self):
        self.calls.append("download")
        self.st.update(self.script.get("download", {}))
        return dict(self.st)

    def apply(self):
        self.calls.append("apply")
        self.st.update(self.script.get("apply", {}))
        return dict(self.st)


def _click(monkeypatch, mod, svc):
    from mio_taskhub.update import service as svc_mod
    monkeypatch.setattr(svc_mod, "get_service", lambda: svc)
    icon = _Icon()
    logs = []
    monkeypatch.setattr(mod, "_log", lambda msg, role="tray": logs.append(msg))
    mod._update_busy.clear()
    mod._on_update_clicked(icon=icon)
    for _ in range(300):            # _work 在 daemon 线程；busy 清除即完成
        if not mod._update_busy.is_set():
            break
        time.sleep(0.01)
    else:
        pytest.fail("_work 未在 3s 内结束")
    return icon, logs


def test_up_to_date_click_notifies_and_skips_download(monkeypatch):
    mod = _load_run_hub()
    svc = FakeSvc(initial="idle",
                  script={"check": {"state": "up_to_date", "latest": "0.5.0"}})
    icon, logs = _click(monkeypatch, mod, svc)
    assert svc.calls == ["check"]
    assert any("已是最新" in m for m in icon.msgs)
    assert any("tray: check ->" in l for l in logs)


def test_check_failed_click_notifies_error(monkeypatch):
    mod = _load_run_hub()
    svc = FakeSvc(initial="check_failed",
                  script={"check": {"state": "check_failed", "error": "网络超时"}})
    icon, _logs = _click(monkeypatch, mod, svc)
    assert svc.calls == ["check"]
    assert any("检查更新失败" in m and "网络超时" in m for m in icon.msgs)


def test_failed_state_click_retries_instead_of_silent(monkeypatch):
    """回归：state=failed 时点击不再静默 return，且失败要 toast + 落日志。"""
    mod = _load_run_hub()
    svc = FakeSvc(initial="failed",
                  script={"check": {"state": "available"},
                          "download": {"state": "failed",
                                       "error": "download failed after 3 attempts"}})
    icon, logs = _click(monkeypatch, mod, svc)
    assert svc.calls == ["check", "download"]
    assert any("更新失败" in m and "download failed" in m for m in icon.msgs)
    assert any("tray: download -> state=failed" in l for l in logs)


def test_ready_click_applies_and_notifies_restart(monkeypatch):
    mod = _load_run_hub()
    svc = FakeSvc(initial="ready", script={"apply": {"state": "done"}})
    icon, logs = _click(monkeypatch, mod, svc)
    assert svc.calls == ["apply"]
    assert any("即将重启" in m for m in icon.msgs)
    assert any("tray: apply -> state=done" in l for l in logs)


def test_available_flow_check_download_apply(monkeypatch):
    mod = _load_run_hub()
    svc = FakeSvc(initial="available",
                  script={"check": {"state": "available"},
                          "download": {"state": "ready"},
                          "apply": {"state": "done"}})
    _icon, logs = _click(monkeypatch, mod, svc)
    assert svc.calls == ["check", "download", "apply"]
    assert any("tray: update clicked" in l for l in logs)


def test_non_actionable_state_ignored_and_logged(monkeypatch):
    mod = _load_run_hub()
    svc = FakeSvc(initial="downloading")
    icon, logs = _click(monkeypatch, mod, svc)
    assert svc.calls == []
    assert icon.msgs == []
    assert any("non-actionable" in l for l in logs)


@pytest.mark.parametrize("state,expect", [
    ("applying", "正在应用更新"),
    ("done", "重启"),
    ("failed", "重试"),
])
def test_menu_label_shows_progress_states(monkeypatch, state, expect):
    """回归（2026-10-06「看不到进度」）：applying/done/failed 态托盘文案不得落回「检查更新」。"""
    mod = _load_run_hub()
    from mio_taskhub.update import service as svc_mod
    svc = FakeSvc(initial=state)
    monkeypatch.setattr(svc_mod, "get_service", lambda: svc)
    assert expect in mod._update_menu_label()
