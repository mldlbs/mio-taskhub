"""P0 安全回归用例（2026-11-07 上线前审计修复）。

覆盖三条 P0：
- F-001：`/nightrun/config`、`/nightrun/spawn-now`、`/nightrun/stop` 需要管理员凭据，
        且 nightrun 命令必须命中可执行白名单（原实现 shell=True 直执任意命令）。
- F-002：文档读取不得越出 workspace（原实现接受任意绝对路径，可读明文 apiKey 配置）。
- F-003：文档写入必须是文档类后缀且在 workspace 内，workspace 不能是盘根/主目录/系统目录。
"""
import pathlib

import pytest
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.scheduling import night_runner as nr

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

def test_nightrun_config_requires_admin(cfg_path, monkeypatch):
    monkeypatch.delenv("MIO_TASKHUB_ADMIN_TOKEN", raising=False)
    app.state.auth_token = ""
    r = client.put("/api/v1/nightrun/config", json={"enabled": False, "agents": []})
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


def test_spawn_now_requires_admin():
    app.state.auth_token = ""
    r = client.post("/api/v1/nightrun/spawn-now", json={})
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
    r = client.patch(f"/api/v1/tasks/{tid}", json={"workspace": anchor})
    assert r.status_code == 400
    assert "根目录" in r.text


def test_patch_workspace_rejects_home(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    tid = _mk_task(ws)
    r = client.patch(f"/api/v1/tasks/{tid}", json={"workspace": str(pathlib.Path.home())})
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
