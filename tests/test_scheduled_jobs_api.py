"""Tests for mio_taskhub.api.scheduled_jobs — CRUD + trigger + pause/resume."""
import pytest
from httpx import AsyncClient, ASGITransport
from mio_taskhub.main import app
from sqlmodel import Session
from mio_taskhub.db import engine
from mio_taskhub.models import ScheduledJob


@pytest.fixture(autouse=True)
def _clean_scheduled_jobs():
    """Remove seeded scheduled jobs so tests start with empty list."""
    from sqlmodel import select
    with Session(engine) as s:
        jobs = s.exec(select(ScheduledJob)).all()
        for j in jobs:
            s.delete(j)
        s.commit()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_list_jobs_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/scheduled-jobs")
    assert r.status_code == 200
    assert r.json() == []


@pytest.mark.anyio
async def test_create_and_get_job():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create
        r = await client.post("/api/v1/scheduled-jobs", json={
            "name": "daily sync",
            "cron_expr": "0 9 * * *",
            "action_type": "create_task",
            "action_config": {"title": "Sync data", "priority": 1},
        })
        assert r.status_code == 200
        job = r.json()
        assert job["name"] == "daily sync"
        assert job["cron_expr"] == "0 9 * * *"
        assert job["action_type"] == "create_task"
        assert job["enabled"] is True
        assert job["next_run_at"] is not None
        job_id = job["id"]

        # Get
        r2 = await client.get(f"/api/v1/scheduled-jobs/{job_id}")
        assert r2.status_code == 200
        assert r2.json()["name"] == "daily sync"


@pytest.mark.anyio
async def test_create_webhook_job():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/scheduled-jobs", json={
            "name": "webhook test",
            "cron_expr": "*/10 * * * *",
            "action_type": "webhook",
            "action_config": {
                "url": "https://example.com/hook",
                "method": "POST",
                "body": {"event": "trigger"},
            },
        })
        assert r.status_code == 200
        job = r.json()
        assert job["action_type"] == "webhook"
        assert job["action_config"]["url"] == "https://example.com/hook"


@pytest.mark.anyio
async def test_create_invalid_cron():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/scheduled-jobs", json={
            "name": "bad cron",
            "cron_expr": "invalid",
            "action_type": "create_task",
        })
        assert r.status_code == 422


@pytest.mark.anyio
async def test_create_webhook_no_url():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/scheduled-jobs", json={
            "name": "webhook no url",
            "cron_expr": "0 * * * *",
            "action_type": "webhook",
            "action_config": {},
        })
        assert r.status_code == 422


@pytest.mark.anyio
async def test_update_job():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/scheduled-jobs", json={
            "name": "original",
            "cron_expr": "0 9 * * *",
            "action_type": "create_task",
        })
        job_id = r.json()["id"]

        r2 = await client.patch(f"/api/v1/scheduled-jobs/{job_id}", json={
            "name": "updated",
            "cron_expr": "0 10 * * *",
        })
        assert r2.status_code == 200
        assert r2.json()["name"] == "updated"
        assert r2.json()["cron_expr"] == "0 10 * * *"


@pytest.mark.anyio
async def test_pause_resume_job():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/scheduled-jobs", json={
            "name": "pause test",
            "cron_expr": "0 * * * *",
            "action_type": "create_task",
        })
        job_id = r.json()["id"]

        # Pause
        r2 = await client.post(f"/api/v1/scheduled-jobs/{job_id}/pause")
        assert r2.status_code == 200
        assert r2.json()["enabled"] is False
        assert r2.json()["next_run_at"] is None

        # Resume
        r3 = await client.post(f"/api/v1/scheduled-jobs/{job_id}/resume")
        assert r3.status_code == 200
        assert r3.json()["enabled"] is True
        assert r3.json()["next_run_at"] is not None


@pytest.mark.anyio
async def test_delete_job():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/scheduled-jobs", json={
            "name": "delete me",
            "cron_expr": "0 * * * *",
            "action_type": "create_task",
        })
        job_id = r.json()["id"]

        r2 = await client.delete(f"/api/v1/scheduled-jobs/{job_id}")
        assert r2.status_code == 200
        assert r2.json()["deleted"] is True

        # Verify gone
        r3 = await client.get(f"/api/v1/scheduled-jobs/{job_id}")
        assert r3.status_code == 404


@pytest.mark.anyio
async def test_get_nonexistent():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/scheduled-jobs/nonexistent")
        assert r.status_code == 404


@pytest.mark.anyio
async def test_validate_cron_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/scheduled-jobs/validate-cron?expr=0+9+*+*+1-5")
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert len(data["next_runs"]) == 5


@pytest.mark.anyio
async def test_validate_cron_invalid_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/api/v1/scheduled-jobs/validate-cron?expr=bad")
        assert r.status_code == 422


@pytest.mark.anyio
async def test_list_executions():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/scheduled-jobs", json={
            "name": "exec test",
            "cron_expr": "0 * * * *",
            "action_type": "create_task",
        })
        job_id = r.json()["id"]

        r2 = await client.get(f"/api/v1/scheduled-jobs/{job_id}/executions")
        assert r2.status_code == 200
        assert isinstance(r2.json(), list)


@pytest.mark.anyio
async def test_create_empty_name():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/api/v1/scheduled-jobs", json={
            "name": "",
            "cron_expr": "0 * * * *",
            "action_type": "create_task",
        })
        # Should auto-fill name as "unnamed job"
        assert r.status_code == 200
        assert r.json()["name"] == "unnamed job"
