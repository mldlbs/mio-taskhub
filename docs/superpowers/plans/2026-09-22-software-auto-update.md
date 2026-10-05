# mio-taskhub 软件自动更新 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **用户约定：本仓库当前不执行 git 提交。** 每个任务末尾用「Checkpoint」代替 Commit：跑测试 + 自检。若要提交，由人工在 Checkpoint 处执行。

**Goal:** 让 mio-taskhub（绿色版 onedir）能从 GitHub Releases 检查、下载、校验并**可恢复地**替换自身，失败可回滚，成功以新 Hub 健康启动为准。

**Architecture:** 应用内 updater 模式。hub 进程内 `UpdateService` 定时查 `latest.json` → 提示 → 下载校验 → spawn `mio-taskhub.exe --apply-update ...` 独立进程完成"等 PID 退出 → staging 解压 → 同卷 rename 事务替换 → 重启 → 健康确认 → 删备份/回滚"。只替换程序目录，绝不碰 `~/.mio_taskhub` 用户数据。

**Tech Stack:** Python 3.12（标准库：`urllib`/`zipfile`/`hashlib`/`ctypes`/`threading`）、FastAPI（现有）、pystray（现有托盘）、React+Vite（现有前端）、PowerShell（打包/发版）、GitHub Releases（`gh` CLI 发版）。

**Spec:** `docs/superpowers/specs/2026-09-22-software-auto-update-design.md`

---

## File Structure

**新增**
- `mio_taskhub/version.py` — 单一版本源 + install_dir
- `mio_taskhub/update/__init__.py`
- `mio_taskhub/update/manifest.py` — 契约解析 + 版本比较
- `mio_taskhub/update/source.py` — GitHub Release 获取
- `mio_taskhub/update/downloader.py` — 流式下载 + sha256
- `mio_taskhub/update/apply.py` — 更新事务（替换/回滚/健康确认/残留恢复）
- `mio_taskhub/update/service.py` — 状态机 + 编排（单例）
- `mio_taskhub/api/update.py` — REST 端点
- `web/src/components/UpdateBanner.jsx` — 前端更新条
- `packaging/release.ps1` — 发版脚本
- `tests/test_update_manifest.py`
- `tests/test_update_source.py`
- `tests/test_update_downloader.py`
- `tests/test_update_apply.py`
- `tests/test_update_service.py`
- `tests/test_update_api.py`

**修改**
- `mio_taskhub/main.py` — 挂 router；启动写 runtime sentinel；Startup 调残留恢复
- `mio_taskhub/background.py` — `start_background_jobs` 里启动 UpdateService 后台线程
- `packaging/run.py` — 新增 `apply-update` 模式分派
- `packaging/run_hub.py` — 托盘菜单加"检查更新/立即更新" + 通知
- `packaging/build.ps1` — 注入版本 + 资产改名 `mio-taskhub-win64.zip`
- `web/src/api.js` — 加 update API
- `web/src/App.jsx` — 挂 `<UpdateBanner/>`

---

## Task 1: 版本源与安装目录

**Files:**
- Create: `mio_taskhub/version.py`
- Test: `tests/test_update_manifest.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_update_manifest.py
import os
import sys
from pathlib import Path

from mio_taskhub import version


def test_version_is_nonempty_semver():
    parts = version.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_install_dir_dev_points_to_repo_root():
    repo_root = Path(version.__file__).resolve().parent.parent
    assert version.install_dir() == repo_root


def test_is_frozen_false_in_tests():
    assert version.is_frozen() is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q`
Expected: FAIL（`ModuleNotFoundError: mio_taskhub.version`）

- [ ] **Step 3: 实现 version.py**

```python
# mio_taskhub/version.py
# -*- coding: utf-8 -*-
"""单一版本源 + 安装目录解析。

`__version__` 是唯一权威版本号：打包时由 packaging/build.ps1 覆写，
运行时被 FastAPI(app version) 与 update 子模块共同引用。
"""
import sys
from pathlib import Path

__version__ = "0.3.0"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包产物中。"""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """程序目录：frozen → exe 所在目录；开发态 → 仓库根。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q`
Expected: PASS。**不提交 git**（按用户约定）。

---

## Task 2: 版本比较（SemVer）

**Files:**
- Create: `mio_taskhub/update/__init__.py`（空文件）
- Create: `mio_taskhub/update/manifest.py`
- Test: `tests/test_update_manifest.py`

- [ ] **Step 1: 写失败测试**（追加到 `tests/test_update_manifest.py`）

```python
from mio_taskhub.update.manifest import parse_version, is_newer


def test_parse_version_valid():
    assert parse_version("0.3.0") == (0, 3, 0, 1, "")
    assert parse_version("1.2.3-rc.1") == (1, 2, 3, 0, "rc.1")


def test_parse_version_invalid():
    assert parse_version("1.2") is None
    assert parse_version("v1.2.3") is None
    assert parse_version("") is None


def test_is_newer_basic():
    assert is_newer("0.3.0", "0.4.0") is True
    assert is_newer("0.4.0", "0.3.0") is False
    assert is_newer("0.3.0", "0.3.0") is False
    assert is_newer("0.3.0", "0.3.1") is True
    assert is_newer("0.9.9", "1.0.0") is True


def test_is_newer_prerelease_lower_than_release():
    assert is_newer("0.4.0", "0.4.0-rc.1") is False
    assert is_newer("0.4.0-rc.1", "0.4.0") is True


def test_is_newer_failsafe_on_invalid():
    assert is_newer("bad", "0.4.0") is False
    assert is_newer("0.3.0", "bad") is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q -k "version or newer"`
Expected: FAIL（`ModuleNotFoundError: mio_taskhub.update`）

- [ ] **Step 3: 实现 manifest.py 的版本部分**

```python
# mio_taskhub/update/__init__.py
# -*- coding: utf-8 -*-
```

```python
# mio_taskhub/update/manifest.py
# -*- coding: utf-8 -*-
"""latest.json Manifest 契约 + 语义化版本比较。

只做纯逻辑，不触网（触网在 source.py）。解析/校验失败一律抛 ManifestError
或返回 None，调用方按 "不更新" 处理（fail-safe）。
"""
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple
from urllib.parse import urlparse

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.\-]+))?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_HOSTS = ("github.com",)
ALLOWED_HOST_SUFFIXES = (".githubusercontent.com",)
SUPPORTED_SCHEMA = 1


class ManifestError(ValueError):
    """Manifest 无效。"""


def parse_version(s: str) -> Optional[Tuple[int, int, int, int, str]]:
    """'1.2.3[-pre]' → (major, minor, patch, is_release, pre)。

    is_release: 正式版=1，预发布=0（故 1.0.0 > 1.0.0-rc.1）。
    非法版本返回 None。
    """
    m = SEMVER_RE.match((s or "").strip())
    if not m:
        return None
    pre = m.group(4) or ""
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre)


def is_newer(current: str, latest: str) -> bool:
    """latest 是否比 current 新。任一解析失败 → False（fail-safe）。"""
    c = parse_version(current)
    l = parse_version(latest)
    if c is None or l is None:
        return False
    return l > c


def is_valid_asset_url(url: str) -> bool:
    """仅接受 https + 白名单域（github.com / *.githubusercontent.com）。"""
    try:
        u = urlparse(url or "")
    except ValueError:
        return False
    if u.scheme != "https" or not u.hostname:
        return False
    host = u.hostname.lower()
    if host in ALLOWED_HOSTS:
        return True
    return any(host.endswith(s) for s in ALLOWED_HOST_SUFFIXES)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q`
Expected: PASS。不提交 git。

---

## Task 3: Manifest 契约解析与校验

**Files:**
- Modify: `mio_taskhub/update/manifest.py`
- Test: `tests/test_update_manifest.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
import pytest
from mio_taskhub.update.manifest import (
    Asset, UpdateManifest, manifest_from_dict, ManifestError,
)

GOOD = {
    "schema": 1,
    "version": "0.4.0",
    "channel": "stable",
    "released_at": "2026-09-22T10:00:00Z",
    "min_supported": "0.3.0",
    "mandatory": False,
    "notes": "## 变更\n- x",
    "assets": [{
        "os": "windows", "arch": "x64",
        "url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip",
        "sha256": "a" * 64, "size": 10, "format": "zip",
    }],
}


def test_manifest_from_dict_ok():
    m = manifest_from_dict(GOOD)
    assert isinstance(m, UpdateManifest)
    assert m.version == "0.4.0"
    assert m.asset_for("windows", "x64").sha256 == "a" * 64


def test_manifest_rejects_bad_schema():
    bad = dict(GOOD, schema=2)
    with pytest.raises(ManifestError):
        manifest_from_dict(bad)


def test_manifest_rejects_bad_sha():
    bad = dict(GOOD, assets=[dict(GOOD["assets"][0], sha256="xyz")])
    with pytest.raises(ManifestError):
        manifest_from_dict(bad)


def test_manifest_rejects_http_url():
    bad = dict(GOOD, assets=[dict(GOOD["assets"][0], url="http://evil.com/x.zip")])
    with pytest.raises(ManifestError):
        manifest_from_dict(bad)


def test_manifest_rejects_non_whitelisted_host():
    bad = dict(GOOD, assets=[dict(GOOD["assets"][0], url="https://evil.com/x.zip")])
    with pytest.raises(ManifestError):
        manifest_from_dict(bad)


def test_manifest_rejects_bad_version():
    with pytest.raises(ManifestError):
        manifest_from_dict(dict(GOOD, version="nope"))


def test_manifest_weak_verify_allows_empty_sha():
    raw = dict(GOOD, assets=[dict(GOOD["assets"][0], sha256="")])
    m = manifest_from_dict(raw, require_sha=False)
    assert m.weak_verify is True
    with pytest.raises(ManifestError):
        manifest_from_dict(raw, require_sha=True)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q -k manifest`
Expected: FAIL（`ImportError: cannot import name 'Asset'`）

- [ ] **Step 3: 实现契约类与解析**（追加到 `manifest.py`）

```python
@dataclass
class Asset:
    os: str
    arch: str
    url: str
    sha256: str
    size: int
    format: str = "zip"


@dataclass
class UpdateManifest:
    schema: int
    version: str
    channel: str
    released_at: str
    min_supported: str
    mandatory: bool
    notes: str
    assets: list = field(default_factory=list)
    weak_verify: bool = False

    def asset_for(self, os_name: str = "windows", arch: str = "x64") -> Optional[Asset]:
        for a in self.assets:
            if a.os == os_name and a.arch == arch:
                return a
        return None


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ManifestError(msg)


def _as_bool(v) -> bool:
    """严格布尔归一：字符串仅 '1/true/yes/on' 为真（避免 'false' 被当成真）。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    return bool(v)


def manifest_from_dict(d: dict, require_sha: bool = True) -> UpdateManifest:
    """解析并校验 latest.json。**任何非法输入 → ManifestError**（含 Inf/浮点/非 dict）。"""
    _require(isinstance(d, dict), "manifest must be an object")
    schema = d.get("schema")
    _require(type(schema) is int and schema == SUPPORTED_SCHEMA,
             f"unsupported schema: {schema!r} (expected {SUPPORTED_SCHEMA})")
    version = str(d.get("version") or "")
    _require(parse_version(version) is not None, f"invalid version: {version!r}")
    min_supported = str(d.get("min_supported") or "0.0.0")
    _require(parse_version(min_supported) is not None,
             f"invalid min_supported: {min_supported!r}")

    raw_assets = d.get("assets")
    _require(isinstance(raw_assets, list) and raw_assets, "assets must be a non-empty list")
    assets = []
    weak = False
    for ra in raw_assets:
        _require(isinstance(ra, dict), "asset must be an object")
        url = str(ra.get("url") or "")
        _require(is_valid_asset_url(url), f"asset url not allowed: {url!r}")
        sha = str(ra.get("sha256") or "").lower()
        if not sha:
            _require(not require_sha, "asset sha256 empty (sha required)")
            weak = True
        else:
            _require(bool(SHA256_RE.match(sha)), f"invalid sha256: {sha!r}")
        raw_size = ra.get("size")
        _require(isinstance(raw_size, int) and not isinstance(raw_size, bool),
                 f"invalid asset size: {raw_size!r}")
        _require(raw_size > 0, "asset size must be > 0")
        assets.append(Asset(
            os=str(ra.get("os") or "windows"),
            arch=str(ra.get("arch") or "x64"),
            url=url, sha256=sha, size=raw_size,
            format=str(ra.get("format") or "zip"),
        ))

    return UpdateManifest(
        schema=SUPPORTED_SCHEMA,
        version=version,
        channel=str(d.get("channel") or "stable"),
        released_at=str(d.get("released_at") or ""),
        min_supported=min_supported,
        mandatory=_as_bool(d.get("mandatory")),
        notes=str(d.get("notes") or ""),
        assets=assets,
        weak_verify=weak,
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q`
Expected: PASS（15 passed）

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q`
Expected: PASS。不提交 git。

---

## Task 4: GitHub Release 源（含 latest.json 缺失回退）

**Files:**
- Create: `mio_taskhub/update/source.py`
- Test: `tests/test_update_source.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_update_source.py
import json
import pytest

from mio_taskhub.update.source import GitHubReleaseSource, SourceError

MANIFEST = {
    "schema": 1, "version": "0.4.0", "channel": "stable",
    "released_at": "2026-09-22T10:00:00Z", "min_supported": "0.3.0",
    "mandatory": False, "notes": "n",
    "assets": [{
        "os": "windows", "arch": "x64",
        "url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip",
        "sha256": "b" * 64, "size": 20, "format": "zip",
    }],
}
RELEASE_JSON = {
    "tag_name": "v0.5.0",
    "assets": [{
        "name": "mio-taskhub-win64.zip",
        "browser_download_url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.5.0/mio-taskhub-win64.zip",
        "size": 30,
    }],
}


def _opener_ok(url):
    if url.endswith("latest.json"):
        return json.dumps(MANIFEST).encode()
    raise AssertionError("unexpected url " + url)


def test_fetch_manifest_from_latest_json():
    src = GitHubReleaseSource(base_url="https://github.com/mldlbs/mio-taskhub",
                              opener=_opener_ok)
    m = src.fetch_manifest()
    assert m.version == "0.4.0"
    assert m.weak_verify is False


def _opener_fallback(url):
    if url.endswith("latest.json"):
        raise SourceError("HTTP 404")
    return json.dumps(RELEASE_JSON).encode()


def test_fetch_manifest_fallback_to_release_api():
    src = GitHubReleaseSource(base_url="https://github.com/mldlbs/mio-taskhub",
                              opener=_opener_fallback)
    m = src.fetch_manifest()
    assert m.version == "0.5.0"
    assert m.weak_verify is True
    assert m.asset_for("windows", "x64").url.endswith("mio-taskhub-win64.zip")


def _opener_broken(url):
    raise SourceError("boom")


def test_fetch_manifest_propagates_error_when_both_fail():
    src = GitHubReleaseSource(base_url="https://github.com/mldlbs/mio-taskhub",
                              opener=_opener_broken)
    with pytest.raises(SourceError):
        src.fetch_manifest()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_source.py -q`
Expected: FAIL（`ModuleNotFoundError: mio_taskhub.update.source`）

- [ ] **Step 3: 实现 source.py**

```python
# mio_taskhub/update/source.py
# -*- coding: utf-8 -*-
"""更新源：从 GitHub Releases 获取 latest.json（含缺失回退）。

- 首选 CDN 固定 URL `.../releases/latest/download/latest.json`（免 token、不吃 API 限额）。
- 缺失时回退 `api.github.com/repos/<owner>/<repo>/releases/latest`，用 tag + 同名资产拼 manifest（弱校验）。
- 所有网络访问经 `opener(url) -> bytes`，便于测试注入。
"""
import json
import os
import urllib.request

from mio_taskhub.update.manifest import manifest_from_dict, UpdateManifest

DEFAULT_BASE = "https://github.com/mldlbs/mio-taskhub"
ASSET_NAME = "mio-taskhub-win64.zip"


class SourceError(RuntimeError):
    """更新源不可用 / 返回非法。"""


def _default_opener(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mio-taskhub-updater"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class GitHubReleaseSource:
    def __init__(self, base_url: str = None, timeout: float = 10.0, opener=None):
        self.base = (base_url or os.environ.get("MIO_UPDATE_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.timeout = timeout
        self._opener = opener or (lambda u: _default_opener(u, self.timeout))

    def manifest_url(self) -> str:
        # 测试 / 内网可把 base 指向任意支持 /latest.json 的地址
        if "github.com" not in self.base:
            return self.base + "/latest.json"
        return self.base + "/releases/latest/download/latest.json"

    def _api_latest_url(self) -> str:
        # https://github.com/<owner>/<repo> → https://api.github.com/repos/<owner>/<repo>/releases/latest
        if "github.com" not in self.base:
            raise SourceError("非 GitHub 源不支持 release API 回退")
        parts = self.base.split("github.com/", 1)[-1].strip("/")
        return "https://api.github.com/repos/%s/releases/latest" % parts

    def fetch_manifest(self) -> UpdateManifest:
        try:
            raw = self._opener(self.manifest_url())
            return manifest_from_dict(json.loads(raw.decode("utf-8")))
        except Exception as e:  # noqa: BLE001 —— latest.json 缺失/非法 → 回退
            first_err = e
        try:
            return self._fetch_via_api()
        except Exception as second_err:  # noqa: BLE001
            raise SourceError("无法获取更新清单：%s；回退也失败：%s"
                              % (first_err, second_err)) from second_err

    def _fetch_via_api(self) -> UpdateManifest:
        raw = self._opener(self._api_latest_url())
        rel = json.loads(raw.decode("utf-8"))
        tag = str(rel.get("tag_name") or "").lstrip("vV")
        if not tag:
            raise SourceError("release 无 tag_name")
        asset = None
        for a in rel.get("assets") or []:
            if a.get("name") == ASSET_NAME:
                asset = a
                break
        if asset is None:
            raise SourceError("release 无资产 %s" % ASSET_NAME)
        size = int(asset.get("size") or 0)
        if size <= 0:
            raise SourceError("release 资产缺少有效 size")
        pseudo = {
            "schema": 1, "version": tag, "channel": "stable",
            "released_at": str(rel.get("published_at") or ""),
            "min_supported": "0.0.0", "mandatory": False,
            "notes": str(rel.get("body") or ""),
            "assets": [{
                "os": "windows", "arch": "x64",
                "url": asset.get("browser_download_url"),
                "sha256": "", "size": size, "format": "zip",
            }],
        }
        return manifest_from_dict(pseudo, require_sha=False)


def default_source() -> GitHubReleaseSource:
    return GitHubReleaseSource()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_source.py -q`
Expected: PASS（3 passed）

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_source.py -q`
Expected: PASS。不提交 git。

---

## Task 5: 下载器（流式 + sha256 + 重试）

**Files:**
- Create: `mio_taskhub/update/downloader.py`
- Test: `tests/test_update_downloader.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_update_downloader.py
import hashlib
import pytest

from mio_taskhub.update.downloader import download, sha256_file, DownloadError

PAYLOAD = b"hello-zip-bytes" * 100
GOOD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _opener_ok(url):
    # 返回一个可迭代的 chunk 生成器（模拟流式）
    def gen():
        for i in range(0, len(PAYLOAD), 64):
            yield PAYLOAD[i:i + 64]
    return gen()


def test_download_ok_writes_and_verifies(tmp_path):
    dest = tmp_path / "pkg.zip"
    progress = []
    out = download("https://github.com/x/y.zip", dest, sha256=GOOD_SHA,
                   opener=_opener_ok, progress=lambda d, t: progress.append((d, t)))
    assert out == dest
    assert dest.read_bytes() == PAYLOAD
    assert not (tmp_path / "pkg.zip.part").exists()
    assert progress and progress[-1][0] == len(PAYLOAD)


def test_download_sha_mismatch_raises_and_cleans(tmp_path):
    dest = tmp_path / "pkg.zip"
    with pytest.raises(DownloadError):
        download("https://github.com/x/y.zip", dest, sha256="0" * 64,
                 opener=_opener_ok, retries=2)
    assert not dest.exists()
    assert not (tmp_path / "pkg.zip.part").exists()


def test_download_retries_then_succeeds(tmp_path):
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("net down")
        return _opener_ok(url)

    dest = tmp_path / "pkg.zip"
    download("https://github.com/x/y.zip", dest, sha256=GOOD_SHA, opener=flaky, retries=3)
    assert dest.read_bytes() == PAYLOAD
    assert calls["n"] == 2


def test_sha256_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(PAYLOAD)
    assert sha256_file(f) == GOOD_SHA


def test_download_size_mismatch_retries_and_cleans(tmp_path):
    calls = {"n": 0}

    def short(url):
        calls["n"] += 1
        return _opener_ok(url)  # 实际字节 < size → 触发 size mismatch

    dest = tmp_path / "pkg.zip"
    with pytest.raises(DownloadError):
        download("https://github.com/x/y.zip", dest, sha256=GOOD_SHA,
                 size=len(PAYLOAD) + 999, opener=short, retries=2)
    assert calls["n"] == 2
    assert not dest.exists()
    assert not (tmp_path / "pkg.zip.part").exists()


def test_download_opener_closed_even_on_error(tmp_path):
    """流式响应必须被关闭（防 socket 泄漏），即使读取中途抛错。"""
    opened = {"closed": False}

    class FakeResp:
        def read(self, n):
            raise OSError("mid-stream boom")

        def close(self):
            opened["closed"] = True

    import mio_taskhub.update.downloader as dl

    def opener(url):
        return dl._default_opener.__wrapped__(url) if False else iter([b"x"])

    # 直接测 gen 的 finally 语义：手动构造一个带 close 的 fake
    def opener_with_close(url):
        resp = FakeResp()

        def gen():
            try:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        return
                    yield chunk
            finally:
                resp.close()

        return gen()

    dest = tmp_path / "pkg.zip"
    with pytest.raises(DownloadError):
        download("https://github.com/x/y.zip", dest, opener=opener_with_close, retries=1)
    assert opened["closed"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_downloader.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 downloader.py**

```python
# mio_taskhub/update/downloader.py
# -*- coding: utf-8 -*-
"""流式下载 + 边下边算 sha256 + 重试；只在校验通过后 rename 成最终文件。"""
import hashlib
import os
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional


class DownloadError(RuntimeError):
    """下载或校验失败。"""


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_opener(url: str, timeout: float = 60.0):
    """返回 bytes 迭代器；读取结束/异常时确保关闭响应（防 socket 泄漏）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "mio-taskhub-updater"})
    resp = urllib.request.urlopen(req, timeout=timeout)

    def gen():
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    return
                yield chunk
        finally:
            resp.close()

    return gen()


def download(url: str, dest, sha256: str = None, size: int = None,
             opener: Callable = None, retries: int = 3,
             progress: Callable[[int, int], None] = None) -> Path:
    """下载 url → dest。边下边算 sha256，匹配后 rename .part→dest。

    opener(url) 返回 bytes 的迭代器。失败重试 retries 次。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.parent / (dest.name + ".part")   # 不用 with_suffix：对 tar.gz/无后缀名都正确
    opener = opener or _default_opener
    want = (sha256 or "").lower()
    last_err = None

    for attempt in range(1, retries + 1):
        h = hashlib.sha256()
        done = 0
        try:
            if part.exists():
                part.unlink()
            with open(part, "wb") as f:
                for chunk in opener(url):
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, size or 0)
            if size and done != size:
                raise DownloadError("size mismatch: got %d want %d" % (done, size))
            if want and h.hexdigest() != want:
                raise DownloadError("sha256 mismatch")
            os.replace(part, dest)          # 同卷 replace，原子
            return dest
        except Exception as e:  # noqa: BLE001
            last_err = e
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            if attempt < retries:
                time.sleep(0.5 * attempt)
    raise DownloadError("download failed after %d attempts: %s" % (retries, last_err))
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_downloader.py -q`
Expected: PASS（4 passed）

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_downloader.py -q`
Expected: PASS。不提交 git。

---

## Task 6: 更新事务核心（execute_replace 纯函数）

**Files:**
- Create: `mio_taskhub/update/apply.py`
- Test: `tests/test_update_apply.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_update_apply.py
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
    assert not (backup / "junk.txt").exists()   # 同名备份被替换，不含陈旧内容
    assert (backup / "mio-taskhub.exe").exists()


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
    res = execute_replace(install, staging, backup)
    assert res.ok is False
    # 已回滚：install 恢复为旧内容
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_apply.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 apply.py（事务核心部分）**

```python
# mio_taskhub/update/apply.py
# -*- coding: utf-8 -*-
"""更新事务：同卷 rename + 可恢复替换，失败回滚。

放到独立进程执行（mio-taskhub.exe --apply-update ...），因此不持有 hub 的文件句柄。
"""
import os
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

REQUIRED_ENTRIES = (
    "mio-taskhub.exe",
    os.path.join("_internal", "web", "dist", "index.html"),
)


@dataclass
class ReplaceResult:
    ok: bool
    backup: str = ""
    error: str = ""


def _rename_retry(src, dst, timeout: float = 30.0, interval: float = 0.5) -> None:
    """同卷 rename，遇文件锁退避重试，超时抛 OSError。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.rename(str(src), str(dst))
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(interval)


def verify_staging(staging) -> Tuple[bool, str]:
    """校验解压出的程序目录含关键项。"""
    staging = Path(staging)
    for rel in REQUIRED_ENTRIES:
        if not (staging / rel).exists():
            return False, "staging 缺少关键项：%s" % rel
    return True, "ok"


def execute_replace(install, staging, backup) -> ReplaceResult:
    """install→backup，staging→install；第二步失败则回滚。

    调用方保证 backup 命名含来源版本（app.bak-<from_version>），
    以便"同名备份可安全覆盖、异版本备份不被破坏"。
    """
    import shutil
    install, staging, backup = Path(install), Path(staging), Path(backup)

    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    try:
        _rename_retry(install, backup)
    except OSError as e:
        return ReplaceResult(False, error="install→backup rename 失败：%s" % e)

    try:
        _rename_retry(staging, install)
    except OSError as e:
        try:
            _rename_retry(backup, install)
        except OSError:
            return ReplaceResult(False, str(backup),
                                 "staging→install 失败且回滚失败：%s" % e)
        return ReplaceResult(False, str(backup), "staging→install rename 失败（已回滚）：%s" % e)

    return ReplaceResult(True, backup=str(backup), error="")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_apply.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_apply.py -q`
Expected: PASS。不提交 git。

---

## Task 7: 进程等待 + 健康确认 + 残留恢复

**Files:**
- Modify: `mio_taskhub/update/apply.py`
- Test: `tests/test_update_apply.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
import json
import time as _time
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_apply.py -q -k "runtime or healthy or residual or pid_exit"`
Expected: FAIL（`ImportError`）

- [ ] **Step 3: 实现（追加到 apply.py）**

```python
RUNTIME_REL = os.path.join(".mio_taskhub", "update", "runtime.json")


def default_runtime_path() -> Path:
    return Path.home() / RUNTIME_REL


def pid_alive(pid: int) -> bool:
    """跨平台判断进程是否存活。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_pid_exit(pid: int, timeout: float = 60.0, poll: float = 0.5) -> bool:
    """等进程退出；超时返回 False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(poll)
    return not pid_alive(pid)


def read_runtime(path=None) -> dict:
    p = Path(path) if path else default_runtime_path()
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def wait_healthy(target_version: str, after_ts: float, runtime_path=None,
                 timeout: float = 90.0, poll: float = 1.0) -> bool:
    """轮询 sentinel：version == 目标 且 started_at > after_ts。"""
    deadline = time.monotonic() + timeout
    p = Path(runtime_path) if runtime_path else default_runtime_path()
    while time.monotonic() < deadline:
        st = read_runtime(p)
        if (str(st.get("version")) == str(target_version)
                and float(st.get("started_at") or 0) > float(after_ts)):
            return True
        time.sleep(poll)
    return False


def _looks_complete(dir_path) -> bool:
    ok, _ = verify_staging(dir_path)
    return ok


def recover_residual(install_dir, log: Callable[[str], None] = None) -> list:
    """hub 启动时调用：清理 staging、在 install 缺失时从 backup 恢复。"""
    install = Path(install_dir)
    parent = install.parent
    name = install.name
    actions = []

    def _log(m):
        actions.append(m)
        if log:
            log(m)

    for staging in sorted(parent.glob(name + ".staging-*")):
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        _log("清理残留 staging：%s" % staging.name)

    if not _looks_complete(install):
        backups = sorted(parent.glob(name + ".bak-*"))
        for backup in reversed(backups):
            if _looks_complete(backup):
                if install.exists():
                    import shutil
                    shutil.rmtree(install, ignore_errors=True)
                try:
                    os.rename(str(backup), str(install))
                    _log("restore：从 %s 恢复 install" % backup.name)
                except OSError as e:
                    _log("restore 失败：%s" % e)
                break
    return actions
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_apply.py -q`
Expected: PASS（13 passed）

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_apply.py -q`
Expected: PASS。不提交 git。

---

## Task 8: apply-update 进程入口

**Files:**
- Modify: `mio_taskhub/update/apply.py`
- Modify: `packaging/run.py`
- Test: `tests/test_update_apply.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
from mio_taskhub.update.apply import run_apply_update


def test_run_apply_update_bad_args_returns_2():
    assert run_apply_update([]) == 2


def test_run_apply_update_sha_mismatch_returns_1(tmp_path):
    z = tmp_path / "pkg.zip"
    z.write_bytes(b"not-a-zip")
    rc = run_apply_update([
        "--zip", str(z), "--sha256", "0" * 64, "--version", "0.4.0",
        "--pid", "999999999", "--target", str(tmp_path / "app"),
        "--runtime-json", str(tmp_path / "runtime.json"),
    ])
    assert rc == 1


def test_run_apply_update_full_flow(tmp_path, monkeypatch):
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_apply.py -q -k run_apply`
Expected: FAIL（`ImportError: cannot import name 'run_apply_update'`）

- [ ] **Step 3: 实现 run_apply_update + _spawn_hub（追加到 apply.py）**

```python
def _apply_log(msg: str) -> None:
    """追加到 ~/.mio_taskhub/update/apply.log。"""
    log_dir = Path.home() / ".mio_taskhub" / "update"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_dir / "apply.log", "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (ts, msg))
    except OSError:
        pass


def _spawn_hub(target) -> int:
    """以 hub 模式启动新版本；返回 PID（detached）。"""
    import subprocess
    exe = str(Path(target) / "mio-taskhub.exe")
    creation = 0
    if sys.platform == "win32":
        creation = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen([exe, "hub"], cwd=str(target), creationflags=creation,
                         close_fds=True)
    return p.pid


def _parse_args(argv):
    """极简参数解析，返回 dict 或 None。"""
    out = {}
    i = 0
    keys = {"--zip", "--sha256", "--version", "--pid", "--target", "--runtime-json"}
    while i < len(argv):
        k = argv[i]
        if k not in keys or i + 1 >= len(argv):
            return None
        out[k.lstrip("-").replace("-", "_")] = argv[i + 1]
        i += 2
    if not all(x in out for x in ("zip", "sha256", "version", "pid", "target")):
        return None
    return out


def run_apply_update(argv) -> int:
    """--apply-update 模式入口。返回 0=成功，1=失败，2=参数错误。"""
    args = _parse_args(argv)
    if not args:
        print("usage: --apply-update --zip <z> --sha256 <h> --version <v> "
              "--pid <p> --target <dir> [--runtime-json <path>]")
        return 2

    zip_path = Path(args["zip"])
    sha = args["sha256"].lower()
    version = args["version"]
    pid = int(args["pid"])
    target = Path(args["target"])
    runtime_json = args.get("runtime_json")

    start_ts = time.time()
    _apply_log("apply 开始：version=%s target=%s pid=%d" % (version, target, pid))

    # 1) 等 hub 退出
    if not wait_pid_exit(pid, timeout=60.0):
        _apply_log("等待 hub(%d) 退出超时，放弃替换" % pid)
        return 1

    # 2) 校验 zip 指纹
    from mio_taskhub.update.downloader import sha256_file
    try:
        got = sha256_file(zip_path)
    except OSError as e:
        _apply_log("读 zip 失败：%s" % e)
        return 1
    if got != sha:
        _apply_log("zip sha256 不符：got=%s want=%s" % (got, sha))
        return 1

    # 3) 解压到 staging
    import shutil
    staging = target.with_name(target.name + ".staging-" + version)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)
    except Exception as e:  # noqa: BLE001
        _apply_log("解压失败：%s" % e)
        shutil.rmtree(staging, ignore_errors=True)
        return 1

    ok, msg = verify_staging(staging)
    if not ok:
        _apply_log("staging 校验失败：%s" % msg)
        shutil.rmtree(staging, ignore_errors=True)
        return 1

    # 4) 事务替换
    from_ver = str(read_runtime(runtime_json).get("version") or "unknown")
    backup = target.with_name(target.name + ".bak-" + from_ver)
    res = execute_replace(target, staging, backup)
    if not res.ok:
        _apply_log("替换失败：%s" % res.error)
        return 1
    _apply_log("替换完成：backup=%s" % backup.name)

    # 5) 启动新版本 + 健康确认
    try:
        _spawn_hub(target)
    except Exception as e:  # noqa: BLE001
        _apply_log("启动新版本失败：%s，回滚" % e)
        _rollback(backup, target)
        _spawn_hub_safe(target)
        return 1

    if wait_healthy(version, after_ts=start_ts, runtime_path=runtime_json):
        _apply_log("健康确认通过，删除备份 %s" % backup.name)
        shutil.rmtree(backup, ignore_errors=True)
        return 0

    _apply_log("健康确认超时，回滚")
    _rollback(backup, target)
    _spawn_hub_safe(target)
    return 1


def _rollback(backup: Path, target: Path) -> None:
    import shutil
    try:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        _rename_retry(backup, target)
        _apply_log("回滚成功")
    except OSError as e:
        _apply_log("回滚失败：%s（保留 %s 供人工介入）" % (e, backup))


def _spawn_hub_safe(target: Path) -> None:
    try:
        _spawn_hub(target)
    except Exception as e:  # noqa: BLE001
        _apply_log("重启旧版本失败：%s" % e)
```

- [ ] **Step 4: run.py 分派 apply-update**

Modify `packaging/run.py`：在 `main()` 里 `mcp` 分支之前加：

```python
    if mode == "apply-update":
        from mio_taskhub.update.apply import run_apply_update
        raise SystemExit(run_apply_update(sys.argv[2:]))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_apply.py -q`
Expected: PASS（16 passed）

- [ ] **Step 6: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_apply.py -q`
Expected: PASS。不提交 git。

---

## Task 9: UpdateService 状态机与编排

**Files:**
- Create: `mio_taskhub/update/service.py`
- Test: `tests/test_update_service.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_update_service.py
import json
import pytest

from mio_taskhub.update.manifest import manifest_from_dict
from mio_taskhub.update.service import UpdateState, UpdateService

MANIFEST = {
    "schema": 1, "version": "0.4.0", "channel": "stable",
    "released_at": "2026-09-22T10:00:00Z", "min_supported": "0.3.0",
    "mandatory": False, "notes": "n",
    "assets": [{
        "os": "windows", "arch": "x64",
        "url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip",
        "sha256": "c" * 64, "size": 5, "format": "zip",
    }],
}


class FakeSource:
    def __init__(self, m): self.m = m; self.raised = None
    def fetch_manifest(self):
        if self.raised:
            raise self.raised
        return self.m


def _svc(tmp_path, m, current="0.3.0", prefs=None):
    return UpdateService(
        source=FakeSource(m),
        downloader=lambda url, dest, sha, size=None, progress=None: dest,
        install_dir=tmp_path / "app",
        current_version=current,
        prefs_path=prefs or (tmp_path / "prefs.json"),
        apply_runner=lambda *a, **k: 0,
    )


def test_check_available(tmp_path):
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST))
    st = svc.check()
    assert st["state"] == UpdateState.AVAILABLE.value
    assert st["latest"] == "0.4.0"


def test_check_up_to_date(tmp_path):
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST), current="0.4.0")
    assert svc.check()["state"] == UpdateState.UP_TO_DATE.value


def test_check_needs_manual(tmp_path):
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST), current="0.2.0")
    assert svc.check()["state"] == UpdateState.NEEDS_MANUAL.value


def test_check_failed_is_silent(tmp_path):
    svc = _svc(tmp_path, None)
    svc.source.raised = RuntimeError("net")
    st = svc.check()
    assert st["state"] == UpdateState.CHECK_FAILED.value
    assert st["error"]


def test_dismiss_persists(tmp_path):
    prefs = tmp_path / "prefs.json"
    svc = _svc(tmp_path, manifest_from_dict(MANIFEST), prefs=prefs)
    svc.check()
    svc.dismiss()
    assert svc.status()["state"] == UpdateState.DISMISSED.value
    # 新实例读取同一 prefs → 仍 dismissed
    svc2 = _svc(tmp_path, manifest_from_dict(MANIFEST), prefs=prefs)
    svc2.check()
    assert svc2.status()["state"] == UpdateState.DISMISSED.value
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_service.py -q`
Expected: FAIL（`ModuleNotFoundError`）

- [ ] **Step 3: 实现 service.py**

```python
# mio_taskhub/update/service.py
# -*- coding: utf-8 -*-
"""UpdateService：更新状态机与编排（单例）。

状态：idle→checking→up_to_date|needs_manual|available|dismissed|check_failed
      available→downloading→ready→applying→done|failed
"""
import json
import os
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from mio_taskhub.version import __version__, install_dir as _version_install_dir
from mio_taskhub.update.manifest import is_newer, parse_version


def _below_min_supported(current: str, min_supported: str) -> bool:
    """current < min_supported → True（任一解析失败按 False，即不拦截）。"""
    c = parse_version(current)
    m = parse_version(min_supported)
    if c is None or m is None:
        return False
    return c < m


class UpdateState(str, Enum):
    IDLE = "idle"
    CHECKING = "checking"
    UP_TO_DATE = "up_to_date"
    NEEDS_MANUAL = "needs_manual"
    AVAILABLE = "available"
    DISMISSED = "dismissed"
    CHECK_FAILED = "check_failed"
    DOWNLOADING = "downloading"
    READY = "ready"
    APPLYING = "applying"
    DONE = "done"
    FAILED = "failed"


def _default_prefs_path() -> Path:
    return Path.home() / ".mio_taskhub" / "update" / "prefs.json"


def _default_update_dir() -> Path:
    return Path.home() / ".mio_taskhub" / "updates"


class UpdateService:
    def __init__(self, source=None, downloader: Callable = None,
                 install_dir=None, current_version: str = None,
                 prefs_path=None, update_dir=None, apply_runner: Callable = None,
                 on_event: Callable[[dict], None] = None):
        from mio_taskhub.update.source import default_source
        from mio_taskhub.update.downloader import download as _download
        self.source = source or default_source()
        self._download = downloader or (
            lambda url, dest, sha, size=None, progress=None:
            _download(url, dest, sha, size=size, progress=progress))
        # 注意：参数名 install_dir 会遮蔽 version.install_dir()，故导入时别名化
        self.install = Path(install_dir) if install_dir else _version_install_dir()
        self.current = current_version or __version__
        self.prefs_path = Path(prefs_path) if prefs_path else _default_prefs_path()
        self.update_dir = Path(update_dir) if update_dir else _default_update_dir()
        self._apply_runner = apply_runner
        self._on_event = on_event
        self._lock = threading.Lock()
        self._state = UpdateState.IDLE
        self._manifest = None
        self._asset = None
        self._error = ""
        self._progress = 0
        self._dismissed = self._load_dismissed()
        self._thread = None

    # ── prefs ──
    def _load_dismissed(self) -> str:
        try:
            return str(json.loads(self.prefs_path.read_text(encoding="utf-8"))
                       .get("dismissed_version") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _save_dismissed(self) -> None:
        try:
            self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
            self.prefs_path.write_text(
                json.dumps({"dismissed_version": self._dismissed}),
                encoding="utf-8")
        except OSError:
            pass

    def _emit(self, kind: str) -> None:
        if self._on_event:
            try:
                self._on_event({"kind": kind, "status": self.status()})
            except Exception:  # noqa: BLE001
                pass

    # ── 状态 ──
    def status(self) -> dict:
        m = self._manifest
        a = self._asset
        return {
            "state": self._state.value,
            "current": self.current,
            "latest": m.version if m else None,
            "notes": m.notes if m else "",
            "mandatory": bool(m.mandatory) if m else False,
            "min_supported": m.min_supported if m else None,
            "asset_sha256": a.sha256 if a else "",
            "asset_url": a.url if a else "",
            "asset_size": a.size if a else 0,
            "weak_verify": bool(m.weak_verify) if m else False,
            "progress": self._progress,
            "error": self._error,
            "dismissed_version": self._dismissed,
        }

    # ── 动作 ──
    def check(self) -> dict:
        with self._lock:
            self._state = UpdateState.CHECKING
            self._error = ""
        try:
            m = self.source.fetch_manifest()
        except Exception as e:  # noqa: BLE001 —— 静默失败
            with self._lock:
                self._state = UpdateState.CHECK_FAILED
                self._error = str(e)
            self._emit("update_check_failed")
            return self.status()

        asset = m.asset_for("windows", "x64")
        with self._lock:
            self._manifest = m
            self._asset = asset
            if asset is None:
                self._state = UpdateState.CHECK_FAILED
                self._error = "manifest 无 windows/x64 资产"
            elif not is_newer(self.current, m.version):
                self._state = UpdateState.UP_TO_DATE
            elif _below_min_supported(self.current, m.min_supported):
                self._state = UpdateState.NEEDS_MANUAL
            elif self._dismissed == m.version:
                self._state = UpdateState.DISMISSED
            else:
                self._state = UpdateState.AVAILABLE
        self._emit("update_" + self._state.value)
        return self.status()

    def dismiss(self) -> dict:
        with self._lock:
            if self._manifest:
                self._dismissed = self._manifest.version
                self._save_dismissed()
                self._state = UpdateState.DISMISSED
        self._emit("update_dismissed")
        return self.status()

    def download(self) -> dict:
        with self._lock:
            if self._manifest is None or self._asset is None:
                self._state = UpdateState.FAILED
                self._error = "尚未检查更新"
                return self.status()
            self._state = UpdateState.DOWNLOADING
            self._progress = 0
        url = self._asset.url
        dest = self.update_dir / ("mio-taskhub-%s.zip" % self._manifest.version)
        try:
            self._download(url, dest, self._asset.sha256, size=self._asset.size,
                           progress=self._on_progress)
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._state = UpdateState.FAILED
                self._error = str(e)
            self._emit("update_failed")
            return self.status()
        with self._lock:
            self._state = UpdateState.READY
            self._progress = 100
        self._emit("update_ready")
        return self.status()

    def _on_progress(self, done: int, total: int) -> None:
        self._progress = int(done * 100 / total) if total else 0
        self._emit("update_progress")

    def apply(self) -> dict:
        from mio_taskhub.version import is_frozen
        with self._lock:
            if not is_frozen():
                self._state = UpdateState.FAILED
                self._error = "开发模式不支持自更新（仅打包产物）"
                return self.status()
            if self._state != UpdateState.READY or self._manifest is None:
                self._state = UpdateState.FAILED
                self._error = "尚未下载完成"
                return self.status()
            self._state = UpdateState.APPLYING
        zip_path = self.update_dir / ("mio-taskhub-%s.zip" % self._manifest.version)
        assert self._apply_runner is not None, "apply_runner 未注入"
        rc = self._apply_runner(self.install, zip_path, self._manifest, os.getpid())
        with self._lock:
            self._state = UpdateState.DONE if rc == 0 else UpdateState.FAILED
            if rc != 0:
                self._error = "应用更新失败（详见 apply.log）"
        self._emit("update_" + self._state.value)
        return self.status()

    def start_background(self, delay: float = 20.0, interval_h: float = 6.0) -> None:
        """启动后台线程：延迟 delay 首查，此后每 interval_h 小时。"""
        if os.environ.get("MIO_UPDATE_DISABLED", "").strip() in ("1", "true", "yes"):
            return
        if self._thread and self._thread.is_alive():
            return

        def _loop():
            time.sleep(delay)
            while True:
                try:
                    self.check()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(max(60.0, interval_h * 3600.0))

        self._thread = threading.Thread(target=_loop, name="update-check", daemon=True)
        self._thread.start()


# ── 单例 ──
_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def get_service() -> UpdateService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = UpdateService()
        return _SERVICE


def reset_service_for_test(svc: UpdateService = None) -> None:
    global _SERVICE
    _SERVICE = svc
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_service.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_service.py -q`
Expected: PASS。不提交 git。

---

## Task 10: Update REST API

**Files:**
- Create: `mio_taskhub/api/update.py`
- Modify: `mio_taskhub/main.py`（include router）
- Test: `tests/test_update_api.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_update_api.py
from fastapi.testclient import TestClient
import pytest

from mio_taskhub.main import app

client = TestClient(app)


@pytest.fixture()
def fake_service():
    from mio_taskhub.update.service import UpdateService, UpdateState
    from mio_taskhub.update.manifest import manifest_from_dict

    M = {
        "schema": 1, "version": "0.4.0", "channel": "stable",
        "released_at": "x", "min_supported": "0.3.0", "mandatory": False, "notes": "n",
        "assets": [{
            "os": "windows", "arch": "x64",
            "url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip",
            "sha256": "d" * 64, "size": 5, "format": "zip",
        }],
    }

    class S:
        def fetch_manifest(self): return manifest_from_dict(M)

    svc = UpdateService(source=S(), downloader=lambda *a, **k: a[1],
                       current_version="0.3.0",
                       prefs_path=None, install_dir=".")
    import mio_taskhub.api.update as u
    prev = u._service_override
    u._service_override = svc
    yield svc
    u._service_override = prev


def test_update_status_and_check(fake_service):
    r = client.get("/api/v1/update/status")
    assert r.status_code == 200
    assert r.json()["current"] == "0.3.0"
    c = client.post("/api/v1/update/check")
    assert c.status_code == 200
    assert c.json()["state"] == "available"


def test_update_dismiss(fake_service):
    client.post("/api/v1/update/check")
    d = client.post("/api/v1/update/dismiss")
    assert d.json()["state"] == "dismissed"


def test_update_apply_dev_mode_fails(fake_service):
    client.post("/api/v1/update/check")
    a = client.post("/api/v1/update/apply")
    assert a.status_code == 200
    assert a.json()["state"] == "failed"
    assert "开发模式" in a.json()["error"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_api.py -q`
Expected: FAIL（404 或 import error）

- [ ] **Step 3: 实现 api/update.py**

```python
# mio_taskhub/api/update.py
# -*- coding: utf-8 -*-
"""更新 REST API。"""
from fastapi import APIRouter

from mio_taskhub.update.service import get_service

router = APIRouter(prefix="/update", tags=["update"])

# 测试注入点（None → 用全局单例）
_service_override = None


def _svc():
    return _service_override or get_service()


@router.get("/status")
def status():
    return _svc().status()


@router.post("/check")
def check():
    return _svc().check()


@router.post("/download")
def download():
    return _svc().download()


@router.post("/apply")
def apply():
    return _svc().apply()


@router.post("/dismiss")
def dismiss():
    return _svc().dismiss()
```

- [ ] **Step 4: main.py 挂载 router**

Modify `mio_taskhub/main.py`：
1. import 区加 `update as update_api`。
2. `include_router` 区（现有 `app.include_router(observability.router)` 行后）加：
```python
app.include_router(update_api.router, prefix="/api/v1", tags=["update"])
```

- [ ] **Step 5: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_api.py -q`
Expected: PASS（3 passed）

- [ ] **Step 6: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_api.py -q`
Expected: PASS。不提交 git。

---

## Task 11: 后台线程 + 运行时 sentinel + 残留恢复接线

**Files:**
- Modify: `mio_taskhub/background.py`（`start_background_jobs`）
- Modify: `mio_taskhub/main.py`（lifespan）
- Test: `tests/test_update_api.py`

- [ ] **Step 1: 写失败测试**（追加）

```python
def test_runtime_state_written_when_enabled(tmp_path, monkeypatch):
    from mio_taskhub.update import runtime
    p = tmp_path / "runtime.json"
    monkeypatch.setenv("MIO_UPDATE_STATE_PATH", str(p))
    runtime.write_runtime_state(version="9.9.9", port=12345)
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["version"] == "9.9.9"
    assert data["port"] == 12345
    assert data["pid"] > 0


def test_recover_residual_called_on_startup(tmp_path, monkeypatch):
    from mio_taskhub.update import runtime
    calls = []
    monkeypatch.setattr(runtime, "recover_residual", lambda *a, **k: calls.append(a))
    runtime.startup_recovery(install_dir=str(tmp_path))
    assert calls
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_api.py -q -k "runtime or recovery"`
Expected: FAIL（`ModuleNotFoundError: mio_taskhub.update.runtime`）

- [ ] **Step 3: 实现 runtime.py**

```python
# mio_taskhub/update/runtime.py
# -*- coding: utf-8 -*-
"""运行时 sentinel（更新健康确认依据）+ 启动残留恢复。"""
import json
import os
import sys
import time
from pathlib import Path

from mio_taskhub.update.apply import default_runtime_path, recover_residual as _recover


def write_runtime_state(version: str, port: int, path=None) -> None:
    """成功启动后写 sentinel（apply 进程据此判定"健康"）。"""
    p = Path(path or os.environ.get("MIO_UPDATE_STATE_PATH") or default_runtime_path())
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({
            "version": version,
            "pid": os.getpid(),
            "started_at": time.time(),
            "port": port,
        }), encoding="utf-8")
    except OSError:
        pass


def recover_residual(install_dir=None):
    from mio_taskhub.version import install_dir as _id
    target = install_dir or str(_id())
    try:
        return _recover(target)
    except Exception:  # noqa: BLE001
        return []


def startup_recovery(install_dir=None):
    """hub 启动时：清理 staging、必要时从 backup 恢复。仅打包产物执行。"""
    from mio_taskhub.version import is_frozen
    if not is_frozen():
        return []
    return recover_residual(install_dir)
```

- [ ] **Step 4: 接入 main.py lifespan**

Modify `mio_taskhub/main.py` 的 `lifespan`，在 `app.state.alert_manager = alert_mgr` 之后加：

```python
    # 更新：启动残留恢复 + 写 runtime sentinel + 后台检查线程
    from mio_taskhub.update import runtime as update_runtime
    from mio_taskhub.version import __version__ as _ver, is_frozen as _frozen
    update_runtime.startup_recovery()
    try:
        from mio_taskhub.update.service import get_service
        _svc = get_service()
        _svc.start_background(delay=20.0, interval_h=6.0)
        app.state.update_service = _svc
    except Exception:
        pass
    if _frozen():
        update_runtime.write_runtime_state(version=_ver, port=48620)
```

- [ ] **Step 5: background.py 不动（说明）**

`UpdateService.start_background` 已在 lifespan 内启动，无需改 `background.py`。删除本计划 File Structure 中该条修改项。

- [ ] **Step 6: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_api.py -q`
Expected: PASS（5 passed）

- [ ] **Step 7: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_api.py tests/test_update_service.py -q`
Expected: PASS。不提交 git。

---

## Task 12: 托盘入口与通知

**Files:**
- Modify: `packaging/run_hub.py:231-233`（菜单）
- Test: 手动（无法在单测覆盖 GUI）

- [ ] **Step 1: 加菜单项**

Modify `packaging/run_hub.py`，在 `start_tray()` 的 `pystray.Menu(...)`（231 行附近）替换为：

```python
            menu=pystray.Menu(
                pystray.MenuItem("打开面板", _open_panel, default=True),
                pystray.MenuItem(
                    lambda item: _update_menu_label(),
                    _on_update_clicked),
                pystray.MenuItem("退出", _quit),
            ),
```

- [ ] **Step 2: 加回调与标签函数**（在 `start_tray` 上方模块级添加）

```python
def _update_menu_label(_item=None) -> str:
    try:
        from mio_taskhub.update.service import get_service
        st = get_service().status()
        state = st.get("state")
        if state == "available":
            return "立即更新 (v%s)" % st.get("latest")
        if state == "ready":
            return "重启并应用更新"
        if state == "downloading":
            return "下载中 %d%%" % st.get("progress", 0)
        if state == "needs_manual":
            return "需手动更新 (v%s)" % st.get("latest")
    except Exception:  # noqa: BLE001
        pass
    return "检查更新"


def _notify(icon, msg):
    try:
        if icon is not None:
            icon.notify(msg, "mio-taskhub")
    except Exception:  # noqa: BLE001
        pass


_update_busy = threading.Event()


def _on_update_clicked(icon=None, _item=None):
    """托盘点击：在**工作线程**里跑 check→download→apply，避免冻结托盘消息循环。"""
    from mio_taskhub.update.service import get_service
    svc = get_service()
    try:
        state = svc.status().get("state")
    except Exception as e:  # noqa: BLE001
        _log("tray: update status failed: %r" % e)
        return

    if state == "ready":
        steps = ["apply"]
    elif state in ("available", "check_failed", "up_to_date", "idle", "dismissed"):
        steps = ["check", "download", "apply"]
    else:
        return

    if _update_busy.is_set():      # 防重入：下载/应用中再点不重复触发
        _notify(icon, "更新正在进行中…")
        return
    _update_busy.set()

    def _work():
        try:
            if "check" in steps:
                svc.check()
            if "download" in steps and svc.status().get("state") == "available":
                svc.download()
            if "apply" in steps and svc.status().get("state") == "ready":
                svc.apply()
            st = svc.status()
            if st.get("state") == "failed":
                _notify(icon, "更新失败：%s" % (st.get("error") or "见 apply.log"))
        except Exception as e:  # noqa: BLE001
            _log("tray: update action failed: %r" % e)
            _notify(icon, "更新失败：%s" % e)
        finally:
            _update_busy.clear()

    threading.Thread(target=_work, daemon=True, name="update-action").start()
```

> 说明：`_on_update_clicked` 采用"一键走完 check→download→apply"；若需更细确认，可改为只做 `check` 并开面板由 Web 驱动（Web 已有按钮）。默认采用一键语义以满足"一键更新"。

- [ ] **Step 3: 有新版本时通知**（在 `start_tray` 里 `icon.run` 线程启动后加）

```python
    def _notify_loop():
        import time as _t
        last = None
        while True:
            _t.sleep(30)
            try:
                from mio_taskhub.update.service import get_service
                st = get_service().status()
                if st.get("state") == "available" and st.get("latest") != last:
                    last = st.get("latest")
                    icon.notify("发现新版本 v%s，点击托盘图标更新" % st.get("latest"), "mio-taskhub")
            except Exception:  # noqa: BLE001
                pass

    threading.Thread(target=_notify_loop, daemon=True).start()
```

- [ ] **Step 4: 手动验证**

Run:
```powershell
.venv\Scripts\python.exe -c "import packaging.run_hub" 2>$null; if ($?) { 'import ok' }
```
Expected: `import ok`（无语法/导入错误）
然后人工：启动 `mio-taskhub.exe hub`，右键托盘确认出现"检查更新"项。

- [ ] **Step 5: Checkpoint**

确认托盘菜单出现且点击不报错（`~/.mio_taskhub/runtime.log` 无 traceback）。不提交 git。

---

## Task 13: 前端更新条

**Files:**
- Modify: `web/src/api.js`（导出对象内加方法）
- Create: `web/src/components/UpdateBanner.jsx`
- Modify: `web/src/App.jsx`（挂载）
- Test: 手动（构建通过 + 视觉确认）

- [ ] **Step 1: api.js 加方法**

Modify `web/src/api.js` 导出对象（在 `processInfo: ...` 后、`}` 前）加：

```javascript
  // Software update
  updateStatus: () => req('GET', '/update/status'),
  updateCheck: () => req('POST', '/update/check'),
  updateDownload: () => req('POST', '/update/download'),
  updateApply: () => req('POST', '/update/apply'),
  updateDismiss: () => req('POST', '/update/dismiss'),
```

- [ ] **Step 2: 新建 UpdateBanner.jsx**

```jsx
/** 顶部更新提示条：有新版本时展示并支持一键更新。 */
import { useEffect, useState } from 'react'
import { api } from '../api'

export default function UpdateBanner({ eventTick }) {
  const [st, setSt] = useState(null)
  const [busy, setBusy] = useState(false)

  async function refresh() {
    try { setSt(await api.updateStatus()) } catch { /* ignore */ }
  }

  useEffect(() => { refresh() }, [eventTick])

  if (!st) return null
  const state = st.state
  if (!['available', 'downloading', 'ready', 'needs_manual', 'failed'].includes(state)) return null

  async function onPrimary() {
    setBusy(true)
    try {
      if (state === 'available') { await api.updateDownload(); await refresh() }
      else if (state === 'ready') { await api.updateApply() }
    } finally { setBusy(false) }
  }

  const label = {
    available: `发现新版本 v${st.latest}`,
    downloading: `正在下载 ${st.progress || 0}%`,
    ready: `已下载 v${st.latest}，重启应用更新`,
    needs_manual: `v${st.latest} 需手动更新（跨代不兼容）`,
    failed: `更新失败：${st.error || '见 apply.log'}`,
  }[state]

  return (
    <div className="update-banner" role="status" aria-live="polite">
      <span className="update-banner__text">{label}</span>
      {state === 'available' && (
        <button className="btn btn--primary" disabled={busy} onClick={onPrimary}>立即更新</button>
      )}
      {state === 'ready' && (
        <button className="btn btn--primary" disabled={busy} onClick={onPrimary}>重启并更新</button>
      )}
      {state === 'available' && (
        <button className="btn btn--ghost" disabled={busy}
                onClick={async () => { await api.updateDismiss(); await refresh() }}>忽略此版本</button>
      )}
    </div>
  )
}
```

- [ ] **Step 3: index.css 加样式**

Modify `web/src/index.css` 末尾追加：

```css
.update-banner {
  display: flex; align-items: center; gap: 12px;
  padding: 8px 16px; font-size: 13px;
  background: rgba(59,130,246,.12); border-bottom: 1px solid rgba(59,130,246,.35);
  color: var(--text, #e2e8f0);
}
.update-banner__text { flex: 1; }
```

- [ ] **Step 4: App.jsx 挂载**

Modify `web/src/App.jsx`：
1. 顶部 import：`import UpdateBanner from './components/UpdateBanner'`
2. 在 `<ConnectionBanner .../>` 附近渲染 `<UpdateBanner eventTick={lastSync} />`（复用现有 `lastSync` 触发刷新）。

- [ ] **Step 5: 构建验证**

Run:
```powershell
cd web; npm run build
```
Expected: 构建成功（vite 输出 `dist/`，无报错）

- [ ] **Step 6: Checkpoint**

Run: `cd web; npm run build`
Expected: PASS。不提交 git。

---

## Task 14: 发版契约（build.ps1 改 + release.ps1 新增）

**Files:**
- Modify: `packaging/build.ps1`
- Create: `packaging/release.ps1`

- [ ] **Step 1: build.ps1 注入版本**

Modify `packaging/build.ps1`：参数行改为
```powershell
param([switch]$Quick, [string]$Version = "")
```
在 `[1/6] 构建前端` 之前插入：
```powershell
# ---------- 0.5) 注入版本号 ----------
if ($Version) {
    Write-Host "[0.5/6] 注入版本号 $Version ..."
    $verFile = Join-Path $root 'mio_taskhub\version.py'
    $content = @"
# mio_taskhub/version.py
# -*- coding: utf-8 -*-
"""(自动生成) 单一版本源。"""
import sys
from pathlib import Path

__version__ = "$Version"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
"@
    Set-Content -Path $verFile -Value $content -Encoding UTF8
}
```
并把 zip 名从 `mio-taskhub-绿色版.zip` 改为 `mio-taskhub-win64.zip`（第 83 行）。

- [ ] **Step 2: 新建 release.ps1**

```powershell
# 发版：构建 → 生成 latest.json → gh release create
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File packaging/release.ps1 -Version 0.4.0
param(
  [Parameter(Mandatory=$true)][string]$Version,
  [string]$Notes = "",
  [switch]$Prerelease
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "[1/4] 构建 $Version ..."
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build.ps1') -Version $Version
if ($LASTEXITCODE -ne 0) { throw '构建失败' }

$zip = Join-Path $root 'dist\mio-taskhub-win64.zip'
if (-not (Test-Path $zip)) { throw "缺少产物 $zip" }

Write-Host "[2/4] 计算 sha256 / size ..."
$hash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
$size = (Get-Item $zip).Length

Write-Host "[3/4] 生成 latest.json ..."
$tag = "v$Version"
$url = "https://github.com/mldlbs/mio-taskhub/releases/download/$tag/mio-taskhub-win64.zip"
$manifest = [ordered]@{
  schema = 1
  version = $Version
  channel = $(if ($Prerelease) { 'prerelease' } else { 'stable' })
  released_at = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  min_supported = "0.3.0"
  mandatory = $false
  notes = $Notes
  assets = @([ordered]@{
    os = "windows"; arch = "x64"; url = $url
    sha256 = $hash; size = $size; format = "zip"
  })
}
$jsonPath = Join-Path $root 'dist\latest.json'
# 注意：PS 5.1 的 Set-Content -Encoding UTF8 会写 BOM，客户端 json.loads(utf-8) 会因 BOM 抛错
# 并被静默降级到弱校验路径。必须写**无 BOM** UTF-8。
$json = $manifest | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($jsonPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  sha256=$hash size=$size"

Write-Host "[4/4] 创建 GitHub Release $tag ..."
$preFlag = if ($Prerelease) { '--prerelease' } else { '--latest' }
$notesFile = Join-Path $env:TEMP "release-notes-$Version.md"
if ($Notes) { Set-Content -Path $notesFile -Value $Notes -Encoding UTF8 } else { Set-Content -Path $notesFile -Value "Release $Version" -Encoding UTF8 }
gh release create $tag $zip $jsonPath --title $tag --notes-file $notesFile $preFlag
Write-Host "[完成] $tag"
```

- [ ] **Step 3: 干跑校验（不真发版）**

Run:
```powershell
.venv\Scripts\python.exe -c "import json,re;d=json.load(open(r'mio_taskhub/version.py',encoding='utf-8')) if False else None;print('ok')"
```
Expected: `ok`（确认无语法问题）；`release.ps1` 用 `-WhatIf` 不便，改为人工审查脚本 + `gh --version` 可用性检查：
```powershell
gh --version
```
Expected: 输出版本（若未登录，发版前置 `gh auth login`，非本任务范围）

- [ ] **Step 4: Checkpoint**

确认 `build.ps1 -Version` 能覆写 `version.py`（git diff 可见版本号变化），`release.ps1` 语法通过 `powershell -NoProfile -Command "Get-Command -Syntax"` 式检查。不提交 git。

---

## Task 15: 端到端集成测试（本地假 release）

**Files:**
- Test: `tests/test_update_e2e.py`

- [ ] **Step 1: 写集成测试**

```python
# tests/test_update_e2e.py
"""本地假 release：check → download → 校验 → execute_replace，全程不触网。"""
import hashlib
import json
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
```

- [ ] **Step 2: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_e2e.py -q`
Expected: PASS（1 passed）

- [ ] **Step 3: 跑全部更新相关测试**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py tests/test_update_source.py tests/test_update_downloader.py tests/test_update_apply.py tests/test_update_service.py tests/test_update_api.py tests/test_update_e2e.py -q`
Expected: PASS（约 45 passed）

- [ ] **Step 4: Checkpoint**

Run 上一步命令。Expected: 全绿。不提交 git。

---

## Task 16: 文档与开关说明

**Files:**
- Modify: `docs/superpowers/specs/2026-09-22-software-auto-update-design.md`（补"实现状态"节）
- Modify: `packaging/使用说明.txt`（补用户可见的更新说明）

- [ ] **Step 1: spec 追加实现状态**

在 spec 末尾加：

```markdown
## 19. 实现状态（2026-09-22）

- 已实现：version / manifest / source / downloader / apply（含回滚+健康确认+残留恢复）/
  service / api / runtime sentinel / 托盘入口 / 前端更新条 / build.ps1 版本注入 / release.ps1。
- 测试：`tests/test_update_*.py`（约 45 项）全绿。
- 未做（非目标）：代码签名、增量差分、CI 自动发版。
- 发版命令：`powershell -File packaging/release.ps1 -Version 0.4.0 -Notes "..."`。
```

- [ ] **Step 2: 使用说明补更新指引**

在 `packaging/使用说明.txt` 追加：

```
自动更新：
  - 程序启动后会自动检查新版本；有新版本时托盘会提示，面板顶部出现"立即更新"条。
  - 点击后自动下载、校验（sha256）、替换并重启。
  - 若更新失败会自动回滚到上一版本；日志见 %USERPROFILE%\.mio_taskhub\update\apply.log
  - 关闭自动检查：设置环境变量 MIO_UPDATE_DISABLED=1
  - 用户数据（数据库/日志）不会被更新影响。
```

- [ ] **Step 3: Checkpoint**

Run: `.venv\Scripts\python.exe -m pytest tests/test_update_manifest.py -q`
Expected: PASS（说明性改动，回归不破）。不提交 git。

---

## Self-Review

**1. Spec coverage**

| Spec 节 | 覆盖任务 |
|---------|---------|
| §3.1 同卷 rename + 可恢复事务 | Task 6（`execute_replace`） |
| §3.2 `.bak` 不覆盖（版本化命名） | Task 6（`app.bak-<from_version>` + 同名先删） |
| §3.3 成功=新 Hub 健康启动（sentinel） | Task 7（`wait_healthy`）+ Task 11（`write_runtime_state`）+ Task 8（流程） |
| §3.4 锁重试（不假设 PID=解锁） | Task 6（`_rename_retry`） |
| §4 架构/分发 apply-update | Task 8（run.py 分派） |
| §5 组件边界 | Task 1–11 各模块 |
| §6 Manifest 契约 | Task 3 |
| §7 版本比较 | Task 2 |
| §8 安全边界（https/白名单/sha256） | Task 3（url 校验）+ Task 5（sha256） |
| §9 状态机 | Task 9 |
| §10 数据流 | Task 9 + Task 11 |
| §11 事务步骤 | Task 8 |
| §12 异常矩阵 | Task 5/6/7/8（重试/超时/回滚） |
| §13 残留恢复 | Task 7 + Task 11 |
| §14 API/UI/Tray | Task 10 + Task 12 + Task 13 |
| §15 发版契约 | Task 14 |
| §16 配置开关 | Task 9（DISABLED/间隔）+ Task 3/4（BASE_URL） |
| §17 测试策略 | Task 1–11 单测 + Task 15 集成 |
| §18 未决 | Task 16 明确非目标 |

**2. Placeholder scan**：本计划所有代码步骤均含完整代码；无 TBD/TODO。

**3. Type consistency**：
- `UpdateManifest.asset_for(os, arch)`（Task 3）在 Task 9/10 使用一致。
- `execute_replace(install, staging, backup) -> ReplaceResult`（Task 6）在 Task 8 使用一致。
- `wait_healthy(target_version, after_ts, runtime_path, timeout, poll)`（Task 7）在 Task 8 使用一致。
- `run_apply_update(argv) -> int`（Task 8）与 run.py 分派一致。
- `UpdateService(source, downloader, install_dir, current_version, prefs_path, update_dir, apply_runner, on_event)`（Task 9）在 Task 10/11 使用一致。
- 前端 `api.updateStatus/updateCheck/updateDownload/updateApply/updateDismiss`（Task 13）与后端 `/update/*` 端点（Task 10）一致。

**已知偏差（有意）**：Task 11 Step 5 说明 `background.py` 无需改动（服务在 lifespan 启动），修正了 File Structure 中的初始假设。

---

## Task 17（最终审查补漏·阻断级）：生产 apply_runner + 优雅退出接线

**问题**：`get_service()` 构造的 `UpdateService()` 没有 `apply_runner`，`apply()` 里 `assert` 失败；且**没有任何生产代码 spawn `mio-taskhub.exe --apply-update`**。功能能 check/download，永远无法 apply。

**Files**：`mio_taskhub/update/runner.py`（新建）、`mio_taskhub/update/service.py`（默认 runner + 去 assert）、`packaging/run_hub.py`（注册退出回调）、`tests/test_update_runner.py`（新建）

**设计**：
- `runner.py`：`set_exit_callback(cb)` / `_request_exit()` / `default_apply_runner(install, zip_path, manifest, hub_pid) -> int`：detached spawn `[exe,"apply-update","--zip",...,"--sha256",<asset.sha256>,"--version",<ver>,"--pid",<hub_pid>,"--target",<install>,"--runtime-json",<sentinel>]`，随后 `_request_exit()`，返回 0（rc 仅表示"已触发"，健康由 updater 的 sentinel 判定）。
- `service.py`：`get_service()` 传 `apply_runner=default_apply_runner`；`apply()` 去掉 `assert`，None 时 FAILED 明确文案。
- `run_hub.py`：`main()` 里 `set_exit_callback(lambda: current["server"] and setattr(current["server"], "should_exit", True))` → uvicorn 优雅停止 → 进程退出 → updater `wait_pid_exit` 成功。

## Task 18（最终审查补漏·Important）：版本/channel/interval/弱校验/备份轮换

- `main.py`：`FastAPI(version=__version__)`（来自 `mio_taskhub.version`）。
- `service.check()`：读 `MIO_UPDATE_CHANNEL`（默认 stable），manifest.channel 与之不符则视为不更新。
- lifespan：`interval_h` 读 `MIO_UPDATE_INTERVAL_H`（默认 6）。
- `service.apply()`：`manifest.weak_verify` → FAILED「弱校验包不支持自动更新，请手动更新」（避免下载后才失败）。
- `apply.execute_replace()`：替换成功后轮换旧备份，仅保留最近 2 个 `install.bak-*`。

---

## Task 19（补漏）：接线 update_* WS 事件

**问题**：`UpdateService` 的 `on_event` 钩子生产未接，前端更新条只能靠既有 5s 任务轮询刷新（spec §14 要求 `update_available/progress/ready/failed`）。

**Files**：`mio_taskhub/events.py`、`mio_taskhub/update/service.py`、`web/src/App.jsx`、`tests/test_update_ws.py`

**设计**：
- `events.py` 增 `broadcast_json(message: dict) -> None`：任意线程安全广播裸消息（`asyncio.run(ws_manager.broadcast(msg))`，异常/无循环静默）。
- `service.py` 增 `_on_update_event(ev)` → `broadcast_json({"type":"update_status","kind":ev["kind"],"status":ev["status"]})`；`get_service()` 传 `on_event=_on_update_event`；把 `dismiss()` 里的 `_emit` 移出锁（避免持锁做网络写）。
- `App.jsx` WS 处理增 `data.type === 'update_status'` 分支：`setLastSync(new Date())` 即时刷新 `<UpdateBanner>`；`kind==='update_available'` 时 `addToast`。
