# -*- coding: utf-8 -*-
"""UpdateService：更新状态机与编排（单例）。

状态：idle→checking→up_to_date|needs_manual|available|dismissed|check_failed
      available→downloading→ready→applying→done|failed
"""
import json
import os
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Callable

from mio_taskhub.version import __version__, install_dir as _version_install_dir
from mio_taskhub.update.manifest import is_newer, parse_version


def _below_min_supported(current: str, min_supported: str) -> bool:
    """current < min_supported → True（任一解析失败按 False，即不拦截）。"""
    c = parse_version(current)
    m = parse_version(min_supported)
    if c is None or m is None:
        return False
    return c < m


def _configured_channel() -> str:
    return (os.environ.get("MIO_UPDATE_CHANNEL") or "stable").strip().lower()


class UpdateState(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    NEEDS_MANUAL = "needs_manual"
    AVAILABLE = "available"
    DISMISSED = "dismissed"
    CHECK_FAILED = "check_failed"
    DOWNLOADING = "downloading"
    READY = "ready"
    APPLYING = "applying"
    DONE = "done"
    FAILED = "failed"


def _default_prefs_path() -> Path:
    return Path.home() / ".mio_taskhub" / "update" / "prefs.json"


def _default_update_dir() -> Path:
    return Path.home() / ".mio_taskhub" / "updates"


class UpdateService:
    def __init__(self, source=None, downloader: Callable = None,
                 install_dir=None, current_version: str = None,
                 prefs_path=None, update_dir=None, apply_runner: Callable = None,
                 on_event: Callable[[dict], None] = None):
        from mio_taskhub.update.source import default_source
        from mio_taskhub.update.downloader import download as _download
        self.source = source or default_source()
        self._download = downloader or (
            lambda url, dest, sha, size=None, progress=None:
            _download(url, dest, sha, size=size, progress=progress))
        # 注意：参数名 install_dir 会遮蔽 version.install_dir()，故导入时别名化
        self.install = Path(install_dir) if install_dir else _version_install_dir()
        self.current = current_version or __version__
        self.prefs_path = Path(prefs_path) if prefs_path else _default_prefs_path()
        self.update_dir = Path(update_dir) if update_dir else _default_update_dir()
        self._apply_runner = apply_runner
        self._on_event = on_event
        self._lock = threading.Lock()
        self._state = UpdateState.IDLE
        self._manifest = None
        self._asset = None
        self._error = ""
        self._progress = 0
        self._dismissed = self._load_dismissed()
        self._thread = None

    # ── prefs ──
    def _load_dismissed(self) -> str:
        try:
            return str(json.loads(self.prefs_path.read_text(encoding="utf-8"))
                       .get("dismissed_version") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _save_dismissed(self) -> None:
        try:
            self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
            self.prefs_path.write_text(
                json.dumps({"dismissed_version": self._dismissed}),
                encoding="utf-8")
        except OSError:
            pass

    def _emit(self, kind: str) -> None:
        if self._on_event:
            try:
                self._on_event({"kind": kind, "status": self.status()})
            except Exception:  # noqa: BLE001
                pass

    # ── 状态 ──
    def status(self) -> dict:
        m = self._manifest
        a = self._asset
        return {
            "state": self._state.value,
            "current": self.current,
            "latest": m.version if m else None,
            "notes": m.notes if m else "",
            "mandatory": bool(m.mandatory) if m else False,
            "min_supported": m.min_supported if m else None,
            "asset_sha256": a.sha256 if a else "",
            "asset_url": a.url if a else "",
            "asset_size": a.size if a else 0,
            "weak_verify": bool(m.weak_verify) if m else False,
            "progress": self._progress,
            "error": self._error,
            "dismissed_version": self._dismissed,
        }

    # ── 动作 ──
    def check(self) -> dict:
        with self._lock:
            self._state = UpdateState.CHECKING
            self._error = ""
        try:
            m = self.source.fetch_manifest()
        except Exception as e:  # noqa: BLE001 —— 静默失败
            with self._lock:
                self._state = UpdateState.CHECK_FAILED
                self._error = str(e)
            self._emit("update_check_failed")
            return self.status()

        asset = m.asset_for("windows", "x64")
        with self._lock:
            self._manifest = m
            self._asset = asset
            if asset is None:
                self._state = UpdateState.CHECK_FAILED
                self._error = "manifest 无 windows/x64 资产"
            elif m.channel.strip().lower() != _configured_channel():
                self._state = UpdateState.UP_TO_DATE
            elif not is_newer(self.current, m.version):
                self._state = UpdateState.UP_TO_DATE
            elif _below_min_supported(self.current, m.min_supported):
                self._state = UpdateState.NEEDS_MANUAL
            elif self._dismissed == m.version:
                self._state = UpdateState.DISMISSED
            else:
                self._state = UpdateState.AVAILABLE
        self._emit("update_" + self._state.value)
        return self.status()

    def dismiss(self) -> dict:
        emitted = False
        with self._lock:
            if self._manifest:
                self._dismissed = self._manifest.version
                self._save_dismissed()
                self._state = UpdateState.DISMISSED
                emitted = True
        if emitted:
            self._emit("update_dismissed")
        return self.status()

    def download(self) -> dict:
        with self._lock:
            if self._manifest is None or self._asset is None:
                self._state = UpdateState.FAILED
                self._error = "尚未检查更新"
                return self.status()
            self._state = UpdateState.DOWNLOADING
            self._progress = 0
        url = self._asset.url
        dest = self.update_dir / ("mio-taskhub-%s.zip" % self._manifest.version)
        try:
            self._download(url, dest, self._asset.sha256, size=self._asset.size,
                           progress=self._on_progress)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._state = UpdateState.FAILED
                self._error = str(e)
            self._emit("update_failed")
            return self.status()
        with self._lock:
            self._state = UpdateState.READY
            self._progress = 100
        self._emit("update_ready")
        return self.status()

    def _on_progress(self, done: int, total: int) -> None:
        self._progress = int(done * 100 / total) if total else 0
        self._emit("update_progress")

    def apply(self) -> dict:
        from mio_taskhub.version import is_frozen
        with self._lock:
            if not is_frozen():
                self._state = UpdateState.FAILED
                self._error = "开发模式不支持自更新（仅打包产物）"
                return self.status()
            if self._state != UpdateState.READY or self._manifest is None:
                self._state = UpdateState.FAILED
                self._error = "尚未下载完成"
                return self.status()
            if self._manifest.weak_verify:
                self._state = UpdateState.FAILED
                self._error = "弱校验包（无 sha256）不支持自动更新，请手动更新"
                return self.status()
            self._state = UpdateState.APPLYING
        zip_path = self.update_dir / ("mio-taskhub-%s.zip" % self._manifest.version)
        if self._apply_runner is None:
            with self._lock:
                self._state = UpdateState.FAILED
                self._error = "未配置更新执行器（apply_runner）"
            self._emit("update_failed")
            return self.status()
        rc = self._apply_runner(self.install, zip_path, self._manifest, os.getpid())
        with self._lock:
            self._state = UpdateState.DONE if rc == 0 else UpdateState.FAILED
            if rc != 0:
                self._error = "应用更新失败（详见 apply.log）"
        self._emit("update_" + self._state.value)
        return self.status()

    def start_background(self, delay: float = 20.0, interval_h: float = 6.0) -> None:
        """启动后台线程：延迟 delay 首查，此后每 interval_h 小时。"""
        if os.environ.get("MIO_UPDATE_DISABLED", "").strip() in ("1", "true", "yes"):
            return
        if self._thread and self._thread.is_alive():
            return

        def _loop():
            time.sleep(delay)
            while True:
                try:
                    self.check()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(max(60.0, interval_h * 3600.0))

        self._thread = threading.Thread(target=_loop, name="update-check", daemon=True)
        self._thread.start()


# ── 单例 ──
_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def _on_update_event(ev: dict) -> None:
    """把更新状态变化广播到 WS（前端更新条据此即时刷新）。"""
    try:
        from mio_taskhub.events import broadcast_json
        broadcast_json({"type": "update_status",
                        "kind": (ev or {}).get("kind", ""),
                        "status": (ev or {}).get("status") or {}})
    except Exception:  # noqa: BLE001
        pass


def get_service() -> UpdateService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            from mio_taskhub.update.runner import default_apply_runner
            _SERVICE = UpdateService(apply_runner=default_apply_runner,
                                     on_event=_on_update_event)
        return _SERVICE


def reset_service_for_test(svc: UpdateService = None) -> None:
    global _SERVICE
    _SERVICE = svc
