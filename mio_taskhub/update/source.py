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


def _direct_opener():
    """不走任何代理的 opener（ProxyHandler({})）。

    本机/CI 常配了 WinINET 或 env 代理（如 127.0.0.1:64681）且是死的；urllib 默认
    会读它们 → latest.json 取不到 → 静默降级到弱校验路径（丢 sha256、吃 API 限额）。
    需要走代理时设 MIO_UPDATE_USE_PROXY=1。
    """
    if (os.environ.get("MIO_UPDATE_USE_PROXY") or "").strip().lower() in ("1", "true", "yes"):
        return urllib.request.build_opener()
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _default_opener(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "mio-taskhub-updater"})
    with _direct_opener().open(req, timeout=timeout) as resp:
        return resp.read()


class GitHubReleaseSource:
    def __init__(self, base_url: str = None, timeout: float = 12.0, opener=None):
        # 12s：CDN 别名 latest.json 命中通常 1.5~6.5s；失败会挂到 20s+，
        # 用 12s 封顶后迅速回退到 API 路径（该路径有 digest，可强校验）。
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
        # GitHub Releases API 的 asset.digest 形如 "sha256:<hex>"：
        # 有了它，即使 CDN 别名 latest.json 取不到（本网络偶发），回退路径也能**强校验**。
        sha = ""
        digest = str(asset.get("digest") or "")
        if digest.startswith("sha256:"):
            sha = digest.split(":", 1)[1].strip().lower()
        pseudo = {
            "schema": 1, "version": tag, "channel": "stable",
            "released_at": str(rel.get("published_at") or ""),
            "min_supported": "0.0.0", "mandatory": False,
            "notes": str(rel.get("body") or ""),
            "assets": [{
                "os": "windows", "arch": "x64",
                "url": asset.get("browser_download_url"),
                "sha256": sha, "size": size, "format": "zip",
            }],
        }
        return manifest_from_dict(pseudo, require_sha=False)


def default_source() -> GitHubReleaseSource:
    return GitHubReleaseSource()
