# -*- coding: utf-8 -*-
"""同项目已完成任务的参考信息（lessons）——**只提供信息，不做任何阻断**。

设计（见 docs/taskhub/28f8fceb/spec.md，任务 28f8fceb）：

动机来自真实数据（2026-10-10 实测真实库 284 条 Run）：
- 165 条内容型 `run.result` 里，**87 条（53%）已自发写了测试 / 构建 / lint 结果**，
  但只有 22 条（13%）列出改了哪些文件；
- 结论是「agent 已经在产出质量证据，缺的是把证据回流给下一个 agent」。

因此本模块**只读**地做一件事：从已完成任务里提取三类线索
（测试执行方式 / 文件布局习惯 / 已知坑），在 claim 时一并返回给 agent。

三条硬约束（验收 AC-5.1~ AC-5.3）：
1. **不抛异常**：`recent_lessons` 内部捕获所有异常，一律降级为空结构；
2. **不依赖 git**：只读 DB 里的 task / run —— 真实库有 16 条 workspace 根本不是 git 仓库，
   依赖 git 会让大部分场景取不到数据；
3. **不新增任何 HTTPException**：本模块不 import fastapi。
"""
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from sqlmodel import Session, col, select

from mio_taskhub.models import Run, RunState, Task, TaskState

logger = logging.getLogger("task_lessons")

# 提取测试线索的关键词（大小写不敏感）。按「命令特征 → 结果特征 → 中文描述」分三档，
# 命中任一即视为测试证据。
_TEST_PATTERNS = (
    # 命令特征
    "pytest", "unittest", "npm test", "npm run test", "npm run build", "pnpm test",
    "mvn test", "mvn verify", "gradle", "gradlew", "vitest", "jest", "tox", "nose",
    "rspec", "phpunit", "go test", "cargo test", "dotnet test", "make test",
    "lint", "eslint", "ruff", "flake8", "mypy", "tsc", "eslint --",
    # 结果特征
    "passed", "failed", "all pass", "n passed", "tests passed", "ok.",
    # 中文描述
    "测试", "编译通过", "构建成功", "构建", "lint 0", "单元测试", "回归",
)

# 句子切分：中英文句号 + 分号 + 换行 + 列表项起始。
_SENT_SPLIT = re.compile(r"[。；;\n]+|(?:^|\n)\s*(?:[-*\d]+[.)、]\s*)")

# 每个列表最多返回多少条
_DEFAULT_LIMIT = 5
# 每条文本最多多少字符
_DEFAULT_MAX_CHARS = 200
# 参与提取的历史任务上限（limit 的 3 倍，留出过滤余量）
_TASK_SCAN_FACTOR = 3
# 上限保护：最多扫多少个历史任务，避免极端情况下拖慢 claim
_TASK_SCAN_CAP = 50


def empty_lessons() -> Dict[str, Any]:
    """空结构。字段与有数据时完全一致，避免消费方判空分支。"""
    return {
        "test_hints": [],
        "file_layout": [],
        "pitfalls": [],
        "source_count": 0,
    }


def _clean(text: Optional[str], max_chars: int) -> str:
    """压缩空白 + 截断。"""
    if not text:
        return ""
    s = " ".join(str(text).split())
    if len(s) > max_chars:
        return s[:max_chars].rstrip() + "..."
    return s


def _extract_test_hints(results: Iterable[Optional[str]], limit: int,
                        max_chars: int) -> List[str]:
    """从历史 run.result 文本中提取「测试怎么跑」的线索。

    做法：按句切分 → 命中关键词的句子保留 → 去重 → 截断 → 限条数。
    保留原出现顺序（先出现的通常是该项目最典型的做法）。
    """
    out: List[str] = []
    seen = set()
    patterns = tuple(p.lower() for p in _TEST_PATTERNS)
    for raw in results:
        if not raw or len(raw) > 200_000:
            # 超长 result 直接跳过，避免正则/切分开销
            continue
        low_all = raw.lower()
        if not any(p in low_all for p in patterns):
            continue
        for sent in _SENT_SPLIT.split(raw):
            s = (sent or "").strip()
            if len(s) < 4:
                continue
            if not any(p in s.lower() for p in patterns):
                continue
            # 允许句子被截断后再比对，但去重键用原始句
            key = s[:120]
            if key in seen:
                continue
            seen.add(key)
            out.append(_clean(s, max_chars))
            if len(out) >= limit:
                return out
    return out


def _extract_file_layout(path_lists: Iterable[Iterable[str]], limit: int,
                         max_chars: int) -> List[str]:
    """从历史 files / deliverables 提取文件布局习惯（按目录前缀出现频次降序）。"""
    counts: Dict[str, int] = {}
    for paths in path_lists:
        for p in paths or []:
            if not p or not isinstance(p, str):
                continue
            norm = p.replace("\\", "/").strip()
            if not norm:
                continue
            # 取目录部分（不含文件名）
            head, sep, _ = norm.rpartition("/")
            key = head + "/" if sep else ""
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [_clean(k, max_chars) for k, _ in ordered[:limit]]


def _extract_pitfalls(failures: Iterable[Optional[str]], limit: int,
                      max_chars: int) -> List[str]:
    """从失败任务的 summary / review_result 提取已知坑。"""
    out: List[str] = []
    seen = set()
    for raw in failures:
        s = _clean(raw, max_chars)
        if not s:
            continue
        # 跳过「已修复」类无信息量的记录
        if any(w in s for w in ("cleanup", "probe cleanup", "verify", "取消", "已过时")):
            continue
        key = s[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= limit:
            break
    return out


def _query_history(db: Session, workspace: str, project: str,
                   scan_cap: int) -> List[Task]:
    """查同 workspace（退化到 project）的近期**已完成**任务。"""
    stmt = _scope_stmt(select(Task).where(Task.state == TaskState.COMPLETED),
                       workspace, project)
    if stmt is None:
        return []
    stmt = stmt.order_by(col(Task.completed_at).desc()).limit(scan_cap)
    return list(db.exec(stmt).all())


def _query_failures(db: Session, workspace: str, project: str,
                    scan_cap: int) -> List[Task]:
    """查同 workspace（退化到 project）的近期**失败**任务，用于提取已知坑。

    注意：必须**独立于** `_query_history` 查询 —— 失败任务与已完成任务是互斥的
    状态，若先按 COMPLETED 过滤再找 FAILED，结果恒为空（这是首版实现的 bug，
    由 TC-2 用例发现）。
    """
    stmt = _scope_stmt(select(Task).where(Task.state == TaskState.FAILED),
                       workspace, project)
    if stmt is None:
        return []
    stmt = stmt.order_by(col(Task.failed_at).desc()).limit(scan_cap)
    try:
        return list(db.exec(stmt).all())
    except Exception:
        logger.warning("lessons: 查询失败任务失败", exc_info=True)
        return []


def _scope_stmt(stmt, workspace: str, project: str):
    """按 workspace（优先）或 project（退化）限定范围。无依据时返回 None。"""
    if workspace:
        return stmt.where(Task.workspace == workspace)
    if project:
        # workspace 优先；缺失时退化到 project，保证非代码任务也能命中
        return stmt.where((Task.project == project) | (Task.workspace == ""))
    return None


def _load_run_results(db: Session, task_ids: List[str]) -> Dict[str, str]:
    """批量取这些任务最新一条有内容的 run.result。

    只取 FINISHED 的 run —— 未跑完的 run 没有参考价值。
    """
    if not task_ids:
        return {}
    stmt = (select(Run)
            .where(col(Run.task_id).in_(task_ids))
            .where(Run.state == RunState.FINISHED)
            .order_by(col(Run.finished_at).desc()))
    out: Dict[str, str] = {}
    try:
        rows = db.exec(stmt).all()
    except Exception:
        logger.warning("lessons: 查询历史 run 失败", exc_info=True)
        return out
    for r in rows:
        # 每个任务只取第一条（即最新的一条）有内容的 result
        if r.task_id in out:
            continue
        #阈值取 4：与 _extract_test_hints 的最小句长一致。**不能用审计脚本里的
        # len>20** —— 那对 lessons 太粗，「测试：npm run build 成功」这种短而
        # 有价值的结论会被整条丢掉（由 TC-5 实测发现）。
        if r.result and len(r.result.strip()) >= 4:
            out[r.task_id] = r.result
    return out


def _load_experience(project: str, limit: int, max_chars: int) -> List[str]:
    """从 memory_store 取历史经验（FR-3）。失败降级为空。"""
    if not project:
        return []
    try:
        from mio_taskhub.memory_store import search_entities
        rows = search_entities([project], kind="experience", limit=limit)
    except Exception:
        logger.warning("lessons: 读取 memory_store 经验失败", exc_info=True)
        return []
    out: List[str] = []
    for ent in rows or []:
        for obs in (ent.get("observations") or [])[:limit]:
            s = _clean(obs, max_chars)
            if s:
                out.append(s)
            if len(out) >= limit:
                break
        if len(out) >= limit:
            break
    return out


def recent_lessons(db: Session, workspace: str = "", project: str = "",
                   limit: int = _DEFAULT_LIMIT,
                   max_chars: int = _DEFAULT_MAX_CHARS) -> Dict[str, Any]:
    """返回同项目近期已完成任务的参考信息。

    **本函数不抛异常**（FR-2 / AC-6）：任何失败都返回 `empty_lessons()`，
    绝不阻断 claim。

    参数：
    - `db`：数据库会话（由调用方提供；`build_claim_context` 无 db 时自行开短会话）
    - `workspace`：任务工作区，优先匹配
    - `project`：workspace 为空时退化匹配
    - `limit`：每个列表最多返回几条
    - `max_chars`：每条文本最多多少字符

    返回 `{"test_hints", "file_layout", "pitfalls", "source_count"}`，
    无数据时四个字段分别为空数组 / 0。
    """
    try:
        workspace = (workspace or "").strip()
        project = (project or "").strip()
        limit = max(1, int(limit))
        max_chars = max(20, int(max_chars))

        scan_cap = min(limit * _TASK_SCAN_FACTOR, _TASK_SCAN_CAP)
        tasks = _query_history(db, workspace, project, scan_cap)
        if not tasks:
            # 无历史时仍尝试取memory_store 经验（可能来自其他 workspace 的同项目任务）
            pits = _load_experience(project, limit, max_chars)
            out = empty_lessons()
            out["pitfalls"] = pits
            return out

        task_ids = [t.id for t in tasks]
        results = _load_run_results(db, task_ids)

        test_hints = _extract_test_hints(results.values(), limit, max_chars)
        file_layout = _extract_file_layout(
            [list(t.files or []) + list(t.deliverables or []) for t in tasks],
            limit, max_chars)
        # 已知坑：同 workspace 的**失败**任务（独立查询，与已完成集合互斥）
        failed = _query_failures(db, workspace, project, scan_cap)
        pitfalls = _extract_pitfalls(
            [(t.review_result or t.acceptance_criteria or "") for t in failed],
            limit, max_chars)
        # 合并 memory_store 里的项目经验
        for s in _load_experience(project, limit, max_chars):
            if s not in pitfalls:
                pitfalls.append(s)
            if len(pitfalls) >= limit:
                pitfalls = pitfalls[:limit]
                break

        return {
            "test_hints": test_hints,
            "file_layout": file_layout,
            "pitfalls": pitfalls,
            "source_count": len(tasks),
        }
    except Exception:
        # 契约：任何异常都降级为空结构，不外泄
        logger.warning("lessons: recent_lessons 异常，已降级为空", exc_info=True)
        return empty_lessons()


# ── 经验沉淀（FR-3）────────────────────────────────────────────────────────

def write_experience(task: Any, run_result: Optional[str]) -> bool:
    """把本次完成的可复用经验写入 memory_store（任务 28f8fceb FR-3）。

    **失败不影响任务状态**：任何异常只记日志并返回 False（AC-3.4 / TC-14）。

    存储沿用 `memory_store` 既有的本地 JSONL，**不新增数据库表**。
    项目名必须写进 observations —— `search_entities` 的 project 过滤就是
    对 observations 文本做子串匹配（`memory_store.py:115-119`）。

    返回 True 表示确实写入了。
    """
    try:
        project = str(getattr(task, "project", "") or "").strip()
        workspace = str(getattr(task, "workspace", "") or "").strip()
        if not project and not workspace:
            return False

        # 1) 测试方式：直接从本次 result 里抽（这是最有价值的经验）
        hints = _extract_test_hints([run_result], _DEFAULT_LIMIT, _DEFAULT_MAX_CHARS)
        if not hints:
            return False

        # 2) 文件布局：本次任务的 files / deliverables 目录前缀
        layout = _extract_file_layout(
            [list(getattr(task, "files", None) or [])
             + list(getattr(task, "deliverables", None) or [])],
            _DEFAULT_LIMIT, _DEFAULT_MAX_CHARS)

        # 3) observations 必须含项目名，否则后续按 project 检索不到
        obs: List[str] = [f"project={project or workspace} 测试方式: {h}" for h in hints]
        if layout:
            obs.append(f"project={project or workspace} 文件布局: {', '.join(layout)}")

        name = f"{project or workspace}-测试经验"
        from mio_taskhub.memory_store import add_entity
        add_entity(name, "experience", obs)
        logger.info("lessons: 已沉淀经验 %s（%d 条）", name, len(obs))
        return True
    except Exception:
        logger.warning("lessons: 写入经验失败（不影响任务状态）", exc_info=True)
        return False
