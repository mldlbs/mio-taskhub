# -*- coding: utf-8 -*-
"""F-004 第 3 步：服务端 destructive 收口（destructive_guard）回归。

覆盖：
- 清单判定：8 个破坏性端点命中（含 /api/v1 前缀两种口径），agent 必需路径
  （claim / heartbeat / submit_result / write_document / add_discussion /
  PATCH 普通字段 / 阶段推进）不命中；
- REST 直连 destructive 无管理面凭据 → 403；带管理面凭据（降级头 /
  强隔离 token / Bearer 兼容）→ 放行；
- MIO_MCP_ALLOW_DESTRUCTIVE=1 只影响 MCP 客户端层，不改变服务端判定；
- 拒绝请求留审计事件（destructive_blocked）。

MCP 客户端层的行为断言（block / 403 透传）见 tests/test_mcp_server.py。
"""
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from mio_taskhub.destructive_guard import is_destructive, match_destructive
from mio_taskhub.main import app

# conftest 已给 TestClient 统一注入 X-TaskHub-Admin: 1（本机可信管理面口径）。
client = TestClient(app)


def _mk_task(tmp_path, **extra) -> str:
    r = client.post("/api/v1/tasks",
                    json={"title": "dg", "workspace": str(tmp_path), **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── 清单判定 ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("method,path,name", [
    ("DELETE", "/api/v1/tasks/abc123", "cancel_task"),
    ("DELETE", "/tasks/abc123", "cancel_task"),           # MCP 口径（无 /api/v1 前缀）
    ("DELETE", "/api/v1/tasks/abc123/", "cancel_task"),   # 尾斜杠
    ("POST", "/api/v1/tasks/abc123/doc/spec/status", "set_doc_status"),
    ("POST", "/tasks/abc123/doc/spec/status", "set_doc_status"),
    ("DELETE", "/api/v1/tasks/abc123/subtasks/s1", "delete_subtask"),
    ("POST", "/api/v1/ideas/gate/force-generate", "force_generate"),
    ("DELETE", "/api/v1/tasks/templates/tpl1", "delete_template"),
    ("DELETE", "/tasks/templates/tpl1", "delete_template"),   # MCP 口径
    ("DELETE", "/api/v1/scheduled-jobs/j1", "delete_scheduled_job"),
    ("DELETE", "/api/v1/alert-rules/r1", "delete_alert_rule"),
    ("POST", "/api/v1/update/apply", "update_apply"),
])
def test_destructive_endpoints_matched(method, path, name):
    assert match_destructive(method, path) == name
    assert is_destructive(method, path) is True


@pytest.mark.parametrize("method,path", [
    # agent 必需路径：一个都不能划进 destructive
    ("POST", "/api/v1/tasks"),                        # 建单（含初始执行边界字段）
    ("POST", "/api/v1/tasks/claim"),                  # 领取
    ("POST", "/api/v1/runs/r1/heartbeat"),            # 心跳
    ("POST", "/api/v1/runs/r1/result"),               # 提交结果
    ("PUT", "/api/v1/tasks/t1/doc"),                  # 写文档
    ("POST", "/api/v1/tasks/t1/discussions"),         # 回写讨论
    ("POST", "/api/v1/decisions"),                    # 登记决策
    ("POST", "/api/v1/agents/register"),
    ("POST", "/api/v1/tasks/t1/subtasks"),
    ("POST", "/api/v1/tasks/t1/stage"),
    ("POST", "/api/v1/tasks/t1/stage/move"),          # 受 stage 门控约束的正常写
    ("PATCH", "/api/v1/tasks/t1"),                    # 普通字段 PATCH（字段级校验在路由层）
    ("GET", "/api/v1/tasks/t1/doc"),
    ("GET", "/api/v1/board/summary"),
    ("DELETE", "/api/v1/tasks/templates/tpl1/versions"),  # 模板版本是只读列表
    ("POST", "/api/v1/scheduled-jobs"),                   # 建定时任务（普通写）
    ("POST", "/api/v1/scheduled-jobs/j1/trigger"),        # 手动触发（有 cron 引擎与 nightrun 白名单兜底）
    ("POST", "/api/v1/update/check"),                     # 更新检查/下载只读，不落终态
    ("POST", "/api/v1/update/download"),
    ("GET", "/api/v1/update/status"),
])
def test_non_destructive_paths_not_matched(method, path):
    assert is_destructive(method, path) is False


# ── REST 直连 destructive：403 / 放行 ────────────────────────────────────────

def test_rest_cancel_without_admin_403(anon_client, tmp_path, monkeypatch):
    """REST 直连取消任务，无任何管理面凭据 → 403（降级态码）。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    r = anon_client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.local_confirmation_required"


def test_rest_cancel_with_local_admin_header_ok(tmp_path, monkeypatch):
    """降级态（未配 MIO_TASKHUB_ADMIN_TOKEN）：环回 + X-TaskHub-Admin: 1 放行。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    r = client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "cancelled"


def test_rest_cancel_strong_isolation_token(tmp_path, monkeypatch):
    """强隔离：配 MIO_TASKHUB_ADMIN_TOKEN 后旧头失效，必须带 X-TaskHub-Admin-Token
    （或未启用 --auth 时的 Bearer 兼容）。"""
    monkeypatch.setenv("MIO_TASKHUB_ADMIN_TOKEN", "adm-tok-3")
    tid = _mk_task(tmp_path)
    r = client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.token_required"
    r2 = client.delete(f"/api/v1/tasks/{tid}",
                       headers={"X-TaskHub-Admin-Token": "adm-tok-3"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["state"] == "cancelled"


def test_rest_delete_subtask_gate(anon_client, tmp_path, monkeypatch):
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    st = client.post(f"/api/v1/tasks/{tid}/subtasks",
                     json={"title": "s1"}).json()
    r = anon_client.delete(f"/api/v1/tasks/{tid}/subtasks/{st['id']}")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.local_confirmation_required"
    r2 = client.delete(f"/api/v1/tasks/{tid}/subtasks/{st['id']}")
    assert r2.status_code in (200, 204), r2.text


def test_rest_force_generate_gate(anon_client, monkeypatch):
    """force-generate 绕过想法闸门：匿名 REST 直连一律 403（门控先于 handler）。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    r = anon_client.post("/api/v1/ideas/gate/force-generate", json={})
    assert r.status_code == 403, r.text


def test_rest_doc_status_gate(anon_client, tmp_path, monkeypatch):
    """set_doc_status 同时被服务端门控覆盖（与第 1 步端点级 require_admin 双保险）。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    r = anon_client.post(f"/api/v1/tasks/{tid}/doc/decision/status",
                         json={"state": "accepted"})
    assert r.status_code == 403, r.text


# ── operator 面删除端点（B16-6 残余 #2 收口）─────────────────────────────────

def _mk_template_row() -> str:
    from mio_taskhub.db import engine
    from mio_taskhub.models import TaskTemplate
    import uuid as _uuid
    tid = _uuid.uuid4().hex[:8]
    with Session(engine) as s:
        s.add(TaskTemplate(id=tid, title="tpl"))
        s.commit()
    return tid


def _mk_job_row() -> str:
    from mio_taskhub.db import engine
    from mio_taskhub.models import ScheduledJob
    import uuid as _uuid
    jid = _uuid.uuid4().hex[:8]
    with Session(engine) as s:
        s.add(ScheduledJob(id=jid, name="job", cron_expr="0 0 * * *"))
        s.commit()
    return jid


def test_rest_operator_deletes_gate(anon_client, tmp_path, monkeypatch):
    """模板 / 定时任务 / 告警规则的硬删除：匿名 403，管理面凭据放行。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)

    tpl = _mk_template_row()
    r = anon_client.delete(f"/api/v1/tasks/templates/{tpl}", params={"confirm": "true"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.local_confirmation_required"
    r2 = client.delete(f"/api/v1/tasks/templates/{tpl}", params={"confirm": "true"})
    assert r2.status_code == 200, r2.text

    jid = _mk_job_row()
    assert anon_client.delete(f"/api/v1/scheduled-jobs/{jid}").status_code == 403
    assert client.delete(f"/api/v1/scheduled-jobs/{jid}").status_code == 200

    cr = client.post("/api/v1/alert-rules",
                     json={"name": "r", "metric": "cpu", "condition": "gt", "threshold": 1})
    assert cr.status_code == 200, cr.text
    rid = cr.json()["id"]
    assert anon_client.delete(f"/api/v1/alert-rules/{rid}").status_code == 403
    assert client.delete(f"/api/v1/alert-rules/{rid}",
                         params={"confirm": "true"}).status_code == 200


def test_rest_update_apply_gate(anon_client, monkeypatch):
    """update/apply：门控先于 handler，匿名永远到不了更新器。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    r = anon_client.post("/api/v1/update/apply", params={"confirm": "true"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.local_confirmation_required"


def test_rest_update_apply_admin_pass(monkeypatch):
    """带管理面凭据（降级头）时放行——用注入桩避免真的替换二进制。"""
    class _Stub:
        def apply(self):
            return {"ok": True, "stub": True}

    from mio_taskhub.api import update as update_api
    monkeypatch.setattr(update_api, "_service_override", _Stub())
    r = client.post("/api/v1/update/apply")
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_allow_destructive_env_does_not_change_server(anon_client, tmp_path, monkeypatch):
    """MIO_MCP_ALLOW_DESTRUCTIVE=1 是 MCP 客户端层开关：服务端判定不随之变化。"""
    monkeypatch.setenv("MIO_MCP_ALLOW_DESTRUCTIVE", "1")
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    r = anon_client.delete(f"/api/v1/tasks/{tid}")
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.local_confirmation_required"


# ── agent 必需路径不受新门控影响 ─────────────────────────────────────────────

def test_agent_required_paths_unaffected(anon_client, tmp_path, monkeypatch):
    """claim / heartbeat / submit_result / write_document / add_discussion：
    匿名主体（只有 API 访问权）全程可用，不被 destructive 门控误伤。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    r = anon_client.post("/api/v1/tasks", json={
        "title": "agent path", "workspace": str(tmp_path), "stage": "ready"})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]

    claim = anon_client.post("/api/v1/tasks/claim", params={"agent": "dg-agent"})
    assert claim.status_code == 200, claim.text
    rid = claim.json()["id"]

    hb = anon_client.post(f"/api/v1/runs/{rid}/heartbeat",
                          params={"agent": "dg-agent"},
                          json={"progress": 30, "pid": 0})
    assert hb.status_code == 200, hb.text

    w = anon_client.put(f"/api/v1/tasks/{tid}/doc", params={"kind": "review"},
                        json={"content": "# review ok"})
    assert w.status_code == 200, w.text

    d = anon_client.post(f"/api/v1/tasks/{tid}/discussions",
                         json={"topic": "t", "agent": "dg-agent", "conclusions": "c"})
    assert d.status_code == 200, d.text

    sub = anon_client.post(f"/api/v1/runs/{rid}/result",
                           params={"agent": "dg-agent"},
                           json={"success": True, "result": "done"})
    assert sub.status_code == 200, sub.text


# ── 审计留痕 ─────────────────────────────────────────────────────────────────

def test_block_writes_audit_event(anon_client, tmp_path, monkeypatch):
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    assert anon_client.delete(f"/api/v1/tasks/{tid}").status_code == 403
    from mio_taskhub.db import engine
    from mio_taskhub.models import Event
    with __import__("sqlmodel").Session(engine) as s:
        ev = s.exec(select(Event).where(
            Event.type == "destructive_blocked",
            Event.entity_id == f"DELETE /api/v1/tasks/{tid}")).first()
    assert ev is not None
    payload = json.loads(ev.payload)
    assert payload["method"] == "DELETE" and payload["code"] == "admin.local_confirmation_required"
