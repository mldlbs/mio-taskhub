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


def test_fetch_manifest_falls_back_on_invalid_latest_json():
    # latest.json 存在但内容非法 → 必须回退到 API，而不是直接抛错
    def opener(url):
        if url.endswith("latest.json"):
            return b"{}"  # 缺字段 → ManifestError → 回退
        return json.dumps(RELEASE_JSON).encode()
    src = GitHubReleaseSource(base_url="https://github.com/mldlbs/mio-taskhub", opener=opener)
    m = src.fetch_manifest()
    assert m.version == "0.5.0" and m.weak_verify is True


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
