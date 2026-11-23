"""baseline_freeze 解析/组装/端到端的纯逻辑测试。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import baseline_freeze as bf


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


def test_run_pytest_retries_once_on_empty_stats(monkeypatch):
    """子进程偶发异常退出（rc!=0 且无摘要）→ 自动重试一次并留首次结果。"""
    calls = []
    seq = [
        {"rc": 4, "stats": {}, "coverage_total_pct": None,
         "summary_line": None, "tail": ["INTERNALERROR> coverage"]},
        {"rc": 0, "stats": {"passed": 3}, "coverage_total_pct": 88.0,
         "summary_line": "3 passed in 1.0s", "tail": []},
    ]

    def fake(repo, pytest_args=None):
        calls.append(pytest_args)
        return dict(seq[len(calls) - 1])

    monkeypatch.setattr(bf, "_run_pytest_once", fake)
    out = bf.run_pytest(bf.REPO, "tests/x.py -q")
    assert len(calls) == 2
    assert out["retried"] is True
    assert out["stats"]["passed"] == 3
    assert out["first_attempt"]["rc"] == 4
    assert out["first_attempt"]["tail"] == ["INTERNALERROR> coverage"]


def test_run_pytest_no_retry_when_first_attempt_ok(monkeypatch):
    calls = []

    def fake(repo, pytest_args=None):
        calls.append(pytest_args)
        return {"rc": 0, "stats": {"passed": 1}, "coverage_total_pct": 90.0,
                "summary_line": "1 passed in 1.0s", "tail": []}

    monkeypatch.setattr(bf, "_run_pytest_once", fake)
    out = bf.run_pytest(bf.REPO, "tests/x.py -q")
    assert len(calls) == 1
    assert "retried" not in out
    assert out["stats"]["passed"] == 1


def test_run_pytest_keeps_output_tail_for_diagnosis(monkeypatch):
    """stats 为空时 tail 必须留下，否则只剩 rc 无从定位。"""
    def fake(repo, pytest_args=None):
        return {"rc": 1, "stats": {}, "coverage_total_pct": None,
                "summary_line": None, "tail": ["boom"]}

    monkeypatch.setattr(bf, "_run_pytest_once", fake)
    out = bf.run_pytest(bf.REPO, "tests/x.py -q", retry=False)
    assert out["tail"] == ["boom"]
    assert out["stats"] == {}
