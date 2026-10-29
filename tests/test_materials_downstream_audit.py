# -*- coding: utf-8 -*-
"""下游审计脚本的重复分组与功效计算。"""
from scripts.materials_downstream_audit import group_tasks, power_estimate


def test_group_tasks_normalizes_duplicates():
    rows = [
        {"title": "A  Task", "description": "same"},
        {"title": "a task", "description": "SAME"},
        {"title": "B", "description": "other"},
    ]
    groups = group_tasks(rows)
    assert len(groups) == 2
    assert sorted(len(v) for v in groups.values()) == [1, 2]


def test_power_estimate_matches_conservative_formula():
    result = power_estimate(71, 41)
    assert result["ci95_half_width"] == 0.1922
    assert result["min_detectable_effect_80pct"] == 0.2748
    assert result["required_positive_per_arm"] == 393
