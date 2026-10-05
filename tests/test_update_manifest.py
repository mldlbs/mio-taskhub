from pathlib import Path

import pytest

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


from mio_taskhub.update.manifest import parse_version, is_newer, is_valid_asset_url


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


def test_is_valid_asset_url_accepts_github():
    assert is_valid_asset_url(
        "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip") is True
    assert is_valid_asset_url("https://github.com") is True


def test_is_valid_asset_url_accepts_githubusercontent_subdomain():
    assert is_valid_asset_url("https://objects.githubusercontent.com/foo/bar.zip") is True


def test_is_valid_asset_url_rejects_http_and_foreign_hosts():
    assert is_valid_asset_url("http://github.com/x.zip") is False
    assert is_valid_asset_url("https://evil.com/x.zip") is False
    assert is_valid_asset_url("https://evilgithub.com/x.zip") is False
    assert is_valid_asset_url("https://evilgithubusercontent.com/x.zip") is False
    assert is_valid_asset_url("") is False
    assert is_valid_asset_url(None) is False


from mio_taskhub.update.manifest import (
    UpdateManifest, manifest_from_dict, ManifestError,
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


def test_manifest_rejects_non_numeric_size():
    bad = dict(GOOD, assets=[dict(GOOD["assets"][0], size="not-a-number")])
    with pytest.raises(ManifestError):
        manifest_from_dict(bad)


def test_manifest_rejects_infinite_size():
    bad = dict(GOOD, assets=[dict(GOOD["assets"][0], size=float("inf"))])
    with pytest.raises(ManifestError):
        manifest_from_dict(bad)


def test_manifest_rejects_float_size():
    bad = dict(GOOD, assets=[dict(GOOD["assets"][0], size=10.9)])
    with pytest.raises(ManifestError):
        manifest_from_dict(bad)


def test_manifest_rejects_missing_size():
    a = dict(GOOD["assets"][0]); a.pop("size")
    with pytest.raises(ManifestError):
        manifest_from_dict(dict(GOOD, assets=[a]))


def test_manifest_rejects_non_dict_asset():
    with pytest.raises(ManifestError):
        manifest_from_dict(dict(GOOD, assets=[42]))


def test_manifest_rejects_empty_assets():
    with pytest.raises(ManifestError):
        manifest_from_dict(dict(GOOD, assets=[]))


def test_manifest_rejects_bad_min_supported():
    with pytest.raises(ManifestError):
        manifest_from_dict(dict(GOOD, min_supported="nope"))


def test_manifest_rejects_bool_schema():
    with pytest.raises(ManifestError):
        manifest_from_dict(dict(GOOD, schema=True))


def test_manifest_mandatory_string_false_is_false():
    m = manifest_from_dict(dict(GOOD, mandatory="false"))
    assert m.mandatory is False
    m2 = manifest_from_dict(dict(GOOD, mandatory="true"))
    assert m2.mandatory is True


def test_manifest_weak_verify_false_when_sha_present():
    m = manifest_from_dict(GOOD)
    assert m.weak_verify is False
