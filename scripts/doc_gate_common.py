# -*- coding: utf-8 -*-
"""文档 Consult 门控共用工具（纯标准库，无第三方依赖）。

用于 pre-push 钩子：解析任务 id、查询 taskhub、判定文档是否 approved、
抓取需求文档正文、从 diff 抽取 FR-n。

设计原则（见 doc-consult-enforcement-plan.md §7）：
- taskhub 不可达 / 解析异常 / 无任务关联 → 一律「放行并告警」，避免卡死开发。
- 仅在确凿的「文档未批准」「FR 不存在」时才阻塞推送。
"""
import os
import re
import sys
import json
import subprocess
import urllib.request
import urllib.error

# Windows GBK 控制台：结论信息含 ✅ 等非 GBK 字符，print/write 会 UnicodeEncodeError
# 直接炸掉钩子（2026-09-27 实测）。统一按 UTF-8 输出，坏字符替换不中断。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — reconfigure 不可用时维持原样
        pass

HUB_BASE = os.environ.get("MIO_TASKHUB_BASE", "http://127.0.0.1:48620/api/v1")
TIMEOUT = float(os.environ.get("MIO_TASKHUB_TIMEOUT", "3"))


def warn_allow(msg):
    """放行并告警，返回 (allow=True, msg)。"""
    sys.stderr.write("[doc-gate] WARN  " + msg + "（已放行，不阻塞推送）\n")
    return (True, msg)


def block(msg):
    """阻塞推送，返回 (allow=False, msg)。"""
    sys.stderr.write("[doc-gate] BLOCK " + msg + "\n")
    return (False, msg)


def run_git(*args):
    # Windows 下 text=True 默认按 locale（GBK）解码，git 的 UTF-8 中文输出会抛
    # UnicodeDecodeError（reader 线程炸掉 → p.stdout=None → 钩子崩溃误拦 push，
    # 2026-09-27 实测）。显式按 UTF-8 解码，坏字节替换不中断。
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


# taskhub 的真实任务 id 是 8 位十六进制（如 b3f970b4），旧正则只认数字会把
# `task-2f5db835` 截成 `2`（查错任务）、`task-b3f970b4` 直接匹配不到（跳过门控）。
# 这里优先匹配十六进制 id（>=6 位），并向后兼容纯数字（`task-123`）。
_ID = r"([0-9a-fA-F]{6,32}|\d+)"
_RE_BRANCH = re.compile(r"\btask[-_/]?" + _ID, re.IGNORECASE)
_RE_TEXT = re.compile(r"\btask[:#]?\s*" + _ID, re.IGNORECASE)


def parse_task_id_from_branch(branch):
    m = _RE_BRANCH.search(branch or "")
    return m.group(1) if m else None


def parse_task_id_from_text(text):
    m = _RE_TEXT.search(text or "")
    return m.group(1) if m else None


def resolve_task_id(branch=None):
    if branch:
        tid = parse_task_id_from_branch(branch)
        if tid:
            return tid
    # 退而求其次：从最近提交信息解析
    msg = run_git("log", "-1", "--pretty=%B").stdout
    return parse_task_id_from_text(msg)


def fetch_json(path):
    url = HUB_BASE + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_task(task_id):
    return fetch_json("/tasks/" + str(task_id))


def get_doc_content(task_id, kind):
    data = fetch_json("/tasks/" + str(task_id) + "/doc?kind=" + kind)
    return (data.get("content") or "", data.get("status"))


def extract_frs_from_text(text):
    return {"FR-" + m.group(1) for m in re.finditer(r"FR-(\d+)", text)}


def _commit_ts(sha):
    out = run_git("show", "-s", "--format=%ct", sha).stdout.strip()
    try:
        return int(out)
    except ValueError:
        return -1


def _stacked_task_base(local_sha, master_base):
    """叠分支基准修正：本任务建立在其他 task-* 分支之上且上游未并入主线时，
    首推 remote_sha 全 0，以 merge-base(main/master) 为基准会把上游分期提交
    的 FR-n 一并计入本任务 diff → 误报（AGENTS.md 语义是 diff 引用的 FR 真实
    存在于已批准需求文档，各分期需求本就是独立已批准文档）。
    取所有上游 task-* 分支切点中晚于主干基准的最新一个作为基准，门控只检查
    本分支自己的改动；无叠分支（切点不晚于主干基准）时维持原行为。"""
    if not master_base:
        return None
    # 归一为 40 位 sha，避免 'HEAD' 等符号引用导致 mb == local_sha 比较失效
    local = run_git("rev-parse", "--verify", local_sha).stdout.strip() or local_sha
    refs = run_git("for-each-ref", "--format=%(refname)",
                   "refs/heads/task-*", "refs/remotes/origin/task-*")
    best, best_ts = None, _commit_ts(master_base)
    for ref in refs.stdout.splitlines():
        ref = ref.strip()
        if not ref:
            continue
        mb = run_git("merge-base", local, ref).stdout.strip()
        # mb == local：该 ref 即待推分支自身（或其上无新提交），跳过
        if not mb or mb == local:
            continue
        ts = _commit_ts(mb)
        if ts > best_ts:
            best, best_ts = mb, ts
    return best


def collect_diff_frs(local_sha, remote_sha):
    """从待推送提交的新增行里抽取 FR-n；返回 set，取不到 diff 时返回 None。"""
    if re.fullmatch(r"0+", remote_sha or ""):
        base = run_git("merge-base", local_sha, "main").stdout.strip() \
            or run_git("merge-base", local_sha, "master").stdout.strip()
        stacked = _stacked_task_base(local_sha, base)
        if stacked:
            base = stacked
        if not base or base == local_sha:
            return set()
        range_spec = [base, local_sha]
    else:
        range_spec = [remote_sha, local_sha]
    p = run_git("diff", "-U0", "--no-color", *range_spec)
    if p.returncode != 0:
        return None
    frs = set()
    for line in p.stdout.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            frs |= extract_frs_from_text(line)
    return frs
