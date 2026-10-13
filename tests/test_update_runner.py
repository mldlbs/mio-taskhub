# tests/test_update_runner.py
import sys

import pytest

from mio_taskhub.update.manifest import manifest_from_dict
from mio_taskhub.update import runner

MANIFEST = {
    "schema": 1, "version": "0.4.0", "channel": "stable",
    "released_at": "x", "min_supported": "0.3.0", "mandatory": False, "notes": "n",
    "assets": [{
        "os": "windows", "arch": "x64",
        "url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip",
        "sha256": "a" * 64, "size": 5, "format": "zip",
    }],
}


def test_runner_returns_1_when_exe_missing(tmp_path):
    m = manifest_from_dict(MANIFEST)
    rc = runner.default_apply_runner(tmp_path / "noinstall", tmp_path / "p.zip", m, 123)
    assert rc == 1


def test_runner_spawns_and_requests_exit(tmp_path, monkeypatch):
    install = tmp_path / "app"
    install.mkdir()
    (install / "mio-taskhub.exe").write_text("stub", encoding="utf-8")
    z = tmp_path / "p.zip"
    z.write_bytes(b"x")
    m = manifest_from_dict(MANIFEST)
    monkeypatch.setattr(runner, "_spool_root", lambda: tmp_path / "spool")

    spawned = {}
    exited = {"n": 0}

    class FakePopen:
        def __init__(self, args, **kw):
            spawned["args"] = args
            spawned["kw"] = kw
            self.pid = 4242

    monkeypatch.setattr(runner.subprocess, "Popen", FakePopen)
    runner.set_exit_callback(lambda: exited.__setitem__("n", exited["n"] + 1))
    try:
        rc = runner.default_apply_runner(install, z, m, 999)
    finally:
        runner.set_exit_callback(None)

    assert rc == 0
    args = spawned["args"]
    assert args[0].endswith("mio-taskhub.exe")
    assert args[1] == "apply-update"
    assert "--sha256" in args and args[args.index("--sha256") + 1] == "a" * 64
    assert "--version" in args and args[args.index("--version") + 1] == "0.4.0"
    assert "--pid" in args and args[args.index("--pid") + 1] == "999"
    assert "--target" in args and args[args.index("--target") + 1] == str(install)
    assert "--runtime-json" in args
    # 回归（2026-10-06）：updater 必须从 spool 运行 —— 从 install 内 spawn 时
    # 自身 exe/映像 DLL 会钉住 install，execute_replace 的 rename 必然 PermissionError
    from pathlib import Path
    spawn_dir = Path(args[0]).parent
    assert spawn_dir != install, "updater 不得从 install 目录内 spawn"
    assert (spawn_dir / "mio-taskhub.exe").exists()
    assert spawned["kw"].get("cwd") == str(spawn_dir)
    if sys.platform == "win32":
        # 回归（2026-10-06「下载完安装不了」）：updater 必须 CREATE_BREAKAWAY_JOB 脱离
        # hub 的 Job，否则 supervisor 退出 CloseHandle(job) 时被 KILL_ON_JOB_CLOSE 连带杀死
        assert spawned["kw"].get("creationflags", 0) & 0x01000000, "missing CREATE_BREAKAWAY_JOB"
    assert exited["n"] == 1


def test_prepare_spool_copies_required_entries(tmp_path, monkeypatch):
    install = tmp_path / "app"
    (install / "_internal" / "web" / "dist").mkdir(parents=True)
    (install / "mio-taskhub.exe").write_text("exe", encoding="utf-8")
    (install / "_internal" / "web" / "dist" / "index.html").write_text("<html/>", encoding="utf-8")
    (install / "使用说明.txt").write_text("junk", encoding="utf-8")
    monkeypatch.setattr(runner, "_spool_root", lambda: tmp_path / "spool")

    spool = runner._prepare_spool(install)
    assert spool is not None
    assert (spool / "mio-taskhub.exe").read_text(encoding="utf-8") == "exe"
    assert (spool / "_internal" / "web" / "dist" / "index.html").exists()
    assert not (spool / "使用说明.txt").exists(), "spool 只需 exe + _internal"


def test_prepare_spool_returns_none_when_no_exe(tmp_path, monkeypatch):
    install = tmp_path / "app"
    install.mkdir()
    monkeypatch.setattr(runner, "_spool_root", lambda: tmp_path / "spool")
    assert runner._prepare_spool(install) is None


def test_prune_spools_removes_stale_keeps_fresh(tmp_path):
    import os as _os
    import time as _time
    root = tmp_path / "spool"
    stale = root / "stale"
    fresh = root / "fresh"
    stale.mkdir(parents=True)
    fresh.mkdir(parents=True)
    old = _time.time() - 7200
    _os.utime(stale, (old, old))
    runner._prune_spools(root, max_age_s=3600.0)
    assert not stale.exists(), "超过 1h 的 spool 应被清理"
    assert fresh.exists(), "新鲜 spool 不得误删"
