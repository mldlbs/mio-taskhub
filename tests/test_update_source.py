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
        "digest": "sha256:" + "d" * 64,
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
    a = m.asset_for("windows", "x64")
    assert a.url.endswith("mio-taskhub-win64.zip")
    # 回退路径用 asset.digest → 仍可**强校验**（不再依赖 CDN latest.json）
    assert m.weak_verify is False and a.sha256 == "d" * 64


def test_fallback_without_digest_is_weak():
    no_digest = {
        "tag_name": "v0.5.0",
        "assets": [{
            "name": "mio-taskhub-win64.zip",
            "browser_download_url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.5.0/mio-taskhub-win64.zip",
            "size": 30,
        }],
    }

    def opener(url):
        if url.endswith("latest.json"):
            raise SourceError("HTTP 404")
        return json.dumps(no_digest).encode()

    src = GitHubReleaseSource(base_url="https://github.com/mldlbs/mio-taskhub", opener=opener)
    m = src.fetch_manifest()
    assert m.weak_verify is True
    assert m.asset_for("windows", "x64").sha256 == ""


def _opener_broken(url):
    raise SourceError("boom")


def test_fetch_manifest_propagates_error_when_both_fail():
    src = GitHubReleaseSource(base_url="https://github.com/mldlbs/mio-taskhub",
                              opener=_opener_broken)
    with pytest.raises(SourceError):
        src.fetch_manifest()


def test_direct_opener_disables_proxy(monkeypatch):
    """默认 opener 必须传入空 ProxyHandler（本机 WinINET/env 代理常是死的）。"""
    import urllib.request
    from mio_taskhub.update import source as s
    captured = {}
    real = urllib.request.build_opener

    def fake(*handlers):
        captured['handlers'] = handlers
        return real(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", fake)
    s._direct_opener()
    hs = captured.get('handlers', ())
    assert len(hs) == 1
    assert isinstance(hs[0], urllib.request.ProxyHandler) and hs[0].proxies == {}


def test_direct_opener_respects_use_proxy_env(monkeypatch):
    import urllib.request
    from mio_taskhub.update import source as s
    captured = {}
    real = urllib.request.build_opener

    def fake(*handlers):
        captured['handlers'] = handlers
        return real(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", fake)
    monkeypatch.setenv("MIO_UPDATE_USE_PROXY", "1")
    s._direct_opener()
    assert captured.get('handlers') == ()      # 走默认（含 env 代理）


def test_fetch_manifest_falls_back_on_invalid_latest_json():
    # latest.json 存在但内容非法 → 必须回退到 API，而不是直接抛错
    def opener(url):
        if url.endswith("latest.json"):
            return b"{}"  # 缺字段 → ManifestError → 回退
        return json.dumps(RELEASE_JSON).encode()
    src = GitHubReleaseSource(base_url="https://github.com/mldlbs/mio-taskhub", opener=opener)
    m = src.fetch_manifest()
    # 回退到 API（RELEASE_JSON 带 digest）→ 强校验
    assert m.version == "0.5.0" and m.weak_verify is False


def test_fetch_manifest_error_mentions_both_failures():
    def opener(url):
        raise SourceError("nope")
    src = GitHubReleaseSource(base_url="https://github.com/mldlbs/mio-taskhub", opener=opener)
    with pytest.raises(SourceError) as ei:
        src.fetch_manifest()
    assert "回退也失败" in str(ei.value)


def test_non_github_base_has_no_api_fallback():
    def opener(url):
        raise SourceError("nope")
    src = GitHubReleaseSource(base_url="http://internal.local/updates", opener=opener)
    with pytest.raises(SourceError):
        src.fetch_manifest()
