# -*- coding: utf-8 -*-
"""FR-07（任务 c8316737）：任务级只读 diff 路由 GET /api/v1/tasks/{id}/diff。

验收标准覆盖：404 / 403 / 405 / 200 空集 / FR-n 映射（UNMAPPED）。
只读断言：该路由不触发状态机、不写质量分、不落事件。
"""
import os

from fastapi.testclient import TestClient
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Run, RunState, Task, TaskState

client = TestClient(app)


def _mk_task(task_id: str, workspace: str = "", doc_paths=None) -> Task:
    with Session(engine) as s:
        t = Task(id=task_id, title=f"task-{task_id}", state=TaskState.RUNNING,
                 workspace=workspace, doc_paths=doc_paths or {})
        s.add(t)
        s.commit()
        s.refresh(t)
        return t


def _mk_run_with_diff(run_id: str, task_id: str, paths, ins=5, dele=2):
    with Session(engine) as s:
        r = Run(id=run_id, task_id=task_id, agent_name="tester",
                state=RunState.FINISHED, exit_code=0,
                diff_summary={
                    "before": None,
                    "after": {"files": len(paths), "insertions": ins,
                              "deletions": dele, "paths": list(paths)},
                    "changed": True, "reason": "has_changes",
                })
        s.add(r)
        s.commit()


# FR-07 TC1：任务不存在 → 404
def test_diff_404_when_task_missing():
    r = client.get("/api/v1/tasks/nonexist/diff")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "task.not_found"


# FR-07 TC2：run 级证据 + requirement 文档 FR 映射
def test_diff_aggregates_run_evidence_with_fr_mapping(tmp_path):
    ws = str(tmp_path)
    os.makedirs(os.path.join(ws, "src"), exist_ok=True)
    with open(os.path.join(ws, "src", "foo.py"), "w", encoding="utf-8") as f:
        f.write("print('hello')\n")
    os.makedirs(os.path.join(ws, "docs"), exist_ok=True)
    with open(os.path.join(ws, "docs", "req.md"), "w", encoding="utf-8") as f:
        f.write("# 需求\n\n### FR-01\n- 改动文件 src/foo.py，见下文。\n\n### FR-02\n- docs/req.md 更新。\n")

    _mk_task("t7a00001", workspace=ws, doc_paths={"requirement": "docs/req.md"})
    _mk_run_with_diff("r7a00001", "t7a00001", ["src/foo.py", "docs/bar.md"])

    r = client.get("/api/v1/tasks/t7a00001/diff")
    assert r.status_code == 200
    data = r.json()
    assert data["reason"] == "ok"
    assert data["stat"]["files"] >= 2
    assert data["stat"]["runs"] == 1
    by_path = {c["path"]: c for c in data["changed_files"]}
    assert by_path["src/foo.py"]["fr"] == "FR-01"
    assert by_path["src/foo.py"]["source"] == "run:r7a00001"
    assert by_path["src/foo.py"]["size"] is not None
    assert by_path["docs/bar.md"]["fr"] == "UNMAPPED"


# FR-07 TC3：无任何变更证据 → 200 空集（workspace 为空目录，非 git 仓）
def test_diff_200_empty_when_no_changes(tmp_path):
    _mk_task("t7a00002", workspace=str(tmp_path))
    r = client.get("/api/v1/tasks/t7a00002/diff")
    assert r.status_code == 200
    data = r.json()
    assert data["reason"] == "no_changes"
    assert data["changed_files"] == []
    assert data["stat"]["files"] == 0


# FR-07 TC3b：workspace 为空字符串（数据常态）→ 200 + no_workspace，不报错
def test_diff_200_no_workspace():
    _mk_task("t7a00003", workspace="")
    r = client.get("/api/v1/tasks/t7a00003/diff")
    assert r.status_code == 200
    assert r.json()["reason"] == "no_workspace"


# FR-07 TC4：workspace 目录不存在 → 403
def test_diff_403_when_workspace_missing():
    _mk_task("t7a00004", workspace="Z:/definitely/not/exist/fr07")
    r = client.get("/api/v1/tasks/t7a00004/diff")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "task.workspace_unreadable"


# FR-07 TC5：写方法 → 405
def test_diff_405_on_write_methods(tmp_path):
    _mk_task("t7a00005", workspace=str(tmp_path))
    assert client.post("/api/v1/tasks/t7a00005/diff", json={}).status_code == 405
    assert client.put("/api/v1/tasks/t7a00005/diff", json={}).status_code == 405
    assert client.delete("/api/v1/tasks/t7a00005/diff").status_code == 405


# FR-03 E3 假门埋点：任何出口（404/200）都写一行本地 jsonl；fr_ref 透传
def test_diff_e3_view_log_written(tmp_path, monkeypatch):
    import json as _json

    log_file = tmp_path / "e3.jsonl"
    monkeypatch.setenv("MIO_E3_VIEW_LOG", str(log_file))

    # 404 出口
    client.get("/api/v1/tasks/nonexist-e3/diff")
    # 200 + no_workspace 出口
    _mk_task("t7a00007", workspace="")
    client.get("/api/v1/tasks/t7a00007/diff")
    # 200 + ok 出口（带 fr_ref 查询参数）
    ws = str(tmp_path / "ws")
    os.makedirs(os.path.join(ws, "src"), exist_ok=True)
    with open(os.path.join(ws, "src", "b.py"), "w", encoding="utf-8") as f:
        f.write("y=2\n")
    _mk_task("t7a00008", workspace=ws)
    _mk_run_with_diff("r7a00008", "t7a00008", ["src/b.py"])
    client.get("/api/v1/tasks/t7a00008/diff?fr_ref=FR-07")

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    recs = [_json.loads(l) for l in lines]
    by_task = {r["task_id"]: r for r in recs}
    assert by_task["nonexist-e3"]["http_status"] == 404
    assert by_task["nonexist-e3"]["action"] == "view"
    assert by_task["t7a00007"]["http_status"] == 200
    assert by_task["t7a00007"]["files"] == 0
    assert by_task["t7a00008"]["http_status"] == 200
    assert by_task["t7a00008"]["files"] >= 1
    assert by_task["t7a00008"]["fr_ref"] == "FR-07"
    # 每条记录必备字段齐全
    for r in recs:
        assert set(r) >= {"ts", "task_id", "action", "http_status", "fr_ref", "files"}


# FR-07 只读断言：调用后不落事件、run/task 字段不变
def test_diff_is_read_only(tmp_path):
    ws = str(tmp_path)
    os.makedirs(os.path.join(ws, "src"), exist_ok=True)
    with open(os.path.join(ws, "src", "a.py"), "w") as f:
        f.write("x=1\n")
    _mk_task("t7a00006", workspace=ws)
    _mk_run_with_diff("r7a00006", "t7a00006", ["src/a.py"])
    with Session(engine) as s:
        run_before = s.get(Run, "r7a00006")
        ds_before = run_before.diff_summary
        task_before = s.get(Task, "t7a00006")
        state_before = task_before.state
    r = client.get("/api/v1/tasks/t7a00006/diff")
    assert r.status_code == 200
    with Session(engine) as s:
        run_after = s.get(Run, "r7a00006")
        assert run_after.diff_summary == ds_before
        task_after = s.get(Task, "t7a00006")
        assert task_after.state == state_before
