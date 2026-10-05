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
    注意：预发布标识符按字符串比较（1.0.0-rc.10 会排在 1.0.0-rc.2 之前），本系统只比较正式发布版本，够用。
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
    """解析并校验 latest.json。非法 → ManifestError。"""
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
