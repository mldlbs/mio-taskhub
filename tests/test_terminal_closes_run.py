# -*- coding: utf-8 -*-
"""T4.3/P1-OBS 执行链：任务移到终态时应关闭其活跃 run。"""
from fastapi.testclient import TestClient
from sqlmodel import Session

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskState, Run, RunState

client = TestClient(app)


def _claim_one(title):
    r = client.post("/api/v1/tasks", json={"title": title, "stage": "ready"})
    tid = r.json()["id"]
    c = client.post("/api/v1/tasks/claim", params={"agent": "move-probe"})
    assert c.status_code == 200, c.text
    return tid, c.json()["id"]


def test_move_to_done_closes_active_run():
    tid, rid = _claim_one("move-done")
    # P1-2：进 done 前需真实评审证据（他人 approve 记录）
    rv = client.post(f"/api/v1/tasks/{tid}/reviews", json={
        "decision": "approve", "reviewer": "human-reviewer",
        "summary": "已核对变更范围与测试结果，无阻断问题",
    })
    assert rv.status_code == 200, rv.text
    # 移到 done（合法路径：ready→...→done）
    r = client.post(f"/api/v1/tasks/{tid}/stage/move",
                    json={"target_stage": "done", "review_result": "approved"})
    assert r.status_code == 200, r.text
    with Session(engine) as s:
        run = s.get(Run, rid)
        assert run.state == RunState.FINISHED, "任务到 done 后活跃 run 应关闭"


def test_move_to_cancelled_closes_active_run():
    tid, rid = _claim_one("move-cancel")
    r = client.post(f"/api/v1/tasks/{tid}/stage/move", json={"target_stage": "cancelled"})
    assert r.status_code == 200, r.text
    with Session(engine) as s:
        run = s.get(Run, rid)
        assert run.state == RunState.FINISHED, "任务到 cancelled 后活跃 run 应关闭"
