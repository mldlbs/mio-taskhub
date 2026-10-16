# -*- coding: utf-8 -*-
"""幽灵 agent 发现 + 修复单测（P1-OBS 后续）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

DOC = REPO / "docs" / "taskhub" / "ghost-agent-finding-20261016.md"
P1OBS = REPO / "docs" / "taskhub" / "p1-obs-reobservation-20261016.md"


def test_ghost_report_exists():
    assert DOC.exists()
    text = DOC.read_text(encoding="utf-8")
    assert "幽灵" in text
    assert "inv1" in text


def test_report_has_corrected_numbers():
    text = DOC.read_text(encoding="utf-8")
    # 修正后：链路断 2 条
    assert "0.4%" in text
    assert "383" in text or "81.0%" in text


def test_p1obs_has_correction_banner():
    text = P1OBS.read_text(encoding="utf-8")
    assert "更正" in text
    assert "ghost-agent-finding" in text


def test_analyze_reaper_never_started_bucket():
    """never_started 独立归桶，不再混入 link_broken。"""
    import analyze_reaper as ar
    b, reason = ar.classify(
        {"run_id": "x", "agent_status": "online", "reaper_kind": "never_started"}, {})
    assert b == ar.BUCKET_NEVER_STARTED
    assert "never_started" in reason
    # 非 never_started 的 online 仍为 link_broken
    b2, _ = ar.classify({"run_id": "y", "agent_status": "online", "reaper_kind": "heartbeat_timeout"}, {})
    assert b2 == ar.BUCKET_LINK_BROKEN


def test_analyze_reaper_all_buckets():
    import analyze_reaper as ar
    assert ar.BUCKET_NEVER_STARTED in ar.ALL_BUCKETS
    assert len(ar.ALL_BUCKETS) == 4
