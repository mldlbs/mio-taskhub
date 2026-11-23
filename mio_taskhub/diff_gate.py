"""窄门禁：拦截「声明成功但零改动」的提交。

背景（需求 `docs/taskhub/d468e633/requirement.md` FR-1 ~ FR-11）：
mio-taskhub 的 `submit_result` 成功路径只校验「Run 所有权 / 状态可提交 /
ReadEvidence 版本一致」，随后**直接采信 Agent 自报的 `success: bool`**。
读证指纹只覆盖被读文档，不覆盖代码 —— 于是「完成」只代表 Agent 自己说完成了。

本模块提供两件事，刻意分开：
  1. **事实判定**（`snapshot` / `differs`）：本次 run 期间工作区有没有改动。
  2. **策略判定**（`check_diff_gate`）：该不该拦（豁免规则 + 环境开关）。

设计原则（决策 `product-decision-20261020.md`）：
  - **只在「明确的假完成」上拦截**：测试未跑、diff 质量等一律降级为标注，只提示不拦。
  - **fail-open 优先**：采集失败、非 git 仓库、无 workspace 一律跳过并标注，
    绝不因采集问题拦人。判据采不到信息时一律视为「有改动」。
  - **拒绝不消耗 attempt**：同一 run 内改完后可重试；连续拒绝达上限才判本次执行失败。
  - **默认 `MIO_DIFF_GATE=warn`**（只标注不拦），灰度确认零误伤后再切 block。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

# git 子进程超时（秒）。超时按 fail-open 跳过判定。
GIT_TIMEOUT_SEC = 5
# 变更文件路径列表上限（超出截断，不影响判定）。
MAX_PATHS = 50
# 单次 run 内允许被拒次数上限。达到即判本次执行失败，交既有重试机制。
MAX_REJECTIONS = 2
# 指纹采集的变更文件数上限。超过则放弃指纹（标 fingerprint_ok=False），
# 判定退化为 fail-open（视为有改动，不拦）——宁可漏放，不能误伤。
MAX_FINGERPRINT_FILES = 5000
# 单文件参与指纹计算的大小上限（字节）。超过则该文件按「过大」标记参与，
# 不读全文（避免大文件/二进制把采集拖垮）。
MAX_FINGERPRINT_FILE_BYTES = 8 * 1024 * 1024

# 门禁开关三档。
GATE_MODES = ("off", "warn", "block")
# 默认 warn：只标注不拦，灰度零误伤后再切 block。
DEFAULT_GATE_MODE = "warn"

# 豁免的 task_kind（评审类 / 变更跟踪类本就不一定改代码）。
EXEMPT_TASK_KINDS = ("idea_review", "change_tracking")

# 活跃 run 状态（用于并发判定）。
ACTIVE_RUN_STATES = ("claimed", "running")


# ── 数据结构 ──────────────────────────────────────────────────────────

@dataclass
class Snapshot:
    """一次采集的原始结果。ok=False 表示采集失败（fail-open 方向）。"""

    ok: bool = True
    error: str = ""
    head: str = ""
    porcelain: str = ""
    files: int = 0
    insertions: int = 0
    deletions: int = 0
    paths: list[str] = field(default_factory=list)
    captured_at: str = ""
    # 工作区变更文件的内容指纹（sha256 聚合）。fingerprint_ok=False 表示
    # 指纹不可用（文件过多/读失败）→ 判定时按 fail-open 视为「有改动」。
    fingerprint: str = ""
    fingerprint_ok: bool = True

    def brief(self) -> dict[str, Any]:
        """摘要形态（入库用）。不返回 error 的绝对路径等敏感信息。"""
        return {
            "head": self.head,
            "porcelain": self.porcelain,
            "files": self.files,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "paths": self.paths[:MAX_PATHS],
            "fingerprint": self.fingerprint,
            "fingerprint_ok": self.fingerprint_ok,
        }


@dataclass
class Verdict:
    """判定结论。"""

    blocked: bool = False
    skipped: bool = False
    reason: str = ""
    message: str = ""
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    mode: str = DEFAULT_GATE_MODE

    def brief(self) -> dict[str, Any]:
        return {
            "blocked": self.blocked,
            "skipped": self.skipped,
            "reason": self.reason,
            "files_changed": self.files_changed,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "mode": self.mode,
        }


# ── 采集层 ────────────────────────────────────────────────────────────

def _git(workspace: str, *args: str) -> tuple[int, str, str]:
    """只读执行 git。返回 (returncode, stdout, stderr)。超时不吞异常。"""
    proc = subprocess.run(
        ["git", *args],
        cwd=workspace,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_TIMEOUT_SEC,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _numstat(workspace: str) -> tuple[int, int, list[str]]:
    """解析 `git diff --numstat HEAD` → (新增行合计, 删除行合计, 路径列表)。

    空仓库（无 HEAD）时该命令失败 → 降级为 (0, 0, [])。
    降级不影响判定：`porcelain` 已能判定「有没有改动」。
    """
    try:
        rc, out, _ = _git(workspace, "diff", "--numstat", "HEAD")
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 0, 0, []
    if rc != 0:
        return 0, 0, []
    ins = dele = 0
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        a, d, p = parts[0], parts[1], parts[2]
        # 二进制文件记为 "-" → 不计入行数，但文件数仍算
        if a.isdigit():
            ins += int(a)
        if d.isdigit():
            dele += int(d)
        paths.append(p)
    return ins, dele, paths


def _file_digest(path: str) -> str:
    """单文件内容 sha256。分块读，不把大文件整个塞进内存。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
    except OSError:
        return "UNREADABLE"
    return h.hexdigest()


def _tree_digest(path: str) -> str:
    """目录内容摘要（未跟踪目录用）。路径相对 + 内容哈希，排序后聚合。"""
    h = hashlib.sha256()
    try:
        for root, dirs, files in os.walk(path):
            dirs.sort()
            for name in sorted(files):
                fp = os.path.join(root, name)
                h.update(os.path.relpath(fp, path).encode("utf-8", "replace"))
                h.update(b"\0")
                h.update(_file_digest(fp).encode("ascii"))
                h.update(b"\0")
    except OSError:
        return "UNREADABLE"
    return h.hexdigest()


def _path_digest(workspace: str, rel: str) -> str:
    """路径内容摘要。目录递归，文件单文件；读不到则返回标记串。"""
    full = os.path.join(workspace, rel)
    try:
        if os.path.isdir(full):
            return _tree_digest(full)
        if os.path.getsize(full) > MAX_FINGERPRINT_FILE_BYTES:
            return "TOO_LARGE"
        return _file_digest(full)
    except OSError:
        return "UNREADABLE"


def _fingerprint(workspace: str, status_out: str) -> tuple[str, bool]:
    """工作区变更文件的内容指纹。

    返回 (fingerprint, ok)：
      · ok=True  → fingerprint 是有效的内容指纹（空串表示工作区干净）
      · ok=False → 指纹不可用（变更文件过多 / 状态行无法解析），调用方须 fail-open

    **为什么必须有指纹**：只比 `git status --porcelain` 只能看出「哪些文件变了」，
    比 `diff --numstat` 的行数只能看出「变了多少行」。两者都分辨不出
    「把一行 A 改成一行 B」——行数相同、文件状态相同，但内容确实变了。
    更严重的是：若只看 after 的绝对行数，工作区在 run 开始前就是脏的
    （本次零改动）时也会被判成「有改动」→ 门禁彻底失效（漏放假完成）。

    指纹只对**变更文件**计算（已跟踪改动 + 未跟踪），成本与变更规模成正比，
    与仓库总大小无关。
    """
    entries: list[str] = []
    try:
        for line in status_out.splitlines():
            if not line.strip():
                continue
            if len(line) < 4:
                return "", False          # 状态行格式异常 → 指纹不可用
            xy = line[:2]
            rel = line[3:].strip()
            if " -> " in rel:            # 重命名：取新路径
                rel = rel.split(" -> ", 1)[1].strip()
            if xy.startswith("??"):
                entries.append(f"{xy}:{rel}:{_path_digest(workspace, rel)}")
                continue
            if "D" in xy and "M" not in xy:
                # 已删除：内容不存在，记删除标记（参与指纹但不给内容）
                entries.append(f"{xy}:{rel}:DELETED")
                continue
            entries.append(f"{xy}:{rel}:{_path_digest(workspace, rel)}")
    except Exception:
        return "", False

    if len(entries) > MAX_FINGERPRINT_FILES:
        return "", False

    entries.sort()
    h = hashlib.sha256()
    for e in entries:
        h.update(e.encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest(), True


def snapshot(workspace: str) -> Snapshot:
    """采集一次快照。任何异常都转成 ok=False + error 原因，绝不向上抛。

    只用只读子命令：`rev-parse HEAD` / `status --porcelain` / `diff --numstat HEAD`。
    """
    from mio_taskhub.utils import _now

    if not workspace or not os.path.isdir(workspace):
        return Snapshot(ok=False, error="workspace 不存在或为空")
    try:
        rc_head, head_out, _ = _git(workspace, "rev-parse", "HEAD")
        rc_st, status_out, _ = _git(workspace, "status", "--porcelain")
    except subprocess.TimeoutExpired:
        return Snapshot(ok=False, error="git 调用超时")
    except FileNotFoundError:
        return Snapshot(ok=False, error="git 不可用")
    except OSError as e:
        return Snapshot(ok=False, error=f"git 调用失败: {e.__class__.__name__}")

    # rev-parse 在非 git 目录返回非零 → 采集失败（fail-open 跳过）
    if rc_st != 0:
        return Snapshot(ok=False, error="非 git 仓库")
    # 空仓库（尚无 commit）时 rev-parse 也非零 —— 这不算采集失败，
    # head 记空串即可（porcelain 仍能判定有没有改动）。
    head = head_out.strip() if rc_head == 0 else ""

    ins, dele, paths = _numstat(workspace)

    # porcelain 每行一个变更条目（含未跟踪 `??`）
    status_lines = [ln for ln in status_out.splitlines() if ln.strip()]

    fingerprint, fp_ok = _fingerprint(workspace, status_out)

    return Snapshot(
        ok=True,
        error="",
        head=head,
        porcelain=status_out,
        files=len(status_lines),
        insertions=ins,
        deletions=dele,
        paths=paths[:MAX_PATHS],
        captured_at=_now().isoformat(),
        fingerprint=fingerprint,
        fingerprint_ok=fp_ok,
    )


# ── 判定层 ────────────────────────────────────────────────────────────

def differs(before: Snapshot, after: Snapshot) -> bool:
    """前后快照是否有变化。

    三条判据（任一命中即算「本次有改动」）：
      1. HEAD 变了 —— agent 提交了 commit
      2. porcelain 变了 —— 变更文件集合/状态变了（含新增未跟踪文件）
      3. 内容指纹变了 —— 同一文件的内容变了（行数相同、状态相同也能分辨）

    **采不到信息时返回 True**（视为有改动）—— 这是故意的：
    宁可漏放假完成，也不能把「采集失败」变成「拦截成功」。

    ⚠️ 判据 3 不能写成「after 的增删行数 > 0」：那样只要工作区在 run 开始前
    就是脏的（本次零改动），after 行数仍然 > 0 → 判定「有改动」→ 门禁彻底失效
    （漏放假完成）。必须比较**前后**的内容指纹。
    """
    if not (before.ok and after.ok):
        return True
    if before.head != after.head:
        return True          # 有新 commit
    if before.porcelain != after.porcelain:
        return True          # 工作区改动有变化（含新增未跟踪文件）
    if not (before.fingerprint_ok and after.fingerprint_ok):
        return True          # 指纹不可用 → fail-open，不拦
    return before.fingerprint != after.fingerprint


# ── 策略层 ────────────────────────────────────────────────────────────

def gate_mode() -> str:
    """当前生效的门禁档位。off/warn/block，其他值按 warn 处理。"""
    raw = (os.environ.get("MIO_DIFF_GATE") or "").strip().lower()
    return raw if raw in GATE_MODES else DEFAULT_GATE_MODE


def _has_concurrent_run(db, task) -> bool:
    """同一 workspace 下是否存在**其他**活跃 run。

    两个 run 的前后快照会互相污染，判定不可信 → 跳过判定。

    Run 表没有 workspace 列，需经 Task 表 join：先按 workspace 找出同工作区的
    任务，再查这些任务下是否有除本 run 外的活跃 run。
    """
    from sqlmodel import select

    from mio_taskhub.models import Run, RunState, Task

    if not task.workspace:
        return False

    # 同一工作区下的其他任务
    sibling_tasks = db.exec(
        select(Task.id).where(
            Task.workspace == task.workspace,
            Task.id != task.id,
        )
    ).all()
    if not sibling_tasks:
        return False
    sib_ids = [row if isinstance(row, str) else row[0] for row in sibling_tasks]

    states = [RunState.CLAIMED, RunState.RUNNING]
    # 兄弟任务下有活跃 run → 并发冲突
    for sib_id in sib_ids:
        n = db.exec(
            select(Run).where(
                Run.task_id == sib_id,
                Run.state.in_(states),
            )
        ).all()
        if n:
            return True
    # 本任务下有除本 run 外的其他活跃 run → 并发冲突
    same_task = db.exec(
        select(Run).where(
            Run.task_id == task.id,
            Run.state.in_(states),
        )
    ).all()
    return len(same_task) > 1


def _before_snapshot(run) -> Snapshot | None:
    """从 run.diff_summary['before'] 还原前快照。"""
    raw = None
    try:
        raw = (run.diff_summary or {}).get("before")
    except Exception:
        raw = None
    if not raw:
        return None
    return Snapshot(
        ok=True,
        head=raw.get("head", "") or "",
        porcelain=raw.get("porcelain", "") or "",
        files=int(raw.get("files") or 0),
        insertions=int(raw.get("insertions") or 0),
        deletions=int(raw.get("deletions") or 0),
        paths=list(raw.get("paths") or []),
        captured_at=raw.get("captured_at", "") or "",
        fingerprint=raw.get("fingerprint", "") or "",
        # 老数据没有 fingerprint 字段 → 指纹不可用 → fail-open（视为有改动）
        fingerprint_ok=bool(raw.get("fingerprint_ok", False)),
    )


def check_diff_gate(db, task, run, body=None) -> Verdict:
    """判定本次提交是否应被拒绝。

    **豁免优先级**（任一命中即跳过判定并写明 reason）：
      开关 off > 无 task > 无 workspace > 显式豁免 > task_kind > 并发冲突
      > 采集失败 > 无前快照
    """
    mode = gate_mode()
    if mode == "off":
        return Verdict(skipped=True, reason="gate_off",
                      message="变更门禁已关闭（MIO_DIFF_GATE=off），本次不判定", mode=mode)
    if task is None:
        return Verdict(skipped=True, reason="no_task",
                      message="无关联任务，跳过变更门禁", mode=mode)
    if not task.workspace:
        return Verdict(skipped=True, reason="no_workspace",
                      message="任务无工作区（workspace 为空），跳过变更门禁", mode=mode)
    if getattr(task, "diff_gate_exempt", False):
        return Verdict(skipped=True, reason="task_exempt",
                      message="任务已显式豁免变更门禁（diff_gate_exempt=true）", mode=mode)
    if (getattr(task, "task_kind", "") or "") in EXEMPT_TASK_KINDS:
        return Verdict(skipped=True, reason="task_kind_exempt",
                      message=f"任务类型 {task.task_kind} 属评审/变更跟踪类，跳过变更门禁", mode=mode)
    if _has_concurrent_run(db, task):
        return Verdict(skipped=True, reason="concurrent_run",
                      message="同一工作区存在其他活跃 run，快照会互相污染，跳过变更门禁", mode=mode)

    after = snapshot(task.workspace)
    if not after.ok:
        return Verdict(skipped=True, reason="collect_failed",
                      message=f"无法采集工作区改动证据（{after.error}），跳过变更门禁，不影响放行",
                      mode=mode)

    before = _before_snapshot(run)
    if before is None:
        return Verdict(skipped=True, reason="no_before_snapshot",
                      message="本次 run 没有前快照（run 创建时未采集），无法判定是否零改动，跳过变更门禁，不影响放行",
                      mode=mode)

    changed = differs(before, after)
    if changed:
        return Verdict(reason="has_changes",
                      message=f"检测到本次改动：{after.files} 个文件 / +{after.insertions} -{after.deletions}",
                      files_changed=after.files,
                      insertions=after.insertions,
                      deletions=after.deletions,
                      mode=mode)

    if mode == "warn":
        return Verdict(reason="empty_diff_warn",
                      message=("本次 run 期间工作区零改动（0 文件 / 0 行）——"
                               "当前为 warn 档，只标注不影响放行"),
                      files_changed=0, mode=mode)

    return Verdict(blocked=True, reason="empty_diff",
                  message=("拒绝提交：声明 success=true，但本次 run 期间工作区零改动"
                           "（0 文件 / 0 行）"),
                  files_changed=0, mode=mode)


# ── 标注合并 ──────────────────────────────────────────────────────────

def merge_annotations(existing, new_items) -> list[dict]:
    """合并标注列表。同一 code 只保留最新一条（避免同一 run 反复提交刷屏）。"""
    items: list[dict] = []
    if isinstance(existing, str):
        try:
            existing = json.loads(existing)
        except Exception:
            existing = []
    if isinstance(existing, list):
        items = [x for x in existing if isinstance(x, dict)]
    by_code = {x.get("code"): x for x in items if x.get("code")}
    order = [x.get("code") for x in items if x.get("code")]
    for it in (new_items or []):
        if not isinstance(it, dict):
            continue
        code = it.get("code")
        if code in by_code:
            by_code[code] = it
        else:
            by_code[code] = it
            order.append(code)
    out = []
    for code in order:
        it = by_code.get(code)
        if it:
            out.append(it)
    return out
