# -*- coding: utf-8 -*-
"""生产 apply_runner：以独立进程触发 --apply-update，并请求 hub 优雅退出。

架构：hub 进程 → 本 runner 先把 updater **拷到临时 spool 目录**再 spawn（mio-taskhub.exe
apply-update …）→ 请求 hub 优雅退出 → updater 等 hub PID 退出 → 替换 → 重启 → 健康确认。
本 runner 的返回值仅表示"已成功触发"，**不代表更新成功**（成功以 updater 的 sentinel 健康确认为准，
见 apply.run_apply_update 与 ~/.mio_taskhub/update/apply.log）。
"""
import os
import subprocess
import sys
import tempfile
import time
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


def _spool_root() -> Path:
    return Path(tempfile.gettempdir()) / "mio-taskhub-upd"


def _prune_spools(root: Path, max_age_s: float = 3600.0) -> None:
    """清理超过 max_age_s 的旧 spool（正常情况下 updater 已退出，可安全删除）。"""
    now = time.time()
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for d in entries:
        try:
            if now - d.stat().st_mtime > max_age_s:
                import shutil
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _prepare_spool(install: Path) -> Optional[Path]:
    """把 updater 运行所需文件（exe + _internal）拷到临时 spool 目录，返回 spool 路径。

    必要性（2026-10-06 探针实测 PermissionError(13)）：Windows 不允许 rename 一个
    内含「正在运行的 exe 及其映像 DLL」的目录 —— 若 updater 直接从 install 内 spawn，
    execute_replace 的 install→backup rename 必然失败 →「下载完安装不了」。
    spool 与 install 不在同一目录树，rename install 时无任何映像句柄钉住它。
    """
    import shutil
    try:
        root = _spool_root()
        root.mkdir(parents=True, exist_ok=True)
        _prune_spools(root)
        spool = root / ("%d-%d" % (os.getpid(), int(time.time() * 1000)))
        spool.mkdir()
        copied = False
        for item in ("mio-taskhub.exe", "_internal"):
            src = install / item
            if src.is_file():
                shutil.copy2(src, spool / item)
                copied = True
            elif src.is_dir():
                shutil.copytree(src, spool / item)
                copied = True
        if not copied or not (spool / "mio-taskhub.exe").exists():
            shutil.rmtree(spool, ignore_errors=True)
            return None
        return spool
    except OSError:
        return None


def default_apply_runner(install, zip_path, manifest, hub_pid) -> int:
    """spawn 独立 updater（从 spool 运行）；返回 0=已触发，1=无法触发。"""
    install = Path(install)
    if not (install / "mio-taskhub.exe").exists():
        return 1
    spool = _prepare_spool(install)
    if spool is None:
        return 1
    exe = spool / "mio-taskhub.exe"
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
        # DETACHED | NEW_PROCESS_GROUP | CREATE_BREAKAWAY_JOB：
        # supervisor 为 worker 建了 KILL_ON_JOB_CLOSE 的 Job，HTTP apply 路径下本
        # runner 在 job 内 spawn updater，若不脱离，hub 退出 CloseHandle(job) 时
        # 内核会把正在执行替换的 updater 连带杀死（2026-10-06「下载完安装不了」根因）。
        # 调用方不在任何 job 时该 flag 被 CreateProcess 忽略（开发态/测试不受影响）；
        # 所在 job 未开 BREAKAWAY_OK 时 CreateProcess 抛 ACCESS_DENIED → OSError → rc=1。
        creation = 0x00000008 | 0x00000200 | 0x01000000
    try:
        subprocess.Popen(args, cwd=str(spool), creationflags=creation, close_fds=True)
    except OSError:
        return 1
    _request_exit()
    return 0
