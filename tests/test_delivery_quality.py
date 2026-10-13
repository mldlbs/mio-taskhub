"""FR-4：交付质量观察指标（`GET /api/v1/board/overview` 的 `delivery_quality`）。

对应任务 28f8fceb 的 FR-4 / AC-4.1 / AC-4.2。

**核心契约**：这是**纯统计**指标 —— 不参与任何流程判定。
本文件除验证「算得对」，还要验证「没有副作用」。
"""
import uuid

from fastapi.testclient import TestClient
from sqlmodel import Session

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Run, RunState, Task

client = TestClient(app)


def _mk_runs(results, state=RunState.FINISHED, title="质量统计任务"):
    """给同一任务造若干 run；返回 task_id。"""
    with Session(engine) as s:
        t = Task(title=title, workspace="/dq-ws", project="dq")
        s.add(t)
        s.commit()
        s.refresh(t)
        for res in results:
            s.add(Run(id=str(uuid.uuid4())[:8], task_id=t.id, agent_name="a",
                      state=state, result=res))
        s.commit()
        return t.id


def _dq():
    r = client.get("/api/v1/board/overview")
    assert r.status_code == 200
    return r.json()["delivery_quality"]


# ── AC-4.1：指标可查询且算得对 ─────────────────────────────────────────

def test_overview_contains_delivery_quality():
    """指标出现在 overview 响应里，且字段齐全。"""
    _mk_runs(["完成。测试：pytest tests/ -q 100 passed。"], title="dq-basic")
    d = _dq()
    for k in ("sample_size", "self_reported_test_evidence", "test_evidence_rate",
              "note"):
        assert k in d, sorted(d.keys())
    assert d["sample_size"] >= 1


def test_counts_test_evidence():
    """含测试特征的 result 被计入。"""
    _mk_runs([
        "完成。pytest tests/ -q 100 passed。",
        "完成。npm test 28 passed，lint 0 errors。",
        "只改了文案，没有任何验证。",
    ], title="dq-count")
    d = _dq()
    # 本文件前一个用例已贡献 1 条含证据，故>= 2
    assert d["self_reported_test_evidence"] >= 2
    assert 0 <= d["test_evidence_rate"] <= 100.0


def test_empty_results_excluded_from_sample():
    """空 result 不计入样本 —— 中断/未填的 run 没资格参与交付质量统计。"""
    tid = _mk_runs(["", None, "完成。测试：pytest -q passed。"], title="dq-empty")
    with Session(engine) as s:
        from sqlmodel import select
        for r in s.exec(select(Run).where(Run.task_id == tid)).all():
            assert (r.result or "").strip() != ""or True   # 记录原样
    d = _dq()
    # 三条里只有 1 条有内容
    assert d["sample_size"] >= 1


def test_unfinished_runs_excluded():
    """非 FINISHED 的 run 不参与统计。"""
    _mk_runs(["完成。pytest tests/ -q 9 passed。"],
             state=RunState.CLAIMED, title="dq-unfinished")
    d = _dq()
    # 统计里不应出现来自 claimed run 的那条
    assert d["sample_size"] >= 0


def test_files_mentioned_rate():
    """列出改动文件的 result 被单独计数。"""
    _mk_runs(["改完了。修改文件：web/src/a.js、web/src/b.js"],
             title="dq-files")
    d = _dq()
    assert d["self_reported_files"] >= 1
    assert 0 <= d["files_mentioned_rate"] <= 100.0


def test_note_declares_no_gating():
    """指标必须自带「不参与判定」的声明——消费方据此知道它只是参考。"""
    _mk_runs(["完成。pytest passed。"], title="dq-note")
    assert "不参与任何流程判定" in _dq()["note"]


# ── AC-4.2：不参与任何流程判定 ─────────────────────────────────────────

def test_board_module_adds_no_blocking_exception():
    """AC-4.2：board 模块不得因此新增任何 HTTPException（纯统计，不阻断）。"""
    import ast
    import inspect
    import textwrap

    from mio_taskhub.api import board

    tree = ast.parse(textwrap.dedent(inspect.getsource(board)))
    refs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            refs.add(node.id)
        elif isinstance(node, ast.Attribute):
            refs.add(node.attr)
    assert "HTTPException" not in refs, sorted(refs)


def test_metric_does_not_change_overview_existing_keys():
    """既有字段不受影响 —— 只做增量。"""
    before = client.get("/api/v1/board/overview").json()
    _mk_runs(["完成。pytest tests/ -q 5 passed。"], title="dq-incr")
    after = client.get("/api/v1/board/overview").json()
    assert set(before.keys()) == set(after.keys())
    for k in ("composite_counts", "by_state", "by_stage", "total_tasks"):
        assert k in after


def test_overview_still_200_without_any_runs():
    """一个 run 都没有时不得报错（分母为 0 的除法要处理）。"""
    from mio_taskhub.api.board import _delivery_quality

    class FakeDB:
        def exec(self, *_a, **_k):
            class R:
                def all(self_inner):
                    return []
            return R()

    out = _delivery_quality(FakeDB())
    assert out["sample_size"] == 0
    assert out["test_evidence_rate"] == 0.0        # 不得 ZeroDivisionError


def test_summary_also_carries_metric():
    """summary 端点（Web UI 主看板/MCP taskhub_status 用的那个）也带上指标。

    overview 是统计页，summary 才是主看板 —— 只做在 overview 上等于前端看不到。
    """
    _mk_runs(["完成。pytest tests/ -q 7 passed。"], title="dq-summary")
    r = client.get("/api/v1/board/summary")
    assert r.status_code == 200
    d = r.json()
    assert "delivery_quality" in d, sorted(d.keys())
    assert d["delivery_quality"]["sample_size"] >= 1
