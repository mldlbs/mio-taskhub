# -*- coding: utf-8 -*-
"""Observer 采集调度器状态与一键激活（素材生产链路的运维面）。

背景（2026-10-10）：观察层素材的真正生产者是 `observer serve`（research scheduler，
collector 定时采集 + 每日一次 pipeline），它独立于 `observe --start` 的被动观察器
daemon。两个进程都可能静默死亡，且 `observe --status` 对带 --base-dir 的 serve
恒显 not running（pidfile 机制对不上）。

因此状态判定不信任 pidfile，直接扫描 node.exe 进程命令行：
- serve  匹配：命令行同时含 mio.js 与 `observer`+`serve`
- daemon 匹配：命令行含 mio.js 且有独立 token `observe`（排除 serve）

三个端点（tags: mio-runtime）：
- GET  /api/v1/mio/scheduler-status   状态快照（进程 + 今日数据新鲜度）
- POST /api/v1/mio/scheduler-activate 一键激活（只补缺，不重复启动）
- POST /api/v1/mio/scheduler-restart  重启（先 taskkill 再激活）
- POST /api/v1/mio/scheduler-stop     全停
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter

from mio_taskhub import mio_runtime

logger = logging.getLogger("mio_taskhub.scheduler_control")

router = APIRouter(prefix="/api/v1/mio", tags=["mio-runtime"])

# Windows 进程创建标志：脱离控制台 + 新进程组 + 无窗口
_DETACHED = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
# 子进程扫描/kill 不弹控制台黑框（exe 无头运行时 subprocess 默认继承会弹新窗口）
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_HERMES_NODE = r"C:\Users\admin\AppData\Local\hermes\node\node.exe"


def _node_exe() -> Optional[str]:
    """node 可执行文件：env 覆盖 > PATH > hermes 内置。"""
    env = (os.environ.get("MIO_NODE_EXE") or "").strip()
    if env and Path(env).is_file():
        return env
    w = shutil.which("node")
    if w:
        return w
    return _HERMES_NODE if Path(_HERMES_NODE).is_file() else None


def _cli_cmd(base_args: List[str]) -> Optional[List[str]]:
    """基于 mio_runtime.mio_cli() 组装完整命令（node 直调优先）。"""
    cli = mio_runtime.mio_cli()
    if not cli:
        return None
    node = _node_exe()
    cmd = list(cli)
    # mio_cli() 首元素可能是裸 "node"——替换成绝对路径，脱离 hub 的 PATH 环境也能跑
    if cmd and cmd[0] == "node":
        if not node:
            return None
        cmd[0] = node
    return cmd + base_args


# ── 进程扫描 ──────────────────────────────────────────────

def _node_processes() -> List[Dict]:
    """扫描 node.exe 进程，返回 [{pid, cmdline}]。wmic 优先，PowerShell 兜底。"""
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='node.exe'", "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=15, creationflags=_NO_WINDOW,
        )
        if r.returncode == 0 and r.stdout:
            out = []
            for line in r.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("Node,") or line.lower().startswith("node,commandline"):
                    continue
                # csv: Node,CommandLine,ProcessId
                parts = line.split(",", 2)
                if len(parts) >= 3 and parts[2].strip().isdigit():
                    out.append({"pid": int(parts[2].strip()), "cmdline": parts[1]})
            if out or "No Instance(s) Available" in r.stdout:
                return out
    except Exception:  # noqa: BLE001
        pass
    # 兜底：PowerShell Get-CimInstance
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
             "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
            capture_output=True, text=True, timeout=20, creationflags=_NO_WINDOW,
        )
        out = []
        for line in r.stdout.splitlines():
            pid, _, cmd = line.partition("\t")
            if pid.strip().isdigit():
                out.append({"pid": int(pid.strip()), "cmdline": cmd.strip()})
        return out
    except Exception:  # noqa: BLE001
        return []


def _classify(procs: List[Dict]) -> Dict[str, Optional[Dict]]:
    serve = daemon = None
    for p in procs:
        cmd = p.get("cmdline") or ""
        if "mio.js" not in cmd and "mio-agent-runtime" not in cmd:
            continue
        tokens = set(re.split(r"[\s\"']+", cmd))
        if "serve" in tokens and "observer" in tokens:
            serve = serve or p
        elif "observe" in tokens and "serve" not in tokens:
            daemon = daemon or p
    return {"serve": serve, "daemon": daemon}


# ── 数据新鲜度 ────────────────────────────────────────────

def _today_data(base: Path) -> Dict:
    """今日 observations 落盘情况。mtime 是「最近采集」的地面真相。"""
    f = base / "observations" / f"{date.today().isoformat()}.json"
    out: Dict = {"file": str(f), "exists": f.is_file(), "count": 0,
                 "by_source": {}, "last_write_at": None, "minutes_since_write": None}
    if not f.is_file():
        return out
    try:
        r = json.loads(f.read_text(encoding="utf-8", errors="replace"))
        items = r.get("observations") or []
        by_src: Dict[str, int] = {}
        for it in items:
            if isinstance(it, dict):
                by_src[str(it.get("source") or "?")] = by_src.get(str(it.get("source") or "?"), 0) + 1
        out.update(count=len(items), by_source=by_src)
    except Exception:  # noqa: BLE001
        pass
    mtime = datetime.fromtimestamp(f.stat().st_mtime)
    out["last_write_at"] = mtime.isoformat(timespec="seconds")
    out["minutes_since_write"] = round((datetime.now() - mtime).total_seconds() / 60, 1)
    return out


def _status() -> Dict:
    procs = _node_processes()
    found = _classify(procs)
    base = Path(mio_runtime.observer_base_dir())
    data = _today_data(base)

    def info(p: Optional[Dict]) -> Dict:
        return {"running": p is not None, "pid": p["pid"] if p else None,
                "cmdline": (p.get("cmdline") or "")[:200] if p else None}

    # 健康判定：serve 在跑 + 今日有落盘 + 落盘距今 < 8h（collector 最大间隔 4h 的两倍容忍）
    stale_min = data.get("minutes_since_write")
    healthy = bool(found["serve"]) and data["exists"] and isinstance(stale_min, (int, float)) and stale_min < 480
    return {
        "base_dir": str(base),
        "research_scheduler": info(found["serve"]),
        "observer_daemon": info(found["daemon"]),
        "data": data,
        "healthy": healthy,
        "hint": None if healthy else (
            "研究调度器未运行或数据过期——点「一键激活」补启 serve 进程"
            if not found["serve"] else
            "serve 进程在跑但今日无新数据：检查网络（GH API/scrape 失败会静默空转）"),
    }


@router.get("/scheduler-status")
def scheduler_status():
    """采集调度器状态快照（进程 + 今日数据新鲜度）。"""
    return _status()


@router.post("/scheduler-activate")
def scheduler_activate():
    """一键激活：补启缺失的 serve / daemon，已在跑的不动（幂等）。"""
    found = _classify(_node_processes())
    started: List[str] = []
    already: List[str] = []
    errors: List[str] = []
    base = Path(mio_runtime.observer_base_dir())
    log_file = base / "serve.log"

    jobs = [
        ("serve", ["observer", "serve", "--base-dir", str(base)], found["serve"]),
        ("daemon", ["observe", "--start"], found["daemon"]),
    ]
    for name, args, proc in jobs:
        if proc:
            already.append(name)
            continue
        cmd = _cli_cmd(args)
        if not cmd:
            errors.append(f"{name}: mio CLI / node 不可解析")
            continue
        try:
            base.mkdir(parents=True, exist_ok=True)
            kwargs: dict = {"creationflags": _DETACHED, "cwd": str(base.parent),
                            "stdin": subprocess.DEVNULL}
            if name == "serve":
                kwargs["stdout"] = open(log_file, "ab")
                kwargs["stderr"] = subprocess.STDOUT
            p = subprocess.Popen(cmd, **kwargs)
            started.append(f"{name} (pid {p.pid})")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: {e}")

    return {"started": started, "already_running": already, "errors": errors,
            "status": _status()}


def _kill(pid: int) -> bool:
    try:
        r = subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, text=True, timeout=10,
                           creationflags=_NO_WINDOW)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


@router.post("/scheduler-stop")
def scheduler_stop():
    """全停：杀掉 serve 与 daemon 进程。"""
    found = _classify(_node_processes())
    killed, failed = [], []
    for name in ("serve", "daemon"):
        p = found.get(name)
        if not p:
            continue
        (killed if _kill(p["pid"]) else failed).append(f"{name} (pid {p['pid']})")
    return {"killed": killed, "failed": failed, "status": _status()}


@router.post("/scheduler-restart")
def scheduler_restart():
    """重启 = 全停 + 激活。调度器静默死亡后的标准恢复动作。"""
    stop = scheduler_stop()
    act = scheduler_activate()
    return {"stopped": stop, "activated": act, "status": act["status"]}
