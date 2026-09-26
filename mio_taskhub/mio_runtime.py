# -*- coding: utf-8 -*-
"""Mio Agent Runtime 低耦合适配（只读文件 + CLI 白名单 + digest 定时）。

设计原则（低耦合）：
- **不 import** 运行时包、**不依赖 Node**；仅通过两处边界交互：
  1. 只读 `MIO_HOME` 下的 JSONL/配置（容错解析，schema 变动不炸）；
  2. 可选的 CLI 子进程（白名单 + 超时 + 失败静默降级）。
- 运行时缺失/换机/未安装 → 一切接口返回 `available: false`，**不影响 taskhub**。
- 绝不外泄敏感字段：`config.json` 的 `llm`（含明文 apiKey）一律剔除。
- **契约冒烟**（ContractJob）：定时跑 4 条白名单只读 CLI 并断言关键输出字段——
  runtime 升级改了 schema → 告警可见，而不是 policy 门控静默失效 / digest 悄悄停更。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from mio_taskhub.scheduling.scheduler import Scheduler

logger = logging.getLogger("mio_taskhub.mio_runtime")

DEFAULT_HOME = Path.home() / ".mio-intelligence"
_TRUE = ("1", "true", "yes", "on")
_MAX_READ = 8 * 1024 * 1024          # 单文件读取上限（防超大文件拖垮）
# 允许经 CLI 调用的命令白名单（只读或低风险；不含任何写密钥/删除/LLM 消耗类）
CLI_WHITELIST = ("status", "digest", "traces", "recall", "observe", "policy")
# 需两级判定的只读子命令（creativity/insight 的 generate/ferment 会烧 LLM，故不放行）
CLI_SUBCOMMANDS = {
    ("creativity", "status"), ("creativity", "list"),
    ("insight", "status"), ("insight", "list"),
}


def home() -> Path:
    """MIO_HOME（env 覆盖，默认 ~/.mio-intelligence）。"""
    return Path(os.environ.get("MIO_HOME") or DEFAULT_HOME)


def available() -> bool:
    return home().is_dir()


# ── 只读文件 ──────────────────────────────────────────────────────────────

def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 —— 配置缺失/损坏都当空
        return {}


def config_sanitized() -> dict:
    """config.json 的安全子集：剔除 `llm`（含明文 apiKey）等敏感字段。"""
    cfg = _read_json(home() / "config.json")
    agents = cfg.get("agents") or {}
    safe_agents = {}
    if isinstance(agents, dict):
        for name, v in agents.items():
            v = v if isinstance(v, dict) else {}
            safe_agents[name] = {"installedAt": v.get("installedAt"),
                                 "configPath": v.get("configPath")}
    return {"version": cfg.get("version"), "home": cfg.get("home"),
            "createdAt": cfg.get("createdAt"), "agents": safe_agents}


def _tail_jsonl(name: str, limit: int = 20) -> List[dict]:
    """容错读取 JSONL 末尾 limit 条（坏行跳过）。返回**新的在前**。"""
    p = home() / name
    if not p.is_file():
        return []
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > _MAX_READ:
                f.seek(size - _MAX_READ)
                f.readline()                      # 丢弃可能被截断的首行
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    out.reverse()
    return out[:max(1, min(limit, 200))]


def _file_stat(name: str) -> dict:
    p = home() / name
    try:
        st = p.stat()
        return {"name": name, "exists": True, "bytes": st.st_size,
                "mtime": int(st.st_mtime)}
    except OSError:
        return {"name": name, "exists": False, "bytes": 0, "mtime": None}


def _count_records(name: str) -> int:
    """有效 JSONL 记录数（跳过空行与坏行），与 _tail_jsonl 的口径一致。"""
    p = home() / name
    if not p.is_file():
        return 0
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > _MAX_READ:
                f.seek(size - _MAX_READ)
                f.readline()
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return 0
    n = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            json.loads(line)
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def _pid_alive(pid: int) -> bool:
    try:
        from mio_taskhub.update.apply import pid_alive
        return pid_alive(pid)
    except Exception:  # noqa: BLE001
        return False


def observer_state() -> dict:
    """观察器状态：优先读 observe.pid 并校验进程存活。"""
    p = home() / "observe.pid"
    try:
        pid = int((p.read_text(encoding="utf-8", errors="replace") or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        pid = 0
    if pid and _pid_alive(pid):
        return {"running": True, "pid": pid}
    return {"running": False, "pid": pid or None}


# ── 汇总 / 明细 ───────────────────────────────────────────────────────────

def status() -> dict:
    if not available():
        return {"available": False, "home": str(home())}
    files = ["memory.jsonl", "traces.jsonl", "experience_reuse.jsonl",
             "ideas.jsonl", "queries.jsonl"]
    return {
        "available": True,
        "home": str(home()),
        "config": config_sanitized(),
        "observer": observer_state(),
        "cli": {"available": mio_cli() is not None,
                "path": (mio_cli() or [None])[0]},
        "files": [{**_file_stat(n), "records": _count_records(n)} for n in files],
    }


def traces(limit: int = 20) -> dict:
    if not available():
        return {"available": False, "items": []}
    return {"available": True, "items": _tail_jsonl("traces.jsonl", limit)}


def memory(limit: int = 20) -> dict:
    if not available():
        return {"available": False, "items": []}
    return {"available": True, "items": _tail_jsonl("memory.jsonl", limit)}


# ── CLI（白名单 + 超时 + 降级）────────────────────────────────────────────

def mio_cli() -> Optional[List[str]]:
    """解析 mio CLI 命令。优先 env MIO_CLI（可为 .cmd 或 .js 路径）。"""
    override = (os.environ.get("MIO_CLI") or "").strip()
    if override:
        return ["node", override] if override.lower().endswith(".js") else [override]
    for name in ("mio.cmd", "mio.bat", "mio"):
        p = shutil.which(name)
        if p:
            return [p]
    return None


def _check_allowed(args: List[str]) -> Optional[str]:
    """白名单校验（允许前置全局 flag `--json`）。返回 None=允许，否则返回原因。"""
    a = list(args)
    while a and a[0] in ("--json", "-j"):
        a = a[1:]
    if not a:
        return "empty command"
    if a[0] in CLI_WHITELIST:
        return None
    if len(a) >= 2 and (a[0], a[1]) in CLI_SUBCOMMANDS:
        return None
    return "subcommand not allowed"


def _json_stdout(args: List[str], timeout: float = 300.0) -> tuple:
    """跑 CLI 并解析 stdout 的 JSON。返回 (ok, data)。失败/为空 → (False, None)。"""
    res = run_mio(args, timeout=timeout)
    if not res["ok"] or not (res["stdout"] or "").strip():
        return False, None
    try:
        return True, json.loads(res["stdout"])
    except Exception:  # noqa: BLE001
        return False, None


def creativity(limit: int = 20) -> dict:
    """创意假设（只读）：status 计数 + 假设列表（含 novelty/feasibility/impact/score）。"""
    if not available():
        return {"available": False, "status": {}, "items": []}
    _, status = _json_stdout(["--json", "creativity", "status"])
    _, items = _json_stdout(["--json", "creativity", "list",
                             "--limit", str(max(1, min(int(limit), 100)))])
    return {"available": True, "status": status or {},
            "items": items if isinstance(items, list) else []}


def insight(limit: int = 20) -> dict:
    """洞察（只读）：status 计数 + 洞察列表。生成走 MCP（mio.insight.generate）。"""
    if not available():
        return {"available": False, "status": {}, "items": []}
    _, status = _json_stdout(["--json", "insight", "status"])
    _, items = _json_stdout(["--json", "insight", "list",
                             "--limit", str(max(1, min(int(limit), 100)))])
    return {"available": True, "status": status or {},
            "items": items if isinstance(items, list) else []}


def _project_name() -> str:
    """项目名：env MIO_PROJECT → 含 .git 的祖先目录名 → cwd 名（与 Mio 口径对齐）。"""
    env = (os.environ.get("MIO_PROJECT") or "").strip()
    if env:
        return env
    try:
        for parent in Path(__file__).resolve().parents:
            if (parent / ".git").exists():
                return parent.name
    except OSError:  # noqa: BLE001
        pass
    return Path.cwd().name


def policy_check(action: str, project: Optional[str] = None,
                 timeout: float = 3.0) -> dict:
    """历史风险评估（只读，best-effort）。

    失败/超时/MIO_HOME 缺失 → riskLevel=unknown（**fail-open**，绝不阻塞业务）。
    解析 Mio `mio policy check --json`：riskLevel + guidance.hardGate。
    """
    base = {"available": False, "action": action, "project": None,
            "riskLevel": "unknown", "hardGate": False, "total": 0,
            "failures": 0, "suggestion": None, "failureExamples": []}
    if not available() or not (action or "").strip():
        return base
    ok, data = _json_stdout(
        ["--json", "policy", "check", action.strip(),
         "--project", project or _project_name()],
        timeout=timeout,
    )
    if not ok or not isinstance(data, dict):
        return base
    try:
        guidance = data.get("guidance") or {}
        return {
            "available": True,
            "action": data.get("action") or action,
            "project": data.get("project"),
            "riskLevel": data.get("riskLevel") or "unknown",
            "hardGate": bool(guidance.get("hardGate")),
            "total": int(data.get("total") or 0),
            "failures": int(data.get("failures") or 0),
            "suggestion": data.get("suggestion"),
            "failureExamples": list(data.get("failureExamples") or [])[:3],
        }
    except Exception:  # noqa: BLE001 —— schema 变动也 fail-open
        return base


def run_mio(args: List[str], timeout: float = 300.0) -> dict:
    """白名单内调用 mio CLI。返回 {ok, code, stdout, stderr}；不可用/超时 → ok=False。

    注意文档约定：`--json` 是全局 flag（须写在子命令前）；失败时 usage 走 stderr、
    stdout 为空。此处只透传，不做语义解析。
    """
    why = _check_allowed(args)
    if why:
        return {"ok": False, "code": None, "stdout": "", "stderr": why}
    cmd = mio_cli()
    if cmd is None:
        return {"ok": False, "code": None, "stdout": "", "stderr": "mio CLI not found"}
    try:
        r = subprocess.run(cmd + list(args), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return {"ok": r.returncode == 0, "code": r.returncode,
                "stdout": r.stdout or "", "stderr": r.stderr or ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "stdout": "", "stderr": "timeout"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "code": None, "stdout": "", "stderr": str(e)}


# ── digest 定时（把近期教训写回 AGENTS.md 的 MIO_CONTEXT）──────────────────

def digest_enabled() -> bool:
    return (os.environ.get("MIO_DIGEST_DISABLED") or "").strip().lower() not in _TRUE


class MioDigestJob(Scheduler):
    """定时 `mio digest --days N --write-back`。

    环境变量：
    - MIO_DIGEST_DISABLED=1      关闭（默认开）
    - MIO_DIGEST_INTERVAL_MIN    默认 720（12h）
    - MIO_DIGEST_INITIAL_DELAY_MIN 默认 10（启动后首次延迟）
    - MIO_DIGEST_DAYS            默认 7
    运行不可用（无 CLI）或失败 → 静默记日志，不影响 taskhub。
    """

    def __init__(self) -> None:
        super().__init__(interval=float(int(os.environ.get("MIO_DIGEST_INTERVAL_MIN", "720")) * 60),
                         get_due_tasks=lambda: [], on_enqueue=lambda _x: None)
        self.initial_delay = float(int(os.environ.get("MIO_DIGEST_INITIAL_DELAY_MIN", "10")) * 60)
        self.days = int(os.environ.get("MIO_DIGEST_DAYS", "7"))
        self.last = None

    def tick(self):  # 覆盖：不建任务，直接跑 CLI
        if not digest_enabled():
            return
        if mio_cli() is None:
            logger.info("mio digest skipped: CLI not found")
            return
        res = run_mio(["digest", "--days", str(self.days), "--write-back"])
        self.last = {"ok": res["ok"], "code": res["code"]}
        if res["ok"]:
            logger.info("mio digest --write-back ok (days=%d)", self.days)
        else:
            logger.warning("mio digest failed: code=%s stderr=%s",
                           res["code"], (res["stderr"] or "")[:200])

    def _run(self):  # 覆盖：先等 initial_delay 再进入常规间隔
        if self._stop.wait(self.initial_delay):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("mio digest tick failed")
            if self._stop.wait(self.interval):
                return


# ── 契约冒烟自检（把"松耦合且失聪"补成"有哨兵"）──────────────────────────
# 探针 = 白名单只读 CLI + 关键字段断言。契约漂移的典型事故：
# policy check 改字段 → 门控永久 unknown=放行无人知晓；
# digest 改 flag → MIO_CONTEXT 悄悄停更。这里先炸（进告警），不静默。

CONTRACT_PROBES = (
    {"name": "status", "args": ("--json", "status"),
     "fields": ("version", "home", "agents", "serverScriptExists")},
    {"name": "insight_status", "args": ("--json", "insight", "status"),
     "fields": ("total", "unreported", "reported")},
    {"name": "creativity_status", "args": ("--json", "creativity", "status"),
     "fields": ("hypotheses", "combos", "active")},
    {"name": "policy_check", "args": ("--json", "policy", "check",
                                      "mio-taskhub contract smoke self-check"),
     "fields": ("riskLevel", "total", "guidance.hardGate")},
)

_contract_lock = threading.Lock()
_contract_last: dict | None = None


def contract_enabled() -> bool:
    return (os.environ.get("MIO_CONTRACT_DISABLED") or "").strip().lower() not in _TRUE


def contract_interval_s() -> float:
    return float(int(os.environ.get("MIO_CONTRACT_INTERVAL_MIN", "60")) * 60)


def _has_path(obj, path: str) -> bool:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def contract_check(timeout: float = 120.0) -> dict:
    """跑全部契约探针并缓存结果。

    返回 {ok, available, checked_at, epoch, interval_s, probes:[{name, ok, missing, error}]}。
    runtime 缺失/CLI 缺失 → available:false（按设计属正常态，不产生告警）。
    """
    global _contract_last
    interval = contract_interval_s()
    now_iso = datetime.now(timezone.utc).isoformat()
    probes_out: List[dict] = []

    if not available() or mio_cli() is None:
        result = {"ok": False, "available": False,
                  "reason": "runtime unavailable" if not available() else "mio CLI not found",
                  "checked_at": now_iso, "epoch": time.time(),
                  "interval_s": interval, "probes": []}
    else:
        for p in CONTRACT_PROBES:
            args = list(p["args"])
            if p["name"] == "policy_check":
                args += ["--project", _project_name()]
            ok, data = _json_stdout(args, timeout=timeout)
            missing = [f for f in p["fields"]
                       if not (isinstance(data, dict) and _has_path(data, f))]
            entry = {"name": p["name"], "ok": ok and not missing, "missing": missing}
            if not ok:
                entry["error"] = "cli failed or stdout not JSON"
            probes_out.append(entry)
        result = {"ok": all(e["ok"] for e in probes_out), "available": True,
                  "checked_at": now_iso, "epoch": time.time(),
                  "interval_s": interval, "probes": probes_out}

    with _contract_lock:
        _contract_last = result
    return result


def contract_last() -> dict | None:
    """最近一次契约结果（缓存；None=尚未跑过）。"""
    with _contract_lock:
        return dict(_contract_last) if _contract_last else None


class ContractJob(Scheduler):
    """定时契约冒烟（结果只写缓存；告警由 AlertManager._check_contract 读取）。

    环境变量：
    - MIO_CONTRACT_DISABLED=1        关闭（默认开）
    - MIO_CONTRACT_INTERVAL_MIN      默认 60（1h）
    - MIO_CONTRACT_INITIAL_DELAY_MIN 默认 5（启动后首次延迟）
    """

    def __init__(self) -> None:
        super().__init__(interval=contract_interval_s(),
                         get_due_tasks=lambda: [], on_enqueue=lambda _x: None)
        self.initial_delay = float(int(os.environ.get("MIO_CONTRACT_INITIAL_DELAY_MIN", "5")) * 60)
        self.last = None

    def tick(self):  # 覆盖：不建任务，直接跑探针
        if not contract_enabled():
            return
        if mio_cli() is None:
            logger.info("mio contract skipped: CLI not found")
            return
        res = contract_check()
        self.last = {"ok": res["ok"], "epoch": res.get("epoch")}
        if res["ok"]:
            logger.info("mio contract smoke ok (probes=%d)", len(res["probes"]))
        else:
            failed = [p["name"] + (f"(missing {','.join(p['missing'])})" if p.get("missing") else "")
                      for p in res.get("probes", []) if not p.get("ok")]
            logger.warning("mio contract smoke FAILED: %s",
                           "; ".join(failed) or res.get("reason"))

    def _run(self):  # 覆盖：先等 initial_delay 再进入常规间隔
        if self._stop.wait(self.initial_delay):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                logger.exception("mio contract tick failed")
            if self._stop.wait(self.interval):
                return
