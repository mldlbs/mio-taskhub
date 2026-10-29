# -*- coding: utf-8 -*-
"""构造式修复基准：独立快照、基线失败、回归通过、参考修复通过。"""
from scripts import repair_benchmark as rb


def test_benchmark_is_constructible(tmp_path, monkeypatch):
    monkeypatch.setattr(
        rb, "material_binding",
        lambda db, cutoff=rb.CUTOFF: {"cutoff": cutoff, "available": 0,
                                      "ids_sha256": "x"})
    report = rb.audit(tmp_path / "bench", tmp_path / "unused.db")
    assert report["samples"] == 5
    assert report["unique_ids"] is True
    assert report["unique_snapshots"] is True
    assert report["all_constructible"] is True
    assert report["baseline_failure_classes"] == ["assertion"]
    for item in report["results"]:
        assert item["baseline_target"]["returncode"] != 0
        assert item["baseline_regression"]["returncode"] == 0
        assert item["gold_target"]["returncode"] == 0
        assert item["gold_regression"]["returncode"] == 0
