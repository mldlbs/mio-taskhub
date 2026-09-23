import os
import pytest

from mio_taskhub.update.apply import (
    execute_replace, verify_staging, _rename_retry, ReplaceResult, REQUIRED_ENTRIES,
)


def _mk_install(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "mio-taskhub.exe").write_text("old-exe", encoding="utf-8")
    (root / "_internal" / "web" / "dist").mkdir(parents=True)
    (root / "_internal" / "web" / "dist" / "index.html").write_text("old", encoding="utf-8")


def _mk_staging(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "mio-taskhub.exe").write_text("new-exe", encoding="utf-8")
    (root / "_internal" / "web" / "dist").mkdir(parents=True)
    (root / "_internal" / "web" / "dist" / "index.html").write_text("new", encoding="utf-8")


def test_verify_staging_ok(tmp_path):
    s = tmp_path / "staging"
    _mk_staging(s)
    ok, msg = verify_staging(s)
    assert ok, msg


def test_verify_staging_missing_entry(tmp_path):
    s = tmp_path / "staging"
    s.mkdir()
    (s / "mio-taskhub.exe").write_text("x", encoding="utf-8")
    ok, msg = verify_staging(s)
    assert not ok and "web" in msg


def test_execute_replace_happy(tmp_path):
    install = tmp_path / "app"
    _mk_install(install)
    staging = tmp_path / "app.staging-0.4.0"
    _mk_staging(staging)
    backup = tmp_path / "app.bak-0.3.0"
    res = execute_replace(install, staging, backup)
    assert res.ok is True
    assert (install / "mio-taskhub.exe").read_text(encoding="utf-8") == "new-exe"
    assert backup.exists() and (backup / "mio-taskhub.exe").read_text(encoding="utf-8") == "old-exe"
    assert not staging.exists()


def test_execute_replace_replaces_only_same_version_backup(tmp_path):
    install = tmp_path / "app"
    _mk_install(install)
    backup = tmp_path / "app.bak-0.3.0"
    backup.mkdir()
    (backup / "junk.txt").write_text("stale", encoding="utf-8")
    staging = tmp_path / "app.staging-0.4.0"
    _mk_staging(staging)
    res = execute_replace(install, staging, backup)
    assert res.ok is True
    assert not (backup / "junk.txt").exists()
    assert (backup / "mio-taskhub.exe").exists()


def test_execute_replace_prunes_old_backups(tmp_path):
    install = tmp_path / "app"
    _mk_install(install)
    # 造 3 个旧备份
    import time as _t
    for i, ver in enumerate(["0.1.0", "0.2.0", "0.3.0"]):
        b = tmp_path / f"app.bak-{ver}"
        _mk_install(b)
        # 拉开 mtime
        _t.sleep(0.02)
    staging = tmp_path / "app.staging-0.4.0"
    _mk_staging(staging)
    res = execute_replace(install, staging, tmp_path / "app.bak-0.3.0")
    assert res.ok is True
    remaining = sorted(p.name for p in tmp_path.glob("app.bak-*"))
    assert len(remaining) == 2, remaining


def test_execute_replace_rolls_back_when_staging_rename_fails(tmp_path, monkeypatch):
    install = tmp_path / "app"
    _mk_install(install)
    staging = tmp_path / "app.staging-0.4.0"
    _mk_staging(staging)
    backup = tmp_path / "app.bak-0.3.0"

    real = os.rename

    def boom(src, dst):
        if str(src) == str(staging):
            raise OSError("locked")
        return real(src, dst)

    monkeypatch.setattr(os, "rename", boom)
    monkeypatch.setattr("mio_taskhub.update.apply.time.sleep", lambda *_: None)
    res = execute_replace(install, staging, backup, retry_timeout=0.2)
    assert res.ok is False
    assert (install / "mio-taskhub.exe").read_text(encoding="utf-8") == "old-exe"


def test_execute_replace_honors_retry_timeout(tmp_path, monkeypatch):
    install = tmp_path / "app"
    _mk_install(install)
    staging = tmp_path / "app.staging-0.4.0"
    _mk_staging(staging)
    backup = tmp_path / "app.bak-0.3.0"
    real = os.rename

    def boom(src, dst):
        if str(src) == str(staging):
            raise OSError("locked")
        return real(src, dst)

    monkeypatch.setattr(os, "rename", boom)
    sleeps = {"n": 0}
    import mio_taskhub.update.apply as ap
    monkeypatch.setattr(ap.time, "sleep", lambda *_: sleeps.__setitem__("n", sleeps["n"] + 1))
    res = execute_replace(install, staging, backup, retry_timeout=0.2)
    assert res.ok is False and sleeps["n"] > 0
    assert (install / "mio-taskhub.exe").read_text(encoding="utf-8") == "old-exe"


def test_rename_retry_eventually_succeeds(tmp_path):
    src = tmp_path / "a"
    src.write_text("x", encoding="utf-8")
    dst = tmp_path / "b"
    calls = {"n": 0}
    real = os.rename

    def flaky(s, d):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("locked")
        return real(s, d)

    import mio_taskhub.update.apply as ap
    orig = ap.os.rename
    ap.os.rename = flaky
    try:
        _rename_retry(src, dst, timeout=5.0, interval=0.01)
    finally:
        ap.os.rename = orig
    assert dst.exists() and calls["n"] == 3


# ---- Task 7: 进程等待 + 健康确认 + 残留恢复 ----

import json
from mio_taskhub.update.apply import (
    pid_alive, wait_pid_exit, read_runtime, wait_healthy, recover_residual,
)


def test_read_runtime_missing(tmp_path):
    assert read_runtime(tmp_path / "nope.json") == {}


def test_read_runtime_ok(tmp_path):
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps({"version": "0.4.0", "started_at": 100.0}), encoding="utf-8")
    assert read_runtime(p)["version"] == "0.4.0"


def test_wait_healthy_success(tmp_path):
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps({"version": "0.4.0", "started_at": 200.0}), encoding="utf-8")
    assert wait_healthy("0.4.0", after_ts=100.0, runtime_path=p, timeout=2.0) is True


def test_wait_healthy_times_out_on_version_mismatch(tmp_path):
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps({"version": "0.3.0", "started_at": 200.0}), encoding="utf-8")
    assert wait_healthy("0.4.0", after_ts=100.0, runtime_path=p,
                        timeout=1.0, poll=0.1) is False


def test_wait_healthy_requires_new_started_at(tmp_path):
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps({"version": "0.4.0", "started_at": 50.0}), encoding="utf-8")
    assert wait_healthy("0.4.0", after_ts=100.0, runtime_path=p,
                        timeout=1.0, poll=0.1) is False


def test_wait_healthy_tolerates_malformed_runtime(tmp_path):
    p = tmp_path / "runtime.json"
    p.write_text(json.dumps({"version": "0.4.0", "started_at": "abc"}), encoding="utf-8")
    # 畸形数据不得抛异常，只能按"未就绪"轮询到超时
    assert wait_healthy("0.4.0", after_ts=0.0, runtime_path=p,
                        timeout=1.0, poll=0.1) is False


def test_wait_pid_exit_immediate_for_dead_pid():
    # 用一个几乎不可能存在的 PID
    assert wait_pid_exit(999999999, timeout=1.0, poll=0.1) is True


def test_recover_residual_cleans_staging(tmp_path):
    install = tmp_path / "app"
    install.mkdir()
    (install / "mio-taskhub.exe").write_text("cur", encoding="utf-8")
    (install / "_internal" / "web" / "dist").mkdir(parents=True)
    (install / "_internal" / "web" / "dist" / "index.html").write_text("x", encoding="utf-8")
    staging = tmp_path / "app.staging-0.4.0"
    staging.mkdir()
    (staging / "junk").write_text("z", encoding="utf-8")
    actions = recover_residual(install)
    assert not staging.exists()
    assert any("staging" in a for a in actions)


# ---- Task 8: apply-update 进程入口 ----

from pathlib import Path

from mio_taskhub.update.apply import run_apply_update


def test_run_apply_update_bad_args_returns_2():
    assert run_apply_update([]) == 2


def test_apply_log_honors_env_override(tmp_path, monkeypatch):
    from mio_taskhub.update.apply import _apply_log
    p = tmp_path / "custom.log"
    monkeypatch.setenv("MIO_UPDATE_LOG_PATH", str(p))
    _apply_log("hello")
    assert p.exists() and "hello" in p.read_text(encoding="utf-8")


def test_run_apply_update_sha_mismatch_returns_1(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_UPDATE_LOG_PATH", str(tmp_path / "apply.log"))
    z = tmp_path / "pkg.zip"
    z.write_bytes(b"not-a-zip")
    rc = run_apply_update([
        "--zip", str(z), "--sha256", "0" * 64, "--version", "0.4.0",
        "--pid", "999999999", "--target", str(tmp_path / "app"),
        "--runtime-json", str(tmp_path / "runtime.json"),
    ])
    assert rc == 1


def test_run_apply_update_full_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_UPDATE_LOG_PATH", str(tmp_path / "apply.log"))
    # 造一个"新版本"zip
    import zipfile, hashlib
    staging_src = tmp_path / "src"
    (staging_src / "_internal" / "web" / "dist").mkdir(parents=True)
    (staging_src / "mio-taskhub.exe").write_text("new", encoding="utf-8")
    (staging_src / "_internal" / "web" / "dist" / "index.html").write_text("new", encoding="utf-8")
    z = tmp_path / "pkg.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in staging_src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(staging_src))
    sha = hashlib.sha256(z.read_bytes()).hexdigest()

    # 现有 install（旧版本）
    install = tmp_path / "app"
    (install / "_internal" / "web" / "dist").mkdir(parents=True)
    (install / "mio-taskhub.exe").write_text("old", encoding="utf-8")
    (install / "_internal" / "web" / "dist" / "index.html").write_text("old", encoding="utf-8")

    # 模拟：新进程"启动成功"写 sentinel；不真的 spawn
    import mio_taskhub.update.apply as ap
    monkeypatch.setattr(ap, "_spawn_hub", lambda target: 4242)
    monkeypatch.setattr(ap, "wait_pid_exit", lambda pid, timeout=60.0, poll=0.5: True)

    def fake_wait_healthy(version, after_ts, runtime_path=None, timeout=90.0, poll=1.0):
        Path(runtime_path).write_text('{"version": "0.4.0", "started_at": 9e9}',
                                      encoding="utf-8")
        return True

    monkeypatch.setattr(ap, "wait_healthy", fake_wait_healthy)

    rc = run_apply_update([
        "--zip", str(z), "--sha256", sha, "--version", "0.4.0",
        "--pid", "999999999", "--target", str(install),
        "--runtime-json", str(tmp_path / "runtime.json"),
    ])
    assert rc == 0
    assert (install / "mio-taskhub.exe").read_text(encoding="utf-8") == "new"
    # 备份被删除（健康确认后）
    assert not list(tmp_path.glob("app.bak-*"))


def test_recover_residual_restores_install_from_backup(tmp_path):
    install = tmp_path / "app"
    backup = tmp_path / "app.bak-0.3.0"
    backup.mkdir()
    (backup / "mio-taskhub.exe").write_text("old", encoding="utf-8")
    (backup / "_internal" / "web" / "dist").mkdir(parents=True)
    (backup / "_internal" / "web" / "dist" / "index.html").write_text("old", encoding="utf-8")
    # install 缺失（半途失败）
    actions = recover_residual(install)
    assert install.exists() and (install / "mio-taskhub.exe").read_text(encoding="utf-8") == "old"
    assert any("restore" in a for a in actions)


def test_run_apply_update_rolls_back_when_unhealthy(tmp_path, monkeypatch):
    """健康确认失败 → 必须回滚 install 到旧版本，并 rc=1。"""
    monkeypatch.setenv("MIO_UPDATE_LOG_PATH", str(tmp_path / "apply.log"))
    import zipfile, hashlib
    staging_src = tmp_path / "src2"
    (staging_src / "_internal" / "web" / "dist").mkdir(parents=True)
    (staging_src / "mio-taskhub.exe").write_text("new", encoding="utf-8")
    (staging_src / "_internal" / "web" / "dist" / "index.html").write_text("new", encoding="utf-8")
    z = tmp_path / "pkg2.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for p in staging_src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(staging_src))
    sha = hashlib.sha256(z.read_bytes()).hexdigest()

    install = tmp_path / "app2"
    (install / "_internal" / "web" / "dist").mkdir(parents=True)
    (install / "mio-taskhub.exe").write_text("old", encoding="utf-8")
    (install / "_internal" / "web" / "dist" / "index.html").write_text("old", encoding="utf-8")

    import mio_taskhub.update.apply as ap
    monkeypatch.setattr(ap, "_spawn_hub", lambda target: 1)
    monkeypatch.setattr(ap, "wait_pid_exit", lambda pid, timeout=60.0, poll=0.5: True)
    monkeypatch.setattr(ap, "wait_healthy",
                        lambda *a, **k: False)          # 新版本"没起来"
    monkeypatch.setattr(ap, "_rename_retry", ap._rename_retry)  # keep real

    rc = ap.run_apply_update([
        "--zip", str(z), "--sha256", sha, "--version", "0.4.0",
        "--pid", "999999999", "--target", str(install),
        "--runtime-json", str(tmp_path / "runtime.json"),
    ])
    assert rc == 1
    # 已回滚：install 恢复旧内容
    assert (install / "mio-taskhub.exe").read_text(encoding="utf-8") == "old"
