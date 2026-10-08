# -*- coding: utf-8 -*-
"""Mio Agent Runtime 低耦合适配（只读文件 + CLI 白名单 + digest 定时）。

设计原则（低耦合）：
- **不 import** 运行时包、**不依赖 Node**；仅通过两处边界交互：
  1. 只读 `MIO_HOME` 下的 JSONL/配置（容错解析，schema 变动不炸）；
  2. 可选的 CLI 子进程（白名单 + 超时 + 失败静默降级）。
- 运行时缺失/换机/未安装 → 一切接口返回 `available: false`，**不影响 taskhub**。
- 绝不外泄敏感字段：`config.json` 的 `llm`（含明文 apiKey）一律剔除。
- **契约冒烟**（ContractJob）：定时跑 4 条白名单只读 CLI + 1 条本地 MCP 入口探针
  并断言关键输出字段——runtime 升级改了 schema / MCP 脚本搬了家 → 告警可见，
  而不是 policy 门控静默失效 / digest 悄悄停更 / 模板生成 500。
- **路径可移植**：MCP 脚本与 node 不写死绝对路径，走 env（MIO_MCP_SCRIPT /
  MIO_NODE）→ mio CLI 前缀 / npm root -g 推导 → 旧硬编码兜底。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

# Windows：隐藏子进程控制台窗口（.cmd/.bat 经 cmd.exe 宿主会开可见窗口）
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
# 需两级判定的只读子命令（insight generate、creativity generate 会烧 LLM 不放行；
# creativity ferment 烧 LLM 但仅由「跑一次发酵」按钮显式触发，2026-09-27 起放行）
CLI_SUBCOMMANDS = {
    ("creativity", "status"), ("creativity", "list"), ("creativity", "ferment"),
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


def llm_config() -> dict:
    """原始 LLM 配置（**含明文 apiKey**）——仅供服务端进程内调用，禁止外泄/落日志。

    P5（FR-34）：优先级 env `MIO_LLM_URL`/`MIO_LLM_KEY`/`MIO_LLM_MODEL`
    > config.json 的 `llm` 段（由 `mio config llm` 写入）。
    返回 `{apiUrl, apiKey, model}`，缺项为空串。
    """
    cfg = _read_json(home() / "config.json")
    llm = cfg.get("llm")
    if not isinstance(llm, dict):
        llm = {}
    return {
        "apiUrl": str(os.environ.get("MIO_LLM_URL") or llm.get("apiUrl") or "").strip(),
        "apiKey": str(os.environ.get("MIO_LLM_KEY") or llm.get("apiKey") or "").strip(),
        "model": str(os.environ.get("MIO_LLM_MODEL") or llm.get("model") or "").strip(),
    }


def llm_enabled() -> bool:
    """LLM 是否可用（具备 apiUrl + apiKey）。"""
    c = llm_config()
    return bool(c["apiUrl"] and c["apiKey"])


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


def _read_pid_file(p: Path) -> int:
    try:
        return int((p.read_text(encoding="utf-8", errors="replace") or "0").strip() or 0)
    except Exception:  # noqa: BLE001
        return 0


def observer_state() -> dict:
    """观察器状态：稳定读 observe.pid 并校验进程存活。"""
    pid = _read_pid_file(home() / "observe.pid")
    if pid and _pid_alive(pid):
        return {"running": True, "pid": pid}
    return {"running": False, "pid": pid or None}


_FALSE_ENV = ("0", "false", "no", "off")


def _today_observations_path() -> Path:
    """今日观测数据文件（research 活动启发式用；测试可 monkeypatch）。"""
    return (Path.cwd() / ".local" / "observer" / "observations"
            / (datetime.now().strftime("%Y-%m-%d") + ".json"))


def research_state() -> dict:
    """研究调度器（`mio observer serve`）状态。

    检测链：上游 research.pid（守护化路径才写）→ hub 托管 research_hub.json
    （observer_start 写入）→ 近 30min 观测数据活动（外部前台启动的兜底——
    serve 前台模式不写 pid 文件，历史上曾因此被误报 not running）。
    """
    pid = _read_pid_file(home() / "research.pid")
    if pid and _pid_alive(pid):
        return {"running": True, "pid": pid, "source": "pidfile"}
    hub = _read_json(home() / "research_hub.json")
    hub_pid = hub.get("pid")
    if isinstance(hub_pid, int) and hub_pid > 0 and _pid_alive(hub_pid):
        return {"running": True, "pid": hub_pid, "source": "hub",
                "started_at": hub.get("started_at")}
    try:
        f = _today_observations_path()
        if f.is_file() and (time.time() - f.stat().st_mtime) < 1800:
            return {"running": True, "pid": None, "source": "recent-activity"}
    except Exception:  # noqa: BLE001
        pass
    return {"running": False,
            "pid": pid or (hub_pid if isinstance(hub_pid, int) else None),
            "source": None}


def observer_base_dir() -> str:
    """观测台素材库唯一入口：serve 日更调度与生成端取材必须同库。

    历史事故：serve 随启动方 CWD 漂移到 dist/mio-taskhub（幽灵目录，无 dag/），
    每分钟 ENOENT 重试、insights 停更，日度想法生成因素材重复 409。
    env MIO_OBSERVER_BASE_DIR 可覆盖；默认锚定 agent-dev 仓库 .local/observer。
    """
    return os.environ.get("MIO_OBSERVER_BASE_DIR") or r"E:\work\code\agent-dev\.local\observer"


def observer_start() -> dict:
    """拉起 Mio 观测守护（幂等，已运行则跳过）。

    - 观察器：`mio observe --start`（上游自身守护化并写 observe.pid）；
    - 研究调度器：`mio observer serve`（**前台进程**，不写 pid 文件）→ hub 以
      分离子进程托管（CREATE_NO_WINDOW + 日志重定向 MIO_HOME/research_serve.log）
      并记录 research_hub.json 供 research_state 探测。
    """
    out = {"observer": observer_state(), "research": research_state(), "started": []}
    if not out["observer"]["running"]:
        r = run_mio(["observe", "--start"], timeout=30.0)
        out["started"].append("observer")
        out["observer_start_ok"] = bool(r.get("ok"))
        if not r.get("ok"):
            out["observer_error"] = (r.get("stderr") or "")[:200]
    if not out["research"]["running"]:
        cmd = mio_cli()
        if not cmd:
            out["research_error"] = "mio CLI not found"
        else:
            try:
                log_path = home() / "research_serve.log"
                fh = open(log_path, "ab")
                try:
                    base = observer_base_dir()
                    Path(base).mkdir(parents=True, exist_ok=True)
                    proc = subprocess.Popen(
                        list(cmd) + ["observer", "serve", "--base-dir", base],
                        stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                        creationflags=_CREATE_NO_WINDOW, cwd=str(Path.cwd()))
                finally:
                    fh.close()
                (home() / "research_hub.json").write_text(
                    json.dumps({"pid": proc.pid,
                                "started_at": datetime.now().isoformat(timespec="seconds")}),
                    encoding="utf-8")
                out["started"].append("research")
                time.sleep(1.5)
            except Exception as e:  # noqa: BLE001
                out["research_error"] = str(e)
    out["observer"] = observer_state()
    out["research"] = research_state()
    return out


def observer_ensure() -> dict:
    """自启兜底（hub 启动时调用）：已运行则不动；异常只记日志（fail-open）。"""
    try:
        st = observer_start()
        logger.info("observer ensure: started=%s observer_running=%s research_running=%s",
                    st.get("started"),
                    (st.get("observer") or {}).get("running"),
                    (st.get("research") or {}).get("running"))
        return st
    except Exception as e:  # noqa: BLE001
        logger.warning("observer ensure failed (ignored): %s", e)
        return {"ok": False, "error": str(e)}


def autostart_enabled() -> bool:
    """观测守护随 hub 自启开关：默认开；env MIO_OBSERVER_AUTOSTART=0/false/no/off 关闭。"""
    raw = os.environ.get("MIO_OBSERVER_AUTOSTART")
    return raw is None or raw.strip().lower() not in _FALSE_ENV


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
        "research": research_state(),
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

_RUNTIME_JS_REL = ("node_modules", "mio-agent-runtime", "bin", "mio.js")


def _node_direct_from_shim(shim: str) -> Optional[List[str]]:
    """mio.cmd/mio.bat shim → node 直调包内 bin/mio.js。

    背景（2026-10-08 实证）：cmd shim 末行 `%*` 无引号转发会吞中文参数、
    `|` 直接致命——生产 job 的 observer 洞察 source 因此从未进入配对器，
    creativity 退化成「模板自配模板」。shim 同目录能找到 runtime 包且有
    node 时，一律转为 node 直调；否则返回 None 由调用方回退 shim。
    """
    js = Path(shim).parent.joinpath(*_RUNTIME_JS_REL)
    if not js.is_file():
        return None
    node = resolve_node()
    if not node:
        return None
    return [node, str(js)]


def mio_cli() -> Optional[List[str]]:
    """解析 mio CLI 命令。优先 env MIO_CLI（可为 .cmd 或 .js 路径）。

    .cmd/.bat shim 一律尝试转 node 直调（绕过 cmd.exe 的 %* 吞参缺陷，
    见 `_node_direct_from_shim`）；转换条件不满足时回退原 shim 并留日志。
    """
    override = (os.environ.get("MIO_CLI") or "").strip()
    if override:
        low = override.lower()
        if low.endswith(".js"):
            return ["node", override]
        if low.endswith((".cmd", ".bat")):
            direct = _node_direct_from_shim(override)
            if direct:
                return direct
        return [override]
    for name in ("mio.cmd", "mio.bat", "mio"):
        p = shutil.which(name)
        if p:
            if p.lower().endswith((".cmd", ".bat")):
                direct = _node_direct_from_shim(p)
                if direct:
                    return direct
                logger.warning("mio CLI keeps cmd shim %s: node/mio.js not resolvable", p)
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


def creativity(limit: int = 20, timeout: float = 300.0,
               with_status: bool = True) -> dict:
    """假设库（只读 status 概览 + 假设列表：novelty/feasibility/impact/score）。

    P1（FR-11/FR-12）：`ok=False` 表示 CLI 调用失败（调用方可转 503）；
    `with_status=False` 只打 list（导入等只要列表的调用省一次 CLI）。
    """
    if not available():
        return {"available": False, "ok": False, "reason": "runtime_unavailable",
                "status": {}, "items": []}
    status: dict = {}
    if with_status:
        _, status = _json_stdout(["--json", "creativity", "status"], timeout=timeout)
    ok, items = _json_stdout(["--json", "creativity", "list",
                              "--limit", str(max(1, min(int(limit), 100)))],
                             timeout=timeout)
    out = {"available": True, "ok": bool(ok), "status": status or {},
           "items": items if isinstance(items, list) else []}
    if not ok:
        out["reason"] = "cli_failed"
    return out


def insight(limit: int = 20) -> dict:
    """洞察（只读）：status 计数 + 洞察列表。生成走 MCP（mio.insight.generate）。"""
    if not available():
        return {"available": False, "status": {}, "items": []}
    _, status = _json_stdout(["--json", "insight", "status"])
    _, items = _json_stdout(["--json", "insight", "list",
                             "--limit", str(max(1, min(int(limit), 100)))])
    return {"available": True, "status": status or {},
            "items": items if isinstance(items, list) else []}


def ferment(limit: int = 5) -> dict:
    """跑一次 Mio 创意发酵（**会调 LLM**，显式按钮触发）：
    复审 active 假设 → 重打 N/F/I 分 → promote/keep/reject。
    调用方（前端）在拿到结果后应重新 GET /mio/ferment 刷新映射。
    """
    if not available():
        return {"available": False, "fermented": 0, "results": []}
    ok, data = _json_stdout(["--json", "creativity", "ferment",
                             "--limit", str(max(1, min(int(limit), 20)))],
                            timeout=120.0)
    if not ok or not isinstance(data, dict):
        return {"available": True, "fermented": 0, "results": [],
                "error": "ferment failed (CLI error or non-JSON output)"}
    return {"available": True, **data}


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
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=_CREATE_NO_WINDOW)
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


# ── MCP 脚本 / Node 可移植解析（替代写死绝对路径）────────────────────────
# 旧代码在 idea_templates 里写死 D:\node_global\... 与 workbuddy node.exe——换机/
# npm 前缀变化即断，是耦合评估里的最高风险点。解析顺序：env 显式覆盖 → 从 mio
# CLI 前缀 / npm root -g 推导 → 旧硬编码兜底。env 覆盖不静默回退（缺失显式暴露）。

_MCP_SCRIPT_SUFFIX = ("node_modules", "mio-agent-runtime",
                      "server", "mio-intelligence-mcp", "index.js")
_MCP_SCRIPT_LEGACY = (
    r"D:\node_global\node_modules\mio-agent-runtime\server"
    r"\mio-intelligence-mcp\index.js")
_NODE_LEGACY = r"C:\Users\admin\.workbuddy\binaries\node\versions\22.22.2\node.exe"


def _prefix_from_cli(cli: List[str]) -> Optional[Path]:
    """mio shim（prefix/mio.cmd）或包内 js（.../node_modules/<pkg>/...）→ npm prefix。"""
    first = (cli[1] if len(cli) >= 2
             and cli[0].lower().endswith(("node", "node.exe")) else cli[0])
    p = Path(first)
    if p.name.lower().startswith("mio"):
        return p.parent
    parts = p.parts
    if "node_modules" in parts:
        return Path(*parts[: parts.index("node_modules")])
    return None


def _prefix_candidates() -> List[Path]:
    out: List[Path] = []
    cli = mio_cli()
    if cli:
        pref = _prefix_from_cli(cli)
        if pref:
            out.append(pref)
    if out:
        return out                      # 已从 mio CLI 推出前缀，不打扰 npm
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm:
        try:
            root = subprocess.run([npm, "root", "-g"], capture_output=True,
                                  text=True, timeout=10,
                                  creationflags=_CREATE_NO_WINDOW)
            if root.returncode == 0:
                rp = Path((root.stdout or "").strip())
                if rp.is_dir():
                    out.append(rp)
        except Exception:  # noqa: BLE001 —— npm 不可用就走兜底
            pass
    return out


def resolve_mcp_script() -> Optional[str]:
    """mio-intelligence MCP 入口脚本（可移植）。

    顺序：env `MIO_MCP_SCRIPT`（显式覆盖，缺失不静默回退，交给存在性检查暴露）
    → mio CLI 前缀 / npm root -g 推导的 node_modules → 旧硬编码兜底。
    """
    override = (os.environ.get("MIO_MCP_SCRIPT") or "").strip()
    if override:
        return override
    for prefix in _prefix_candidates():
        cand = prefix.joinpath(*_MCP_SCRIPT_SUFFIX)
        if cand.is_file():
            return str(cand)
    return _MCP_SCRIPT_LEGACY if Path(_MCP_SCRIPT_LEGACY).is_file() else None


def resolve_node() -> Optional[str]:
    """node 可执行（可移植）：env `MIO_NODE` → PATH → 便携硬编码兜底。"""
    override = (os.environ.get("MIO_NODE") or "").strip()
    if override:
        return override
    p = shutil.which("node") or shutil.which("node.exe")
    if p:
        return p
    return _NODE_LEGACY if Path(_NODE_LEGACY).is_file() else None


# ── 契约冒烟自检（把"松耦合且失聪"补成"有哨兵"）──────────────────────────
# 探针 = 白名单只读 CLI + 关键字段断言 + 本地 MCP 入口解析。契约漂移的典型事故：
# policy check 改字段 → 门控永久 unknown=放行无人知晓；
# digest 改 flag → MIO_CONTEXT 悄悄停更；MCP 脚本搬家 → 模板生成 500。
# 这里先炸（进告警），不静默。

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
    {"name": "mcp_script", "args": (), "fields": ("path", "node")},
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
            if p["name"] == "mcp_script":
                # 本地探针（不跑 CLI）：MCP 脚本与 node 必须都解析到且存在
                script = resolve_mcp_script()
                node = resolve_node()
                missing = []
                if not (script and Path(script).is_file()):
                    missing.append("script")
                if not (node and Path(node).is_file()):
                    missing.append("node")
                entry = {"name": "mcp_script", "ok": not missing,
                         "missing": missing}
                if script:
                    entry["path"] = script
                probes_out.append(entry)
                continue
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
