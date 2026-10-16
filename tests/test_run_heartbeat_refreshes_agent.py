# -*- coding: utf-8 -*-
"""P1-OBS 执行链 P4：run 心跳应同步刷新 agent 心跳并置 ONLINE。"""
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlmodel import Session

from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, Agent, AgentStatus

client = TestClient(app)


def test_run_heartbeat_refreshes_agent():
    # 先注册 agent（run 心跳刷新对象）
    client.post("/api/v1/agents/register", json={"name": "hb-refresh-agent", "agent_type": "cli"})
    # 建任务 + 领取
    r = client.post("/api/v1/tasks", json={"title": "hb-refresh", "stage": "ready"})
    tid = r.json()["id"]
    c = client.post("/api/v1/tasks/claim", params={"agent": "hb-refresh-agent"})
    assert c.status_code == 200
    rid = c.json()["id"]
    # 人为把 agent 置 OFFLINE + 心跳陈旧
    with Session(engine) as s:
        a = s.get(Agent, "hb-refresh-agent")
        a.status = AgentStatus.OFFLINE
        a.last_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)
        s.add(a); s.commit()
    # run 心跳
    hb = client.post(f"/api/v1/runs/{rid}/heartbeat",
                     params={"agent": "hb-refresh-agent"}, json={"progress": 30})
    assert hb.status_code == 200, hb.text
    with Session(engine) as s:
        a = s.get(Agent, "hb-refresh-agent")
        assert a.status == AgentStatus.ONLINE, "run 心跳应把 agent 置 ONLINE"
        hb_t = a.last_heartbeat
        if hb_t.tzinfo is None:
            hb_t = hb_t.replace(tzinfo=timezone.utc)
        assert (datetime.now(timezone.utc) - hb_t).total_seconds() < 60, "agent 心跳应被刷新"
