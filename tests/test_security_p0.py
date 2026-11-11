"""P0 安全回归用例（2026-11-07 上线前审计修复；F-004 凭据拆分后更新语义）。

覆盖：
- F-001：`/nightrun/config`、`/nightrun/spawn-now`、`/nightrun/stop` 需要**管理面**凭据，
        且 nightrun 命令必须命中可执行白名单（原实现 shell=True 直执任意命令）。
        F-004 后 `--auth` 的全局 token 只是 API 访问凭据，**不再**等价于管理员凭据；
        只配 `--auth` 时 `require_admin` 落到「环回 + X-TaskHub-Admin: 1」降级态。
- F-002：文档读取不得越出 workspace（原实现接受任意绝对路径，可读明文 apiKey 配置）。
- F-003：文档写入必须是文档类后缀且在 workspace 内，workspace 不能是盘根/主目录/系统目录。
- F-004：PATCH 的 ADMIN_TASK_FIELDS 需管理面凭据；`set_doc_status` 端点级需管理员；
        `MIO_TASKHUB_ADMIN_TOKEN` 与 `--auth` 两级凭据互不通用。

默认 `client` 由 conftest 统一注入 `X-TaskHub-Admin: 1`（TestClient 视为本机可信
管理面）；所有「无权限路径」断言一律走 `anon_client`，避免 403 断言变成假绿。
"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.scheduling import night_runner as nr

# TestClient 视为本机可信管理面：conftest 已统一注入 X-TaskHub-Admin: 1。
# 无权限路径另用 anon_client（不带任何管理员头）。
client = TestClient(app)
ADMIN = {"X-TaskHub-Admin": "1"}


@pytest.fixture()
def cfg_path(tmp_path, monkeypatch):
    """nightrun 配置文件隔离到 tmp，绝不碰 ~/.mio_taskhub/night_runner.json。"""
    p = tmp_path / "night_runner.json"
    monkeypatch.setattr(nr, "CONFIG_PATH", p)
    return p


def _mk_task(workspace: pathlib.Path) -> str:
    r = client.post("/api/v1/tasks", json={"title": "p0", "workspace": str(workspace)})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


# ── F-001 管理员凭据 ─────────────────────────────────────────────────────────

def test_nightrun_config_requires_admin(cfg_path, anon_client, monkeypatch):
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    app.state.auth_token = ""
    r = anon_client.put("/api/v1/nightrun/config", json={"enabled": False, "agents": []})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "admin.local_confirmation_required"


def test_nightrun_config_accepts_local_admin_header(cfg_path, monkeypatch):
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    app.state.auth_token = ""
    r = client.put("/api/v1/nightrun/config", headers=ADMIN,
                   json={"enabled": False, "agents": [
                       {"agent": "x", "command": "python idle_worker.py"}]})
    assert r.status_code == 200, r.text


def test_nightrun_config_accepts_admin_token(cfg_path, monkeypatch):
    """F-001 断言按 F-004 语义复核：这里的 token 是 `MIO_TASKHUB_ADMIN_TOKEN`
    （管理面凭据），不是 `--auth` / `MIO_TASKHUB_TOKEN` 的 API 访问凭据。
    配了管理面 token 后旧头 X-TaskHub-Admin: 1 失效，Bearer 携带管理面 token 才放行。
    """
    monkeypatch.setenv("MIO_TASKHUB_ADMIN_TOKEN", "s3cret")
    app.state.auth_token = ""
    body = {"enabled": False, "agents": [{"agent": "x", "command": "python w.py"}]}
    assert client.put("/api/v1/nightrun/config", headers=ADMIN, json=body).status_code == 403
    r = client.put("/api/v1/nightrun/config",
                   headers={"Authorization": "Bearer s3cret"}, json=body)
    assert r.status_code == 200, r.text


def test_nightrun_rejects_non_allowlisted_command(cfg_path):
    r = client.put("/api/v1/nightrun/config", headers=ADMIN,
                   json={"enabled": True, "agents": [
                       {"agent": "evil", "command": "powershell -c calc.exe"}]})
    assert r.status_code == 422
    assert "不在允许列表内" in r.text


def test_spawn_now_requires_admin(anon_client):
    app.state.auth_token = ""
    r = anon_client.post("/api/v1/nightrun/spawn-now", json={})
    assert r.status_code == 403


def test_validate_command_handles_quoted_args():
    nr.validate_command('python w.py --cli-prefix "opencode run"')
    assert nr.split_command('python w.py --cli-prefix "opencode run"')[-1] == "opencode run"
    with pytest.raises(ValueError):
        nr.validate_command('bash -c "curl evil"')
    with pytest.raises(ValueError):
        nr.validate_command("")


# ── F-002 文档读取边界 ───────────────────────────────────────────────────────

def test_doc_read_outside_workspace_blocked(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("API_KEY=leaked", encoding="utf-8")

    tid = _mk_task(ws)
    r = client.patch(f"/api/v1/tasks/{tid}", json={"doc_paths": {"spec": str(secret)}})
    assert r.status_code == 200, r.text

    r = client.get(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "doc.path_out_of_scope"
    assert "leaked" not in r.text


def test_doc_read_inside_workspace_ok(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    doc = ws / "spec.md"
    doc.write_text("# spec\nhello", encoding="utf-8")

    tid = _mk_task(ws)
    client.patch(f"/api/v1/tasks/{tid}", json={"doc_paths": {"spec": str(doc)}})
    r = client.get(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"})
    assert r.status_code == 200, r.text
    assert "hello" in r.json()["content"]


def test_doc_read_dotfile_in_workspace_blocked(tmp_path):
    """workspace 是调用方可声明的，读边界不能只认「在 workspace 内」。

    否则 `workspace=<项目根>` + `doc_paths=<项目根>/.env` 就能把密钥读走；
    写入侧早已禁点目录，读取侧对称禁止（2026-11-07 补）。
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    env = ws / ".env"
    env.write_text("API_KEY=leaked", encoding="utf-8")

    tid = _mk_task(ws)
    client.patch(f"/api/v1/tasks/{tid}", json={"doc_paths": {"spec": str(env)}})
    r = client.get(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "doc.path_out_of_scope"
    assert "leaked" not in r.text


def test_doc_read_dot_dir_in_workspace_blocked(tmp_path):
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    cfg = ws / ".git" / "config"
    cfg.write_text("[remote]\nurl=git@x:y.git", encoding="utf-8")

    tid = _mk_task(ws)
    client.patch(f"/api/v1/tasks/{tid}", json={"doc_paths": {"spec": str(cfg)}})
    r = client.get(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "doc.path_out_of_scope"


def test_doc_read_outside_roots_env_allows(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "shared"
    outside.mkdir()
    doc = outside / "spec.md"
    doc.write_text("shared doc", encoding="utf-8")

    tid = _mk_task(ws)
    client.patch(f"/api/v1/tasks/{tid}", json={"doc_paths": {"spec": str(doc)}})
    monkeypatch.setenv("MIO_TASKHUB_DOC_ROOTS", str(outside))
    r = client.get(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"})
    assert r.status_code == 200
    assert "shared doc" in r.json()["content"]


# ── F-003 文档写入边界 / workspace 校验 ──────────────────────────────────────

def test_file_and_raw_block_dotfile(tmp_path):
    """`/file` 与 `/raw` 与 `/doc` 同源约束：不得直接取 workspace 里的点文件。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".env").write_text("API_KEY=leaked", encoding="utf-8")

    tid = _mk_task(ws)
    for ep in ("file", "raw"):
        r = client.get(f"/api/v1/tasks/{tid}/{ep}", params={"path": ".env"})
        assert r.status_code == 400, (ep, r.status_code, r.text[:200])
        assert "点目录" in r.text
        assert "leaked" not in r.text


def test_doc_write_rejects_dot_dir(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tid = _mk_task(ws)
    r = client.put(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"},
                   json={"path": ".ssh/authorized_keys", "content": "ssh-rsa AAAA"})
    assert r.status_code == 400
    assert "点目录" in r.text


def test_doc_write_rejects_non_document_suffix(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tid = _mk_task(ws)
    r = client.put(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"},
                   json={"path": "run.exe", "content": "MZ"})
    assert r.status_code == 400
    assert "只允许写入文档类文件" in r.text


def test_doc_write_rejects_escape(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tid = _mk_task(ws)
    r = client.put(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"},
                   json={"path": "../escape.md", "content": "x"})
    assert r.status_code == 400


def test_doc_write_markdown_ok(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tid = _mk_task(ws)
    r = client.put(f"/api/v1/tasks/{tid}/doc", params={"kind": "spec"},
                   json={"path": "docs/spec.md", "content": "# ok"})
    assert r.status_code == 200, r.text
    assert (ws / "docs" / "spec.md").read_text(encoding="utf-8") == "# ok"


def test_patch_workspace_rejects_root(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tid = _mk_task(ws)
    anchor = str(pathlib.Path.cwd().anchor)  # Windows: "C:\\"，POSIX: "/"
    # 显式带管理员头：本用例测的是 path_guard 路径校验，不是 F-004 的主体校验。
    r = client.patch(f"/api/v1/tasks/{tid}", headers=ADMIN, json={"workspace": anchor})
    assert r.status_code == 400
    assert "根目录" in r.text


def test_patch_workspace_rejects_home(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tid = _mk_task(ws)
    r = client.patch(f"/api/v1/tasks/{tid}", headers=ADMIN,
                     json={"workspace": str(pathlib.Path.home())})
    assert r.status_code == 400
    assert "用户主目录" in r.text


def test_create_task_rejects_home_workspace():
    r = client.post("/api/v1/tasks", json={"title": "x", "workspace": str(pathlib.Path.home())})
    assert r.status_code == 400


# ── F-011 日志/指标纳入鉴权 ─────────────────────────────────────────────────

@pytest.fixture()
def auth_on():
    app.state.auth_token = "test-secret"
    yield
    app.state.auth_token = ""


def test_metrics_requires_auth_when_enabled(auth_on):
    assert client.get("/metrics").status_code == 401
    r = client.get("/metrics", headers={"Authorization": "Bearer test-secret"})
    assert r.status_code == 200


def test_logs_requires_auth_when_enabled(auth_on):
    assert client.get("/api/v1/logs").status_code == 401
    assert client.get("/logs").status_code in (401, 404)


# ── F-006 门控豁免字段需管理员确认 ──────────────────────────────────────────

def test_gate_exempt_on_create_requires_admin(anon_client):
    r = anon_client.post("/api/v1/tasks", json={"title": "exempt", "doc_gate_exempt": True})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.required_for_gate_fields"


def test_gate_exempt_on_create_with_admin_ok(tmp_path):
    r = client.post("/api/v1/tasks", headers=ADMIN,
                    json={"title": "exempt", "workspace": str(tmp_path),
                          "doc_gate_exempt": True})
    assert r.status_code in (200, 201), r.text
    assert r.json()["doc_gate_exempt"] is True


def test_gate_exempt_on_patch_requires_admin(tmp_path, anon_client):
    tid = _mk_task(tmp_path)
    r = anon_client.patch(f"/api/v1/tasks/{tid}", json={"doc_gate_exempt": True})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.required_for_gate_fields"

    r = client.patch(f"/api/v1/tasks/{tid}", headers=ADMIN,
                     json={"doc_gate_exempt": True})
    assert r.status_code == 200, r.text
    assert r.json()["doc_gate_exempt"] is True


def test_gate_reenable_needs_no_admin(tmp_path, anon_client):
    """把豁免改回 false 是收紧门控，不该要求管理员（用匿名主体验证，不是默认管理面 client）。"""
    tid = _mk_task(tmp_path)
    client.patch(f"/api/v1/tasks/{tid}", headers=ADMIN, json={"doc_gate_exempt": True})
    r = anon_client.patch(f"/api/v1/tasks/{tid}", json={"doc_gate_exempt": False})
    assert r.status_code == 200, r.text
    assert r.json()["doc_gate_exempt"] is False


def test_ordinary_patch_still_works_without_admin(tmp_path, anon_client):
    tid = _mk_task(tmp_path)
    r = anon_client.patch(f"/api/v1/tasks/{tid}", json={"title": "改个标题", "priority": 1})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "改个标题"


# ── F-004 两级凭据 / PATCH 字段级主体校验 / 文档状态管理员门 ──────────────────

_ADMIN_FIELD_CASES = [
    ("workspace", None),  # None → 运行时替换为 str(tmp_path)，避免触发 path_guard 的盘根/主目录规则
    ("files", ["a.py"]),
    ("deliverables", ["r.md"]),
    ("target_agent_type", "coding"),
    ("depends_on", []),
]


def _mk_doc_task(tmp_path, kind="decision"):
    """建任务并写一份文档，使 doc/{kind}/status 的合法转移可达 200。"""
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    tid = client.post("/api/v1/tasks",
                      json={"title": "doc", "workspace": str(ws)}).json()["id"]
    r = client.put(f"/api/v1/tasks/{tid}/doc", params={"kind": kind},
                   json={"content": "# d"})
    assert r.status_code == 200, r.text
    return tid


@pytest.mark.parametrize("field, value", _ADMIN_FIELD_CASES)
def test_patch_admin_field_without_admin_header_403(field, value, anon_client, tmp_path, monkeypatch):
    """无管理面凭据 PATCH ADMIN_TASK_FIELDS → 403 admin.required_for_task_fields。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    body = {field: str(tmp_path) if field == "workspace" else value}
    r = anon_client.patch(f"/api/v1/tasks/{tid}", json=body)
    assert r.status_code == 403, r.text
    d = r.json()["detail"]
    assert d["code"] == "admin.required_for_task_fields", d
    assert field in d["fields"], d


def test_patch_admin_fields_with_admin_header_200(tmp_path, monkeypatch):
    """带管理面确认头 → 五个字段全部可写。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    body = {"workspace": str(tmp_path), "files": ["a.py"], "deliverables": ["r.md"],
            "target_agent_type": "coding", "depends_on": []}
    r = client.patch(f"/api/v1/tasks/{tid}", headers=ADMIN, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["workspace"] == str(tmp_path)
    assert d["files"] == ["a.py"] and d["deliverables"] == ["r.md"]
    assert d["target_agent_type"] == "coding" and d["depends_on"] == []


def test_patch_ordinary_fields_without_admin_header_200(anon_client, tmp_path, monkeypatch):
    """普通字段一律不拦（含 gate_tier 之外的全部可编辑普通字段）。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    body = {
        "title": "t2", "description": "d", "priority": 1, "est_duration_min": 30,
        "max_retries": 2, "acceptance_criteria": "AC",
        "due_at": "2026-12-31T23:59:59+00:00", "labels": ["l"], "project": "p",
        "fallback_after": 60, "grab_mode": True,
    }
    r = anon_client.patch(f"/api/v1/tasks/{tid}", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "t2" and r.json()["project"] == "p"


def test_gate_exempt_back_to_false_without_admin_header_200(anon_client, tmp_path, monkeypatch):
    """门控豁免「改回 false」是收紧，匿名主体也放行。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_task(tmp_path)
    assert client.patch(f"/api/v1/tasks/{tid}", headers=ADMIN,
                        json={"doc_gate_exempt": True}).status_code == 200
    r = anon_client.patch(f"/api/v1/tasks/{tid}", json={"diff_gate_exempt": False})
    assert r.status_code == 200, r.text
    assert r.json()["diff_gate_exempt"] is False


def test_create_task_with_initial_workspace_still_allowed(anon_client, tmp_path, monkeypatch):
    """POST 不挂字段级管理面校验：agent 仍需能建单时指定初始 workspace / depends_on。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    r = anon_client.post("/api/v1/tasks",
                         json={"title": "init ws", "workspace": str(tmp_path),
                               "files": ["a.py"], "depends_on": []})
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]
    detail = anon_client.get(f"/api/v1/tasks/{tid}").json()
    assert detail["workspace"] == str(tmp_path)
    assert detail["files"] == ["a.py"]


def test_doc_status_without_admin_header_403(anon_client, tmp_path, monkeypatch):
    """set_doc_status 端点级 require_admin：匿名主体一律 403（依赖先于 handler 执行）。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_doc_task(tmp_path)
    r = anon_client.post(f"/api/v1/tasks/{tid}/doc/decision/status",
                         json={"state": "accepted"})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.local_confirmation_required"


def test_doc_status_with_admin_header_200(tmp_path, monkeypatch):
    """带管理面确认头 → 合法转移可达 200。"""
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    tid = _mk_doc_task(tmp_path)
    r = client.post(f"/api/v1/tasks/{tid}/doc/decision/status",
                    headers=ADMIN, json={"state": "accepted"})
    assert r.status_code == 200, r.text
    assert r.json()["status"]["state"] == "accepted"


def test_admin_token_env_disables_legacy_header(tmp_path, monkeypatch):
    """配了 MIO_TASKHUB_ADMIN_TOKEN 后，只带 X-TaskHub-Admin: 1 不再放行。"""
    monkeypatch.setenv("MIO_TASKHUB_ADMIN_TOKEN", "admin-only-token")
    tid = _mk_task(tmp_path)
    r = client.patch(f"/api/v1/tasks/{tid}", json={"workspace": str(tmp_path)})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.required_for_task_fields"
    r2 = client.post(f"/api/v1/tasks/{tid}/doc/spec/status", json={"state": "review"})
    assert r2.status_code == 403, r2.text
    assert r2.json()["detail"]["code"] == "admin.token_required"


def test_admin_token_env_accepts_admin_token_header(tmp_path, monkeypatch):
    """配了管理面 token 后，X-TaskHub-Admin-Token 头放行。"""
    monkeypatch.setenv("MIO_TASKHUB_ADMIN_TOKEN", "admin-only-token")
    tid = _mk_task(tmp_path)
    r = client.patch(f"/api/v1/tasks/{tid}",
                     headers={"X-TaskHub-Admin-Token": "admin-only-token"},
                     json={"workspace": str(tmp_path)})
    assert r.status_code == 200, r.text
    assert r.json()["workspace"] == str(tmp_path)


def test_admin_token_env_rejects_wrong_token(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_TASKHUB_ADMIN_TOKEN", "admin-only-token")
    tid = _mk_task(tmp_path)
    r = client.patch(f"/api/v1/tasks/{tid}",
                     headers={"X-TaskHub-Admin-Token": "wrong"},
                     json={"workspace": str(tmp_path)})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "admin.required_for_task_fields"


def test_admin_tokens_excludes_auth_token(monkeypatch):
    """直接验证拆分：admin_tokens() 不再并入 app.state.auth_token。"""
    from types import SimpleNamespace
    from mio_taskhub.admin_guard import admin_tokens
    fake = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(auth_token="api-token")))
    monkeypatch.setenv("MIO_TASKHUB_ADMIN_TOKEN", "admin-secret")
    assert admin_tokens(fake) == ["admin-secret"]
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    assert admin_tokens(fake) == []


def test_auth_plus_admin_token_both_headers(tmp_path, monkeypatch):
    """强隔离（两个都配）：Bearer 是 API 凭据、X-TaskHub-Admin-Token 是管理面凭据。

    服务端必须并列收集两个头，否则 Bearer 会把管理面凭据挤掉，管理员操作全 403。
    """
    monkeypatch.setenv("MIO_TASKHUB_ADMIN_TOKEN", "admin-only-token")
    old = app.state.auth_token
    app.state.auth_token = "api-access-token"
    try:
        created = client.post("/api/v1/tasks",
                              headers={"Authorization": "Bearer api-access-token"},
                              json={"title": "both"})
        assert created.status_code == 200, created.text
        tid = created.json()["id"]
        both = {"Authorization": "Bearer api-access-token",
                "X-TaskHub-Admin-Token": "admin-only-token"}
        r = client.patch(f"/api/v1/tasks/{tid}", headers=both,
                         json={"workspace": str(tmp_path)})
        assert r.status_code == 200, r.text
        # 只带 API 凭据、不带管理面凭据 → 403
        r2 = client.patch(f"/api/v1/tasks/{tid}",
                          headers={"Authorization": "Bearer api-access-token"},
                          json={"workspace": str(tmp_path)})
        assert r2.status_code == 403, r2.text
    finally:
        app.state.auth_token = old


def test_auth_token_is_not_admin_credential(cfg_path, anon_client, monkeypatch):
    """F-001 新语义：只配 --auth、未配管理面 token 时应落到降级态。

    `--auth` 的全局 token 不再是管理员凭据：仅有 Bearer 的匿名主体仍被
    require_admin 拦在「环回 + X-TaskHub-Admin: 1」这道速度闸外。
    """
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    old = app.state.auth_token
    app.state.auth_token = "api-access-token"
    try:
        body = {"enabled": False, "agents": []}
        r = anon_client.put("/api/v1/nightrun/config",
                            headers={"Authorization": "Bearer api-access-token"}, json=body)
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "admin.local_confirmation_required"
        # 同一 API 凭据 + 管理面确认头（默认 client 自带）→ 放行
        r2 = client.put("/api/v1/nightrun/config",
                        headers={"Authorization": "Bearer api-access-token"}, json=body)
        assert r2.status_code == 200, r2.text
    finally:
        app.state.auth_token = old
