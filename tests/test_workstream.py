# -*- coding: utf-8 -*-
"""工作流面板接口：part-of 聚合、依赖层、阻塞统计。"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app

client = TestClient(app)


def _mk(title, labels=None, depends_on=None, stage="ready", est=30):
    body = {"title": title, "stage": stage, "est_duration_min": est}
    if labels:
        body["labels"] = labels
    if depends_on:
        body["depends_on"] = depends_on
    r = client.post("/api/v1/tasks", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_workstream_groups_part_of_and_layers():
    root = _mk("root")
    a = _mk("A", labels=[f"part-of:{root}"], est=10)
    b = _mk("B", labels=[f"part-of:{root}"], depends_on=[a], est=20)
    c = _mk("C", labels=[f"part-of:{root}"], depends_on=[b], stage="implementing", est=40)

    r = client.get(f"/api/v1/tasks/{root}/workstream")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["owner"] == root
    assert data["stats"]["total"] == 3
    assert data["stats"]["estimate_min"] == 70
    assert data["stats"]["blocked"] == 2
    assert data["stats"]["critical_path"] == 3
    assert len(data["layers"]) == 3
    assert data["layers"][0] == [a]
    assert data["layers"][1] == [b]
    assert data["layers"][2] == [c]
    edge = {"from": a, "to": b}
    assert edge in data["edges"]

    member = client.get(f"/api/v1/tasks/{c}/workstream").json()
    assert member["owner"] == root
    assert member["is_member"] is True
    assert member["stats"]["total"] == 3


def test_workstream_empty_group():
    t = _mk("solo")
    data = client.get(f"/api/v1/tasks/{t}/workstream").json()
    assert data["stats"]["total"] == 0
    assert data["nodes"] == []
    assert data["layers"] == []


def test_list_tasks_includes_labels():
    t = _mk("labeled", labels=["part-of:root-x"])
    rows = client.get("/api/v1/tasks").json()
    row = next(r for r in rows if r["id"] == t)
    assert "part-of:root-x" in row["labels"]
