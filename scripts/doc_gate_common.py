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
    return subprocess.run(["git", *args], capture_output=True, text=True)


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


def collect_diff_frs(local_sha, remote_sha):
    """从待推送提交的新增行里抽取 FR-n；返回 set，取不到 diff 时返回 None。"""
    if re.fullmatch(r"0+", remote_sha or ""):
        base = run_git("merge-base", local_sha, "main").stdout.strip() \
            or run_git("merge-base", local_sha, "master").stdout.strip()
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
