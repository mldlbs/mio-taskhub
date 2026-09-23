# -*- coding: utf-8 -*-
"""生产 apply_runner：以独立进程触发 --apply-update，并请求 hub 优雅退出。

架构：hub 进程 → 本 runner spawn 独立的 updater 进程（mio-taskhub.exe apply-update …）
→ 请求 hub 优雅退出 → updater 等 hub PID 退出 → 替换 → 重启 → 健康确认。
本 runner 的返回值仅表示"已成功触发"，**不代表更新成功**（成功以 updater 的 sentinel 健康确认为准，
见 apply.run_apply_update 与 ~/.mio_taskhub/update/apply.log）。
"""
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

from mio_taskhub.update.apply import default_runtime_path

_EXIT_CALLBACK: Optional[Callable[[], None]] = None


def set_exit_callback(cb: Optional[Callable[[], None]]) -> None:
    """由 hub 启动器注册：请求进程优雅退出（如 uvicorn server.should_exit=True）。"""
    global _EXIT_CALLBACK
    _EXIT_CALLBACK = cb


def _request_exit() -> None:
    cb = _EXIT_CALLBACK
    if cb is None:
        return
    try:
        cb()
    except Exception:  # noqa: BLE001
        pass


def default_apply_runner(install, zip_path, manifest, hub_pid) -> int:
    """spawn 独立 updater；返回 0=已触发，1=无法触发。"""
    install = Path(install)
    exe = install / "mio-taskhub.exe"
    if not exe.exists():
        return 1
    asset = manifest.asset_for("windows", "x64")
    sentinel = os.environ.get("MIO_UPDATE_STATE_PATH") or str(default_runtime_path())
    args = [
        str(exe), "apply-update",
        "--zip", str(zip_path),
        "--sha256", (asset.sha256 if asset else ""),
        "--version", manifest.version,
        "--pid", str(hub_pid),
        "--target", str(install),
        "--runtime-json", sentinel,
    ]
    creation = 0
    if sys.platform == "win32":
        creation = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, cwd=str(install), creationflags=creation, close_fds=True)
    except OSError:
        return 1
    _request_exit()
    return 0
