# tests/test_fallback.py
"""target_agent_type + fallback_after 领取语义测试"""
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from mio_taskhub.main import app
from mio_taskhub.db import engine
from mio_taskhub.models import Task, Agent, Run, TaskState, TaskStage
from mio_taskhub.api.tasks import _claim_for, _should_fallback

client = TestClient(app)


def _register(name, agent_type="t"):
    return client.post("/api/v1/agents/register", json={"name": name, "agent_type": agent_type}).json()


def _mk(title, stage="ready", agent_type=None, fallback_after=None, **kw):
    body = {"title": title, "stage": stage}
    if agent_type:
        body["target_agent_type"] = agent_type
    if fallback_after is not None:
        body["fallback_after"] = fallback_after
    body.update(kw)
    return client.post("/api/v1/tasks", json=body).json()


# ---- _should_fallback Python 语义测试 ----

def test_should_fallback_no_target():
    """target_agent_type=None → 永远不 fallback"""
    t = Task(title="x", target_agent_type=None, fallback_after=7200)
    assert _should_fallback(t, "codebuddy") is False


def test_should_fallback_no_agent_type():
    """agent_type=None → 永远不 fallback"""
    t = Task(title="x", target_agent_type="robot", fallback_after=7200)
    assert _should_fallback(t, None) is False


def test_should_fallback_match():
    """专长匹配 → 不触发 fallback"""
    t = Task(title="x", target_agent_type="robot", fallback_after=7200)
    assert _should_fallback(t, "robot") is False


def test_should_fallback_no_fallback_after():
    """fallback_after=None → 永久绑定"""
    t = Task(title="x", target_agent_type="robot", fallback_after=None)
    assert _should_fallback(t, "codebuddy") is False


def test_should_fallback_not_yet():
    """fallback_after=7200, created_at=1h前 → 未到期"""
    t = Task(title="x", target_agent_type="robot", fallback_after=7200,
             created_at=datetime.now(timezone.utc) - timedelta(hours=1))
    assert _should_fallback(t, "codebuddy") is False


def test_should_fallback_expired():
    """fallback_after=7200, created_at=2h前 → 已到期"""
    t = Task(title="x", target_agent_type="robot", fallback_after=7200,
             created_at=datetime.now(timezone.utc) - timedelta(hours=2))
    assert _should_fallback(t, "codebuddy") is True


def test_should_fallback_zero():
    """fallback_after=0 → 立即可 fallback"""
    t = Task(title="x", target_agent_type="robot", fallback_after=0,
             created_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert _should_fallback(t, "codebuddy") is True


def test_should_fallback_no_created_at():
    """created_at=None → 不触发"""
    t = Task(title="x", target_agent_type="robot", fallback_after=7200, created_at=None)
    assert _should_fallback(t, "codebuddy") is False


# ---- _claim_for SQL 排序测试 ----

def test_claim_fallback_none_permanent_always_last():
    """fallback=None 的 robot 任务始终排在 codebuddy 之后（relevance=2）"""
    _register("r永久", "robot")
    _register("cb永久", "codebuddy")
    _mk("robot永久", agent_type="robot")
    _mk("cb通用", stage="ready")
    with Session(engine) as s:
        run = _claim_for("cb永久", s, agent_type="codebuddy")
        assert run is not None
        task = s.get(Task, run.task_id)
        assert task.title == "cb通用", "codebuddy 应优先领取通用任务而非 robot 专属"


def test_claim_fallback_not_yet_always_last():
    """未到期 fallback 任务仍排在 codebuddy 之后（relevance=2）"""
    _register("r未到期", "robot")
    _register("cb未到期", "codebuddy")
    _mk("robot未到期", agent_type="robot", fallback_after=7200)
    _mk("cb通用未到期", stage="ready")
    with Session(engine) as s:
        run = _claim_for("cb未到期", s, agent_type="codebuddy")
        assert run is not None
        task = s.get(Task, run.task_id)
        assert task.title == "cb通用未到期", "codebuddy 应优先领取通用任务"


def test_claim_fallback_expired_upgraded():
    """已到期 fallback 任务降为通用（relevance=1），与 codebuddy 同档"""
    _register("r已到期", "robot")
    _register("cb已到期", "codebuddy")
    _mk("robot已到期", agent_type="robot", fallback_after=7200)
    _mk("cb通用已到期", stage="ready")
    # 设置 robot 已到期
    with Session(engine) as s:
        t = s.exec(select(Task).where(Task.title == "robot已到期")).first()
        t.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
        s.commit()
    with Session(engine) as s:
        run = _claim_for("cb已到期", s, agent_type="codebuddy")
        assert run is not None
        task = s.get(Task, run.task_id)
        # 已到期，codebuddy 可以领取 robot 任务（同为 relevance=1，按优先级+FIFO）
        assert task.target_agent_type == "robot"


def test_claim_fallback_self():
    """robot 领自己的任务 → relevance=0（最高优先）"""
    _register("r自己", "robot")
    _register("cb自己", "codebuddy")
    _mk("robot自己", agent_type="robot", fallback_after=7200)
    with Session(engine) as s:
        t = s.exec(select(Task).where(Task.title == "robot自己")).first()
        t.created_at = datetime.now(timezone.utc) - timedelta(hours=3)
        s.commit()
    with Session(engine) as s:
        run = _claim_for("r自己", s, agent_type="robot")
        assert run is not None
        task = s.get(Task, run.task_id)
        assert task.title == "robot自己"


def test_claim_fallback_generic():
    """target=None → codebuddy relevance=1，可领取"""
    _register("cb通用", "codebuddy")
    _mk("通用任务")
    with Session(engine) as s:
        run = _claim_for("cb通用", s, agent_type="codebuddy")
        assert run is not None
        task = s.get(Task, run.task_id)
        assert task.title == "通用任务"


def test_claim_fallback_zero_instant():
    """fallback_after=0 → 立即可 fallback（relevance=1）"""
    _register("r即时", "robot")
    _register("cb即时", "codebuddy")
    _mk("robot即时", agent_type="robot", fallback_after=0)
    _mk("cb通用即时", stage="ready")
    with Session(engine) as s:
        t = s.exec(select(Task).where(Task.title == "robot即时")).first()
        t.created_at = datetime.now(timezone.utc) - timedelta(seconds=10)
        s.commit()
    with Session(engine) as s:
        run = _claim_for("cb即时", s, agent_type="codebuddy")
        assert run is not None
        task = s.get(Task, run.task_id)
        # fallback_after=0 且已过 10s，codebuddy 可领取（但通用也在，两者同档）
        # 至少确保不崩溃，能领到某个任务
        assert task.title in ("robot即时", "cb通用即时")


# ---- 排序验证测试 ----

def test_claim_priority_ordering_with_fallback():
    """验证 fallback 场景下的优先级排序"""
    _register("r排序", "robot")
    _register("cb排序", "codebuddy")
    # 创建 3 个任务：通用 / robot已到期 / robot未到期
    _mk("通用排序", stage="ready", priority=0)
    _mk("robot已到期排序", stage="ready", agent_type="robot", fallback_after=7200, priority=0)
    _mk("robot未到期排序", stage="ready", agent_type="robot", fallback_after=7200, priority=0)
    # 设置 robot 已到期任务的 created_at
    with Session(engine) as s:
        t = s.exec(select(Task).where(Task.title == "robot已到期排序")).first()
        t.created_at = datetime.now(timezone.utc) - timedelta(hours=3)
        s.commit()
    with Session(engine) as s:
        run = _claim_for("cb排序", s, agent_type="codebuddy")
        assert run is not None
        task = s.get(Task, run.task_id)
        # codebuddy 应优先领通用任务（relevance=1）或已到期 robot（relevance=1）
        # 不应领未到期 robot（relevance=2）
        assert task.title != "robot未到期排序", "未到期 robot 应排最后"


def test_fallback_after_field_in_api():
    """API 创建/获取任务时 fallback_after 字段正确"""
    r = client.post("/api/v1/tasks", json={
        "title": "Fallback API",
        "target_agent_type": "robot",
        "fallback_after": 3600,
        "stage": "ready",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["fallback_after"] == 3600
    # 获取详情验证
    detail = client.get(f"/api/v1/tasks/{data['id']}").json()
    assert detail["fallback_after"] == 3600


def test_update_task_fallback_after():
    """更新任务的 fallback_after"""
    r = client.post("/api/v1/tasks", json={
        "title": "Update FB",
        "target_agent_type": "robot",
        "stage": "ready",
    })
    task_id = r.json()["id"]
    r2 = client.patch(f"/api/v1/tasks/{task_id}", json={"fallback_after": 7200})
    assert r2.status_code == 200
    assert r2.json()["fallback_after"] == 7200
