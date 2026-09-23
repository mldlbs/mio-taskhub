# -*- coding: utf-8 -*-
"""运行时 sentinel（更新健康确认依据）+ 启动残留恢复。"""
import json
import os
import time
from pathlib import Path

from mio_taskhub.update.apply import default_runtime_path, recover_residual as _recover


def write_runtime_state(version: str, port: int, path=None) -> None:
    """成功启动后写 sentinel（apply 进程据此判定"健康"）。"""
    p = Path(path or os.environ.get("MIO_UPDATE_STATE_PATH") or default_runtime_path())
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "version": version,
            "pid": os.getpid(),
            "started_at": time.time(),
            "port": port,
        }), encoding="utf-8")
    except OSError:
        pass


def recover_residual(install_dir=None):
    from mio_taskhub.version import install_dir as _id
    target = install_dir or str(_id())
    try:
        return _recover(target)
    except Exception:  # noqa: BLE001
        return []


def startup_recovery(install_dir=None, _frozen=None):
    """hub 启动时：清理 staging、必要时从 backup 恢复。仅打包产物执行。

    `_frozen` 为测试注入点（默认取 version.is_frozen()）。
    """
    if _frozen is None:
        from mio_taskhub.version import is_frozen as _frozen
    if not _frozen():
        return []
    return recover_residual(install_dir)
