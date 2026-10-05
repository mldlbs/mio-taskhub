# -*- coding: utf-8 -*-
"""本地假 release：check → download → 校验 → execute_replace，全程不触网。"""
import hashlib
import zipfile
from pathlib import Path

from mio_taskhub.update.manifest import manifest_from_dict
from mio_taskhub.update.service import UpdateService, UpdateState
from mio_taskhub.update.downloader import download


def _make_zip(dest: Path, exe_text: str) -> str:
    src = dest.parent / (dest.stem + "-src")
    (src / "_internal" / "web" / "dist").mkdir(parents=True, exist_ok=True)
    (src / "mio-taskhub.exe").write_text(exe_text, encoding="utf-8")
    (src / "_internal" / "web" / "dist" / "index.html").write_text(exe_text, encoding="utf-8")
    with zipfile.ZipFile(dest, "w") as zf:
        for p in src.rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(src))
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def test_local_release_flow(tmp_path):
    payload = tmp_path / "served.zip"
    sha = _make_zip(payload, "new")
    raw = payload.read_bytes()

    def opener(url):
        return iter([raw])

    manifest = manifest_from_dict({
        "schema": 1, "version": "0.4.0", "channel": "stable",
        "released_at": "x", "min_supported": "0.3.0", "mandatory": False, "notes": "n",
        "assets": [{
            "os": "windows", "arch": "x64",
            "url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip",
            "sha256": sha, "size": len(raw), "format": "zip",
        }],
    })

    class Src:
        def fetch_manifest(self): return manifest

    updir = tmp_path / "updates"
    svc = UpdateService(
        source=Src(),
        downloader=lambda url, dest, sha_, size=None, progress=None:
            download(url, dest, sha_, size=size, opener=opener, progress=progress),
        install_dir=tmp_path / "app",
        current_version="0.3.0",
        prefs_path=tmp_path / "prefs.json",
        update_dir=updir,
    )
    assert svc.check()["state"] == UpdateState.AVAILABLE.value
    st = svc.download()
    assert st["state"] == UpdateState.READY.value
    got = (updir / "mio-taskhub-0.4.0.zip")
    assert got.exists() and hashlib.sha256(got.read_bytes()).hexdigest() == sha


def test_local_release_apply_transaction(tmp_path):
    """下载后的 zip → execute_replace 到 install → wait_healthy（假 sentinel）全链。"""
    import json as _json
    from mio_taskhub.update.apply import execute_replace, wait_healthy

    payload = tmp_path / "served.zip"
    _make_zip(payload, "new")

    install = tmp_path / "app"
    (install / "_internal" / "web" / "dist").mkdir(parents=True)
    (install / "mio-taskhub.exe").write_text("old", encoding="utf-8")
    (install / "_internal" / "web" / "dist" / "index.html").write_text("old", encoding="utf-8")

    # 解压 zip 到 staging，再事务替换
    staging = tmp_path / "app.staging-0.4.0"
    with zipfile.ZipFile(payload) as zf:
        zf.extractall(staging)
    backup = tmp_path / "app.bak-0.3.0"
    res = execute_replace(install, staging, backup)
    assert res.ok is True
    assert (install / "mio-taskhub.exe").read_text(encoding="utf-8") == "new"

    # 假 sentinel：新版本"启动成功"
    rt = tmp_path / "runtime.json"
    rt.write_text(_json.dumps({"version": "0.4.0", "started_at": 9e9}), encoding="utf-8")
    assert wait_healthy("0.4.0", after_ts=0.0, runtime_path=rt, timeout=2.0) is True
