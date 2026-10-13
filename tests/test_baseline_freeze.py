# -*- coding: utf-8 -*-
"""baseline_freeze 解析/组装/端到端的纯逻辑测试。"""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import baseline_freeze as bf  # noqa: E402


def test_parse_pytest_summary():
    stats = bf.parse_pytest_summary(
        "1276 passed, 1 skipped, 4 deselected, 2 warnings in 411.99s")
    assert stats["passed"] == 1276
    assert stats["skipped"] == 1
    assert stats["deselected"] == 4
    assert stats["warnings"] == 2
    assert stats["duration_seconds"] == 411.99


def test_parse_pytest_summary_failed():
    stats = bf.parse_pytest_summary("3 failed, 10 passed in 1.2s")
    assert stats["failed"] == 3 and stats["passed"] == 10


def test_parse_coverage_total():
    text = ("Name      Stmts  Miss  Cover\n"
            "mio_taskhub  100    5    95%\n"
            "TOTAL       100     5    95%\n")
    assert bf.parse_coverage_total(text) == 95.0
    assert bf.parse_coverage_total("no total here") is None


def test_env_defaults_shape():
    env = bf.env_defaults()
    assert set(env) == {"MIO_READ_GATE", "MIO_RATCHET_DISABLED"}
    for v in env.values():
        assert v["state"] in ("unset(default)", "overridden")
        assert "value" in v


def test_git_info_real_repo():
    info = bf.git_info(bf.REPO)
    assert "error" not in info
    assert len(info["head"]) == 40
    assert info["branch"]
    assert isinstance(info["dirty_count"], int)


def test_build_manifest_structure():
    m = bf.build_manifest(bf.REPO, skip_pytest=True)
    assert set(m) == {"generated_at", "tool", "python", "git",
                        "env_defaults", "pytest"}
    assert m["pytest"]["skipped"] is True


def test_end_to_end_small(tmp_path):
    tiny = tmp_path / "test_tiny.py"
    tiny.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    m = bf.build_manifest(bf.REPO, pytest_args=f"{tiny} -q")
    stats = m["pytest"]["stats"]
    assert stats.get("passed") == 1
    assert m["pytest"]["rc"] == 0
    assert m["pytest"]["coverage_total_pct"] is not None


def test_main_writes_manifest(tmp_path):
    out = tmp_path / "m.json"
    rc = bf.main(["--out", str(out),
                    "--pytest-args", "tests/test_analyze_reaper.py -q"])
    assert rc == 0
    m = json.loads(out.read_text(encoding="utf-8"))
    assert m["git"]["head"]
    assert m["pytest"]["stats"]["passed"] == 6
