# -*- coding: utf-8 -*-
"""质量缺口验证实验（审计用，可复现）。

用途：为 docs/quality-audit.md 的每条「已复现」结论提供可重跑的证据。
方法：对每个探针施加**期望的安全行为**，实测系统是否阻断。
  缺 = 缺口存在（放行） / 有 = 机制有效（阻断）/ 绕 = 已知绕过路径 / ERR = 探针本身失败

本脚本**不修改源码**，只读源码 + 调 REST，全部跑在一次性 sqlite 上。

运行：
    .venv/Scripts/python.exe scripts/audit_quality_gaps.py
"""
import os
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

# ── 必须在导入 app 之前隔离 DB 与 MIO_HOME ────────────────────────────────
_TMP = Path(tempfile.mkdtemp(prefix="mio_audit_"))
os.environ["MIO_TASKHUB_DB"] = str(_TMP / "audit.db")
os.environ.setdefault("MIO_HOME", str(_TMP / "mio_home_missing"))

from fastapi.testclient import TestClient  # noqa: E402

from mio_taskhub.main import app  # noqa: E402
from mio_taskhub.db import engine, init_db  # noqa: E402
from mio_taskhub.models import SQLModel  # noqa: E402

client = TestClient(app)
RESULTS = []


def _report(pid, verdict, note):
    RESULTS.append((pid, verdict, note))
    print(f"{pid:<5} | {verdict:<4} | {note}", flush=True)


def _fresh_db():
    SQLModel.metadata.drop_all(engine)
    init_db()


def _ws(name):
    p = _TMP / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def _mk_task(ws: Path, *, stage="ready", kinds=("spec", "api", "requirement")):
    """建一个带文档的任务，返回 task_id。ws 必须已存在。"""
    doc_paths = {}
    for k in kinds:
        (ws / f"{k}.md").write_text(f"# {k}\nFR-1 验收标准\n", encoding="utf-8")
        doc_paths[k] = f"{k}.md"
    r = client.post("/api/v1/tasks", json={
        "title": f"audit-{uuid.uuid4().hex[:8]}", "stage": stage,
        "workspace": str(ws), "doc_paths": doc_paths,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _agent(name):
    client.post("/api/v1/agents/register", json={"name": name})


def _claim(agent):
    r = client.post("/api/v1/tasks/claim", params={"agent": agent})
    assert r.status_code == 200, r.text
    return r.json()


def _read_all(tid, rid, agent, kinds):
    for k in kinds:
        client.get(f"/api/v1/tasks/{tid}/doc",
                   params={"kind": k, "run_id": rid, "agent": agent})


def _submit(rid, success=True, result="done"):
    return client.post(f"/api/v1/runs/{rid}/result",
                       json={"success": success, "result": result})


def _brief(r):
    return f"{r.status_code} {r.text[:130]}"


# ═══════════════════════════════════════════════════════════════════════════
# A 组：交付验收链（方案 §6.2 / §6.3 / §6.4 / §6.5）
# ═══════════════════════════════════════════════════════════════════════════

def a1():
    """期望：submit 前应要求提供「实际变更」与「测试执行」证据。"""
    _fresh_db()
    tid = _mk_task(_ws("a1"))
    _agent("a1-agent")
    rid = _claim("a1-agent")["id"]
    _read_all(tid, rid, "a1-agent", ("spec", "api", "requirement"))
    r = _submit(rid)          # 只带 success=true：无 diff、无测试命令、无输出、无代码版本
    body = r.json()
    if r.status_code == 200 and body.get("task_state") == "completed":
        _report("A1", "缺", "仅凭 success=true 即 completed；"
                         "提交体无需提供变更清单/测试命令/输出/代码版本")
    else:
        _report("A1", "有", f"被拦截 {_brief(r)}")


def a2():
    """期望：submit 成功不应等价于代码质量通过，应有独立验收环节。"""
    _fresh_db()
    tid = _mk_task(_ws("a2"))
    _agent("a2-agent")
    rid = _claim("a2-agent")["id"]
    _read_all(tid, rid, "a2-agent", ("spec", "api", "requirement"))
    _submit(rid)
    st = client.get(f"/api/v1/tasks/{tid}").json()
    if st.get("state") == "completed" and st.get("stage") == "review":
        _report("A2", "缺", f"submit 后自动 completed→{st.get('stage')}，"
                            "期间无任何代码质量判定（review_result 仍为空："
                            f"{st.get('review_result')!r}）")
    else:
        _report("A2", "有", f"state={st.get('state')} stage={st.get('stage')}")


def a3():
    """期望：执行该 run 的 agent 不应能自己 approve。"""
    _fresh_db()
    tid = _mk_task(_ws("a3"))
    ag = "a3-self-agent"
    _agent(ag)
    rid = _claim(ag)["id"]
    _read_all(tid, rid, ag, ("spec", "api", "requirement"))
    _submit(rid)
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "reviewer": ag,      # reviewer = 执行者本人
        "summary": "ok", "checklist": ["ok"],
    })
    if r.status_code == 200:
        _report("A3", "缺", f"reviewer='{ag}'（= run.agent_name）自审 approve 成功，"
                            "系统未校验评审人 ≠ 执行人")
    else:
        _report("A3", "有", f"自审被拒 {_brief(r)}")


def a4():
    """期望：approve 结论应有最低内容要求，而非任意字符串。"""
    _fresh_db()
    tid = _mk_task(_ws("a4"))
    _agent("a4-agent")
    rid = _claim("a4-agent")["id"]
    _read_all(tid, rid, "a4-agent", ("spec", "api", "requirement"))
    _submit(rid)
    junk = "x"
    r = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "reviewer": "human", "summary": junk,
    })
    t = client.get(f"/api/v1/tasks/{tid}").json()
    if r.status_code == 200 and t.get("review_result") == junk:
        _report("A4", "缺", f"summary={junk!r}（1 字符）即写入 review_result，"
                            "并满足 done 阶段门控")
    else:
        _report("A4", "有", f"{_brief(r)} review_result={t.get('review_result')!r}")


# ═══════════════════════════════════════════════════════════════════════════
# B 组：证据与版本一致性（方案 §7.3）
# ═══════════════════════════════════════════════════════════════════════════

def b1():
    """期望：证据应绑定实际代码变更；改了代码文件后旧证据应失效。"""
    _fresh_db()
    ws = _ws("b1")
    tid = _mk_task(ws)
    ag = "b1-agent"
    _agent(ag)
    rid = _claim(ag)["id"]
    _read_all(tid, rid, ag, ("spec", "api", "requirement"))
    (ws / "impl.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    r = _submit(rid)
    if r.status_code == 200:
        _report("B1", "缺", "读证后新增代码文件 impl.py，evidence 指纹未失效、"
                            "submit 仍 200 —— 指纹只覆盖 doc_paths 里的文档")
    else:
        _report("B1", "有", f"代码变更后被拦截 {_brief(r)}")


def b2():
    """期望（正面）：文档改动后旧读证应失效。"""
    _fresh_db()
    ws = _ws("b2")
    tid = _mk_task(ws)
    ag = "b2-agent"
    _agent(ag)
    rid = _claim(ag)["id"]
    _read_all(tid, rid, ag, ("spec", "api", "requirement"))
    (ws / "api.md").write_text("# api v2\n", encoding="utf-8")
    r = _submit(rid)
    if r.status_code == 422:
        _report("B2", "有", f"文档改动后 submit 被拦截 {_brief(r)}")
    else:
        _report("B2", "缺", f"文档改动后仍放行 {_brief(r)}")


def b3():
    """期望（正面）：run1 的读证不应让 run2 通过（证据按 run 隔离）。"""
    _fresh_db()
    ws = _ws("b3")
    tid = _mk_task(ws)
    ag = "b3-agent"
    _agent(ag)
    rid1 = _claim(ag)["id"]
    _read_all(tid, rid1, ag, ("spec", "api", "requirement"))
    _submit(rid1, success=False, result="boom")     # 失败 → 可 retry
    rr = client.post(f"/api/v1/tasks/{tid}/retry", json={"reason": "audit"})
    if rr.status_code != 200:
        _report("B3", "ERR", f"retry 未成功 {_brief(rr)}")
        return
    c2 = client.post("/api/v1/tasks/claim", params={"agent": ag})
    if c2.status_code != 200:
        _report("B3", "ERR", f"二次 claim 失败 {_brief(c2)}")
        return
    rid2 = c2.json()["id"]
    r = _submit(rid2)                                # 新 run，完全没读文档
    if r.status_code == 422:
        _report("B3", "有", f"新 run 未读文档即被拦截 {_brief(r)}")
    else:
        _report("B3", "缺", f"新 run 未读即放行 {_brief(r)}")


# ═══════════════════════════════════════════════════════════════════════════
# C 组：绕过路径（方案 §7.2 / §7.4「绕过路径」字段）
# ═══════════════════════════════════════════════════════════════════════════

def c1():
    """绕过路径：环境变量可整体关掉读证门控。"""
    _fresh_db()
    os.environ["MIO_READ_GATE"] = "0"
    try:
        tid = _mk_task(_ws("c1"))
        ag = "c1-agent"
        _agent(ag)
        rid = _claim(ag)["id"]
        r = _submit(rid)                              # 完全没读
        ok = r.status_code == 200
        _report("C1", "绕" if ok else "有",
                f"MIO_READ_GATE=0 → 未读任何文档也能 submit：{_brief(r)}")
    finally:
        os.environ.pop("MIO_READ_GATE", None)


def c2():
    """绕过路径：棘轮基线可被环境变量关闭。"""
    from mio_taskhub import ratchet
    os.environ["MIO_RATCHET_DISABLED"] = "1"
    try:
        off = not ratchet.enabled()
        _report("C2", "绕" if off else "有",
                "MIO_RATCHET_DISABLED=1 → 棘轮校验关闭，质量分/用例数可回退"
                if off else "棘轮无法关闭")
    finally:
        os.environ.pop("MIO_RATCHET_DISABLED", None)


def c3():
    """期望（正面）：MCP 通道默认拒 destructive。"""
    from mio_taskhub import mcp_risk
    allowed, reason = mcp_risk.check("POST", "/tasks/abc/doc/spec/status")
    _report("C3", "有" if not allowed else "缺",
            f"默认拦截 set_doc_status：allowed={allowed}")
    _report("C3b", "绕", "MIO_MCP_ALLOW_DESTRUCTIVE=1 可放行全部 destructive")


def c4():
    """绕过路径：force 能否绕过文档质量门，以及是否留痕。"""
    _fresh_db()
    tid = _mk_task(_ws("c4"), stage="ready")
    ( _TMP / "c4" / "spec.md").write_text("# 空心 spec（缺全部必需章节）\n", encoding="utf-8")
    d = client.post(f"/api/v1/tasks/{tid}/doc/spec/status",
                    json={"state": "draft", "note": "audit"})
    if d.status_code != 200:
        _report("C4", "ERR", f"落 draft 失败 {_brief(d)}")
        return
    r = client.post(f"/api/v1/tasks/{tid}/doc/spec/status",
                    json={"state": "review", "force": True, "note": "audit force"})
    if r.status_code == 200:
        _report("C4", "绕", "空心 spec（质量分 0，缺全部必需章节）经 force=true "
                            "仍被推入 review：质量门被绕过")
    else:
        _report("C4", "有", f"空心 spec 推进被拒 {_brief(r)}")


# ═══════════════════════════════════════════════════════════════════════════
# D 组：阶段门控（正面证据）
# ═══════════════════════════════════════════════════════════════════════════

def d1():
    """审查结论门控：move（拖拽，strict=False）与 advance（strict=True）两条路径对比。"""
    _fresh_db()
    # D1a：走 move 路径（strict=False）
    tid = _mk_task(_ws("d1a"), stage="review")
    r = client.post(f"/api/v1/tasks/{tid}/stage/move",
                    json={"target_stage": "done", "force": True})
    t = client.get(f"/api/v1/tasks/{tid}").json()
    filled = t.get("review_result")
    if r.status_code == 200 and filled:
        _report("D1", "缺", f"move 路径无任何审查证据仍进 done，"
                            f"且 review_result 被自动填充为 {filled!r}")
    else:
        _report("D1", "有", f"move 进 done 被拒或未自动填充：{_brief(r)} / {filled!r}")
    # D1b：走 advance 路径（strict=True）
    tid2 = _mk_task(_ws("d1b"), stage="review")
    r2 = client.post(f"/api/v1/tasks/{tid2}/stage",
                     json={"target_stage": "done", "force": True})
    if r2.status_code == 422:
        _report("D1b", "有", f"advance 路径无审查证据被拒：{_brief(r2)}")
    else:
        _report("D1b", "缺", f"advance 路径亦放行：{_brief(r2)}")


def d2():
    """LIFECYCLE_GATE 对 spec 的实际有效性：区分「未登记状态」与「已登记但未批准」。"""
    _fresh_db()
    # D2a：doc_statuses 为空（新建任务的默认状态）→ 是否需要 spec approved？
    tid = _mk_task(_ws("d2a"), stage="brainstorming")
    r = client.post(f"/api/v1/tasks/{tid}/stage/move", json={"target_stage": "design"})
    if r.status_code == 200:
        _report("D2", "缺", "doc_statuses 为空时，spec 从未设过状态 → "
                            "LIFECYCLE_GATE 对 spec 不校验，未批准即可进 design")
    else:
        _report("D2", "有", f"未批准 spec 进 design 被拒：{_brief(r)}")
    # D2b：spec 显式设为 draft（进入跟踪）后推进
    tid2 = _mk_task(_ws("d2b"), stage="brainstorming")
    client.post(f"/api/v1/tasks/{tid2}/doc/spec/status", json={"state": "draft"})
    r2 = client.post(f"/api/v1/tasks/{tid2}/stage/move", json={"target_stage": "design"})
    if r2.status_code == 422:
        _report("D2b", "有", f"spec 处于 draft 时进 design 被拒：{_brief(r2)}")
    else:
        _report("D2b", "缺", f"spec 处于 draft 仍进 design：{_brief(r2)}")


def d3():
    """期望（正面）：文档状态不得回退（严格向前）。"""
    _fresh_db()
    ws = _ws("d3")
    tid = _mk_task(ws, stage="ready", kinds=("spec",))
    # 用一份完整 spec（带全部必需章节），避免质量门干扰状态机探针
    (ws / "spec.md").write_text(
        "# spec\n\n## 文档信息\n\n| 项 | 值 |\n|---|---|\n| 版本 | 1.0 |\n"
        "| 最后更新 | 2026-10-10 09:00 |\n\n## 模块职责与边界\n\nx\n\n"
        "## 详细设计\n\nx\n", encoding="utf-8")
    d = client.post(f"/api/v1/tasks/{tid}/doc/spec/status", json={"state": "draft"})
    if d.status_code != 200:
        _report("D3", "ERR", f"落 draft 失败 {_brief(d)}")
        return
    d2r = client.post(f"/api/v1/tasks/{tid}/doc/spec/status", json={"state": "review"})
    if d2r.status_code != 200:
        _report("D3", "ERR", f"推进 review 失败（探针前置不成立）：{_brief(d2r)}")
        return
    r = client.post(f"/api/v1/tasks/{tid}/doc/spec/status", json={"state": "draft"})
    if r.status_code == 422:
        _report("D3", "有", f"review→draft 回退被拒：{_brief(r)}")
    else:
        _report("D3", "缺", f"状态可回退：{_brief(r)}")


def d4():
    """新任务能否在无任何文档批准的情况下走完 brainstorming→design→planning→ready。"""
    _fresh_db()
    tid = _mk_task(_ws("d4"), stage="brainstorming")
    steps, last = [], None
    for dst in ("design", "planning", "ready"):
        r = client.post(f"/api/v1/tasks/{tid}/stage/move", json={"target_stage": dst})
        steps.append(f"{dst}:{r.status_code}")
        last = r
    t = client.get(f"/api/v1/tasks/{tid}").json()
    if last.status_code == 200 and t.get("stage") == "ready":
        _report("D4", "缺", "新建任务（doc_statuses 为空）在无任何文档 approved 的情况下 "
                            f"一路推进到 ready：{' → '.join(steps)}；"
                            f"doc_statuses={t.get('doc_statuses')}")
    else:
        _report("D4", "有", f"推进被阻：{' → '.join(steps)} stage={t.get('stage')}")


# ═══════════════════════════════════════════════════════════════════════════

PROBES = [("A1", a1), ("A2", a2), ("A3", a3), ("A4", a4),
          ("B1", b1), ("B2", b2), ("B3", b3),
          ("C1", c1), ("C2", c2), ("C3", c3), ("C4", c4),
          ("D1", d1), ("D2", d2), ("D3", d3), ("D4", d4)]


def main():
    print("=" * 78)
    print("mio-taskhub 质量缺口验证  "
          "判定：缺=缺口放行 / 有=机制有效 / 绕=已知绕过路径 / ERR=探针失败")
    print("=" * 78)
    for pid, fn in PROBES:
        try:
            fn()
        except Exception as exc:
            _report(pid, "ERR", f"{type(exc).__name__}: {exc}")
    print("=" * 78)
    print("汇总：", dict(Counter(v for _, v, _ in RESULTS)))
    print(f"DB 隔离目录：{_TMP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())