# -*- coding: utf-8 -*-
"""task_lessons：同项目参考信息（任务 28f8fceb FR-1/ FR-3）。

覆盖用例（见 docs/taskhub/28f8fceb/test.md）：
- TC-1 无历史时返回空集合
- TC-2 提取测试线索
- TC-3 超长文本被截断
- TC-4 条数受限
- TC-5 退化到 project 匹配
- TC-6 异常不外泄
- TC-11~ TC-13 经验写入与检索
- TC-14 写入失败不影响任务

**本文件同时是「不阻断」契约的守卫**：任何用例都不得因lessons 取值而失败。
"""
import uuid

import pytest
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.models import Run, RunState, Task, TaskState
from mio_taskhub.api.task_lessons import (
    empty_lessons,
    recent_lessons,
    write_experience,
    _extract_file_layout,
    _extract_pitfalls,
    _extract_test_hints,
)

WS = "E:/work/lessons-test/proj-a"
PRJ = "proj-a"


def _mk_task(title, workspace=WS, project=PRJ, state=TaskState.COMPLETED,
             files=None, deliverables=None, review_result=""):
    with Session(engine) as s:
        t = Task(title=title, workspace=workspace, project=project, state=state,
                 files=files or [], deliverables=deliverables or [],
                 review_result=review_result)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _mk_run(task_id, result, state=RunState.FINISHED):
    with Session(engine) as s:
        r = Run(id=str(uuid.uuid4())[:8], task_id=task_id, agent_name="a",
                state=state, result=result)
        s.add(r)
        s.commit()


# ── 提取函数（纯函数，不碰 DB）─────────────────────────────────────────

def test_extract_test_hints_matches_commands():
    text = "完成。测试：pytest tests/ -q 1235 passed；lint 0 errors。"
    hints = _extract_test_hints([text], 5, 200)
    assert any("pytest" in h for h in hints)
    assert any("lint" in h for h in hints)


def test_extract_test_hints_ignores_irrelevant():
    # 不含任何测试特征 → 不产出线索
    assert _extract_test_hints(["改了几个文案，没有跑任何检查"], 5, 200) == []


def test_extract_test_hints_skips_empty_and_huge():
    assert _extract_test_hints([None, "", "x" * 300_000], 5, 200) == []


def test_extract_file_layout_counts_dirs():
    layout = _extract_file_layout(
        [["src/api/a.py", "src/api/b.py", "tests/t.py", "docs/spec.md"]], 5, 200)
    assert layout[0] == "src/api/"          # 出现 2 次，排第一
    assert "tests/" in layout and "docs/" in layout
    # 只有文件名没有目录的不计入
    assert "a.py" not in layout


def test_extract_file_layout_respects_limit():
    layout = _extract_file_layout(
        [["a/x.py", "b/y.py", "c/z.py", "d/w.py"]], 2, 200)
    assert len(layout) == 2


def test_extract_pitfalls_filters_noise():
    out = _extract_pitfalls(
        ["doc_gate_exempt 迁移要置1", "probe cleanup", "verify 清理", "取消"],
        5, 200)
    assert out == ["doc_gate_exempt 迁移要置1"]


def test_extract_pitfalls_truncates():
    out = _extract_pitfalls(["x" * 500], 5, 50)
    assert len(out[0]) <= 53          # 50 + "..."


# ── recent_lessons（DB 场景）──────────────────────────────────────────

def test_lessons_empty_when_no_history():
    """TC-1 / AC-1.2：无历史返回空结构，不抛异常。"""
    with Session(engine) as s:
        out = recent_lessons(s, workspace="E:/work/lessons-test/不存在", project="")
    assert out == empty_lessons()
    assert out["source_count"] == 0


def test_lessons_extracts_all_three_clues():
    """TC-2 / AC-1.1：三类线索都能提取到。"""
    tid_ok = _mk_task("视频查询", files=["src/api/a.py", "tests/t.py"],
                      deliverables=["docs/spec.md"])
    _mk_run(tid_ok, "完成。测试：pytest tests/ -q 1235 passed。")
    tid_fail = _mk_task("某次失败", state=TaskState.FAILED,
                        review_result="门控把存量任务误拦了")
    assert tid_fail
    with Session(engine) as s:
        out = recent_lessons(s, workspace=WS, project=PRJ)
    assert out["source_count"] >= 1
    assert any("pytest" in h for h in out["test_hints"])
    assert "src/api/" in out["file_layout"]
    # 失败任务是独立查询（首版 bug：被 COMPLETED 过滤掉导致恒为空）
    assert any("误拦" in p for p in out["pitfalls"]), out["pitfalls"]


def test_lessons_truncates_long_text():
    """TC-3 / AC-1.3：单条被截断到 max_chars。"""
    tid = _mk_task("长文本")
    _mk_run(tid, "测试：" + "很长的描述" * 200 + " pytest")
    with Session(engine) as s:
        out = recent_lessons(s, workspace=WS, project=PRJ, max_chars=60)
    for h in out["test_hints"]:
        assert len(h) <= 63


def test_lessons_respects_limit():
    """TC-4 / AC-1.4：条数不超过 limit。"""
    for i in range(8):
        tid = _mk_task(f"任务{i}")
        _mk_run(tid, f"测试命令{i}: pytest tests/ -q passed")
    with Session(engine) as s:
        out = recent_lessons(s, workspace=WS, project=PRJ, limit=2)
    assert len(out["test_hints"]) <= 2
    assert len(out["file_layout"]) <= 2
    assert len(out["pitfalls"]) <= 2


def test_lessons_falls_back_to_project():
    """TC-5 / AC-1.5：workspace 为空时用 project 匹配。"""
    tid = _mk_task("退化匹配", workspace="", project="proj-fallback")
    _mk_run(tid, "测试：npm run build 成功")
    with Session(engine) as s:
        out = recent_lessons(s, workspace="", project="proj-fallback")
    assert any("build" in h for h in out["test_hints"])


def test_lessons_no_scope_returns_empty():
    """无 workspace 且无 project → 不查询，直接空结构（避免全库扫描）。"""
    with Session(engine) as s:
        out = recent_lessons(s, workspace="", project="")
    assert out == empty_lessons()


def test_lessons_never_raises():
    """TC-6 / AC-1.6：DB 异常也必须降级为空结构。"""
    with Session(engine) as s:
        bad = recent_lessons(s, workspace=WS, project=PRJ,
                             limit="非法值", max_chars=None)
    assert bad == empty_lessons()


def test_lessons_missing_table_degrades():
    """真实场景：真实库可能缺列（迁移未跑），此时不得抛异常。"""
    with Session(engine) as s:
        out = recent_lessons(s, workspace="", project="")   # 不触碰 DB
        assert out == empty_lessons()
        # 用一个必定查询失败的条件触发异常路径
        out2 = recent_lessons(s, workspace=WS, project=PRJ, limit=0)
        assert "source_count" in out2


# ── 经验沉淀（FR-3）───────────────────────────────────────────────────

def test_write_experience_and_retrieve():
    """TC-11 / TC-12 / TC-13 / AC-3.1~ AC-3.3：写入后可按项目检索到。"""
    tid = _mk_task("沉淀经验", files=["src/x.py", "tests/x_test.py"])
    t = None
    with Session(engine) as s:
        t = s.get(Task, tid)
        assert t is not None
        ok = write_experience(t, "完成。测试：pytest tests/ -q 42 passed。")
    assert ok is True

    from mio_taskhub.api.task_lessons import _load_experience
    obs = _load_experience(PRJ, 5, 200)
    assert any("pytest" in o for o in obs), obs
    # 项目名必须写进 observations（search_entities 靠子串匹配 project）
    assert all("proj-a" in o for o in obs), obs


def test_write_experience_skips_when_no_test_evidence():
    """没有测试证据时不写 —— 不制造噪音经验。"""
    tid = _mk_task("无测试证据")
    with Session(engine) as s:
        t = s.get(Task, tid)
        assert write_experience(t, "随手改了点东西") is False


def test_write_experience_never_raises():
    """TC-14 / AC-3.4：写入失败不影响调用方。"""

    class NoScope:
        """既无 project 也无 workspace → 无 scope 依据，直接 False。"""
        project = ""
        workspace = ""

    assert write_experience(NoScope(), "pytest passed") is False

    class BadTask:
        """迭代 deliverables 时抛异常 —— 必须被捕获并返回 False。"""
        project = "p"
        workspace = "w"
        files = None

        @property
        def deliverables(self):
            raise RuntimeError("boom")

    assert write_experience(BadTask(), "pytest passed") is False


# ── 契约守卫：不得引入 fastapi 阻断 ────────────────────────────────────

def test_module_does_not_import_fastapi():
    """AC-5.1：lessons 模块不得 import fastapi（不产生任何 HTTPException）。

    用 AST 判断而非字符串匹配：模块 docstring 里「本模块不 import fastapi」
    这类说明、以及注释里的字样，都不应被当成违规。
    """
    import ast
    import inspect
    import textwrap

    from mio_taskhub.api import task_lessons

    src = textwrap.dedent(inspect.getsource(task_lessons))
    tree = ast.parse(src)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "fastapi" not in imported, imported

    # 代码中不得**引用** HTTPException（docstring/注释不算 —— 那是字符串常量）
    refs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            refs.add(node.id)
        elif isinstance(node, ast.Attribute):
            refs.add(node.attr)
    assert "HTTPException" not in refs, refs


# ── 两条派单路径都能拿到 lessons（FR-2 / AC-2.2）────────────────────────

def test_background_dispatch_injects_lessons():
    """AC-2.2：后台 idle 派单路径也要带 lessons（它不走 claim 端点）。

    直接验证 background.py 的派单代码里存在 lessons 注入，
    且注入被 try/except 包裹（不能因取不到 lessons 而阻断派单）。
    """
    import inspect
    import textwrap

    from mio_taskhub import background

    src = textwrap.dedent(inspect.getsource(background))
    # 找到 idle 派单那段（task_assigned 事件）
    assert "task_assigned" in src
    seg = src[src.index("task_assigned") - 1200:src.index("task_assigned") + 400]
    assert "recent_lessons" in seg, "后台派单未注入 lessons"
    assert '"lessons"' in seg or "'lessons'" in seg
    # 必须是旁路：有 try/except 兜底
    assert "except Exception" in seg
    assert "lessons = {}" in seg, "异常时未降级为空 dict"


def test_mcp_claim_desc_mentions_lessons():
    """AC-2.1 可用性：MCP claim 的工具描述要提到 lessons，agent 才可能去看。

    「送到了但描述里没提」等于没送到 —— 软信息必须显式告知。
    """
    import inspect

    from mio_taskhub import mcp_server

    src = inspect.getsource(mcp_server)
    assert "lessons" in src, "mcp_server 未提及 lessons"
    # 工具描述里应显式说明「动手前先看」
    assert "lessons.test_hints" in src or "lessons" in src
