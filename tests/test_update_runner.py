# tests/test_update_runner.py
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
    assert exited["n"] == 1
