"""Night Runner: 按夜间计划窗口自动拉起/回收 agent 进程。

职责边界：
- hub 只负责进程生命周期（spawn/kill），不注入业务逻辑
- agent 命令模板由用户配置（~/.mio_taskhub/night_runner.json）
- 窗口内每 agent 只 spawn 一次；窗口结束终止仍在跑的进程
- 幂等：重启 hub 不重复 spawn（按日期记账）
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("night_runner")

CONFIG_PATH = Path(os.path.expanduser("~/.mio_taskhub")) / "night_runner.json"
POLL_INTERVAL = 30  # 秒
DEFAULT_CONFIG = {
    "enabled": False,
    "window_start": "22:00",
    "window_end": "07:00",
    # 每项: {"agent": "opencode", "agent_type": "opencode", "command": "...", "cwd": ""}
    # command 支持 {url} {token} 占位符
    "agents": [],
}


def _hm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _now_minutes() -> int:
    n = datetime.now()
    return n.hour * 60 + n.minute


def _in_window(now_min: int, ws: int, we: int) -> bool:
    """跨夜窗口：22:00-07:00 表示 now>=1320 或 now<420"""
    if ws <= we:
        return ws <= now_min < we
    return now_min >= ws or now_min < we


def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(cfg, dict):
            raise ValueError("config must be an object")
        return {**DEFAULT_CONFIG, **cfg}
    except FileNotFoundError:
        return dict(DEFAULT_CONFIG)
    except Exception as e:
        logger.error(f"load_config failed: {e}")
        return dict(DEFAULT_CONFIG)


def save_config(cfg: dict) -> dict:
    clean = {
        "enabled": bool(cfg.get("enabled", False)),
        "window_start": str(cfg.get("window_start", DEFAULT_CONFIG["window_start"])),
        "window_end": str(cfg.get("window_end", DEFAULT_CONFIG["window_end"])),
        "agents": [a for a in cfg.get("agents", []) if isinstance(a, dict) and a.get("command")],
    }
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


class NightRunner:
    def __init__(self, poll_interval: float = POLL_INTERVAL):
        self.poll_interval = poll_interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._procs: Dict[str, "subprocess.Popen"] = {}   # agent -> 进程
        self._spawned_date: Optional[date] = None          # 今天已 spawn 过
        self._last_status: dict = {}

    # ---- 进程管理 -------------------------------------------------------
    def _spawn(self, agent_cfg: dict) -> bool:
        import subprocess
        name = agent_cfg.get("agent") or agent_cfg.get("agent_type") or f"agent{len(self._procs)}"
        if name in self._procs and self._procs[name].poll() is None:
            logger.info(f"{name} already running, skip")
            return False
        cmd = agent_cfg["command"]
        url = os.environ.get("MIO_TASKHUB_URL", "http://127.0.0.1:48620")
        token = os.environ.get("MIO_TASKHUB_TOKEN", "")
        cmd = cmd.replace("{url}", url).replace("{token}", token)
        cwd = agent_cfg.get("cwd") or None
        try:
            proc = subprocess.Popen(cmd, shell=True, cwd=cwd,
                                    env={**os.environ, "MIO_TASKHUB_URL": url})
            self._procs[name] = proc
            logger.info(f"spawned {name}: pid={proc.pid}")
            return True
        except Exception as e:
            logger.error(f"spawn {name} failed: {e}")
            return False

    def _reap_finished(self):
        for name, p in list(self._procs.items()):
            if p.poll() is not None:
                logger.info(f"{name} exited code={p.returncode}")
                del self._procs[name]

    def stop_agents(self):
        for name, p in list(self._procs.items()):
            if p.poll() is None:
                try:
                    p.terminate()
                    logger.info(f"terminated {name}")
                except Exception as e:
                    logger.error(f"terminate {name} failed: {e}")
            del self._procs[name]

    def status(self) -> dict:
        return {
            "running_agents": {n: p.pid for n, p in self._procs.items() if p.poll() is None},
            "spawned_date": self._spawned_date.isoformat() if self._spawned_date else None,
            **self._last_status,
        }

    # ---- 调度逻辑 -------------------------------------------------------
    def tick(self):
        cfg = load_config()
        if not cfg["enabled"]:
            self.stop_agents() if self._procs else None
            self._last_status = {"enabled": False}
            return
        ws = _hm_to_min(cfg["window_start"])
        we = _hm_to_min(cfg["window_end"])
        now = _now_minutes()
        inside = _in_window(now, ws, we)

        self._reap_finished()

        if not inside:
            # 出窗：回收进程、重置记账
            if self._procs:
                self.stop_agents()
            self._spawned_date = None
            self._last_status = {"enabled": True, "in_window": False}
            return

        # 入窗且今天未启动过 → 全量 spawn
        today = date.today()
        if self._spawned_date != today:
            spawned = []
            for a in cfg["agents"]:
                if self._spawn(a):
                    spawned.append(a.get("agent") or a.get("agent_type"))
            self._spawned_date = today
            logger.info(f"night shift started: {spawned}")

        self._last_status = {"enabled": True, "in_window": True,
                             "window": f'{cfg["window_start"]}-{cfg["window_end"]}'}

    def _loop(self):
        while not self._stop.wait(self.poll_interval):
            try:
                self.tick()
            except Exception as e:
                logger.error(f"tick error: {e}")

    # ---- 生命周期 -------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="night-runner")
        self._thread.start()
        logger.info("night runner started")

    def shutdown(self):
        self._stop.set()
        self.stop_agents()


_runner: Optional[NightRunner] = None


def start_night_runner() -> NightRunner:
    global _runner
    if _runner is None:
        _runner = NightRunner()
    _runner.start()
    return _runner


def stop_night_runner():
    global _runner
    if _runner:
        _runner.shutdown()


def get_runner() -> Optional[NightRunner]:
    return _runner
