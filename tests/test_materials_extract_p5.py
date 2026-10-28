# -*- coding: utf-8 -*-
"""P5 提取器修复：评审分类、原子切分、泛化失败、signal 主张。"""
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.materials.extract import extract_for_source
from mio_taskhub.materials.models import Material, MaterialRaw
from mio_taskhub.materials.pipeline import RawRecord, ingest_records
from mio_taskhub.materials.quality import evaluate_draft


@pytest.fixture(autouse=True)
def _materials_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_MATERIALS_DIR", str(tmp_path / "materials"))


def _extractor(payload, source):
    return extract_for_source(source.key, payload, source)


def _ingest(source_key, external_id, payload):
    rec = RawRecord(
        source_key=source_key, external_id=external_id,
        observed_at=datetime.now(timezone.utc), payload=payload,
    )
    return ingest_records(source_key, [rec], _extractor)


def test_review_acceptance_splits_and_stays_decision():
    payload = {
        "review_result": "验收通过：① 第一项验收内容全部通过。② 第二项验收内容全部通过。③ 第三项验收内容全部通过。",
        "project": "proj",
    }
    drafts = extract_for_source("doc.review", payload)
    assert len(drafts) == 3
    assert all(d.kind == "decision" for d in drafts)
    assert all(len(d.quote) <= 240 for d in drafts)
    for d in drafts:
        gate = evaluate_draft(d, payload, "doc.review", d.scope_key or "", 1, 1)
        assert gate.ok, gate.reason


def test_review_rejection_is_problem():
    payload = {"review_result": "不通过：缺少逐字校验与回滚用例。", "project": "proj"}
    drafts = extract_for_source("doc.review", payload)
    assert len(drafts) == 1
    assert drafts[0].kind == "problem"


def test_long_single_sentence_review_is_rejected():
    payload = {
        "review_result": "验收通过：" + "很长" * 200 + "。",
        "project": "proj",
    }
    assert extract_for_source("doc.review", payload) == []


def test_discussion_long_items_split():
    payload = {
        "conclusions": "第一项内容全部通过并写入文档。第二项内容全部通过并写入文档。第三项内容全部通过并写入文档。",
        "project": "proj",
    }
    drafts = extract_for_source("discussion.closed", payload)
    assert len(drafts) == 3
    assert all(d.kind == "decision" for d in drafts)
    assert all(len(d.quote) <= 240 for d in drafts)


def test_run_failure_generic_is_rejected_by_pipeline():
    stats = _ingest("run.failure", "fail-generic", {
        "result": "agent offline", "exit_code": 1, "project": "proj",
    })
    assert stats.accepted == 0
    with Session(engine) as s:
        assert s.exec(select(Material)).all() == []
        raw = s.exec(select(MaterialRaw)).one()
        assert raw.extract_status in ("rejected", "skipped")
        assert s.exec(select(Material)).all() == []


def test_signal_question_is_rejected_by_pipeline():
    stats = _ingest("hn", "hn-question", {
        "content": "Ask HN: CAD for wood working with LLM support?",
    })
    assert stats.accepted == 0
    with Session(engine) as s:
        assert s.exec(select(Material)).all() == []
        raw = s.exec(select(MaterialRaw)).one()
        assert "HG-10" in (raw.reject_reason or "")


def test_signal_short_release_is_accepted():
    stats = _ingest("hn", "hn-release", {
        "content": "Picard 3.0 Released",
        "url": "https://example.com/picard",
    })
    assert stats.accepted == 1
    with Session(engine) as s:
        m = s.exec(select(Material)).one()
        assert m.kind == "signal"
        assert m.status == "active"


def test_run_result_does_not_silently_truncate():
    payload = {
        "result": "pytest " + "x" * 120 + ", npm run build ok.",
        "exit_code": 0,
        "project": "proj",
    }
    drafts = extract_for_source("run.result", payload)
    assert drafts
    for d in drafts:
        assert len(d.quote) <= 240
        assert d.quote in payload["result"]


def test_run_result_kind_mixing_is_classified():
    from mio_taskhub.materials.extract import _run_result_kind
    assert _run_result_kind("git_sync.py 是投递 worker，轮询 PENDING。") == "fact"
    assert _run_result_kind("5) task 表时间戳列名易踩坑：无 updated_at。") == "lesson"
    assert _run_result_kind("运行进程 PID 13184 启动于 11:16:30。") == "fact"
    assert _run_result_kind("注：set_doc_status 被守卫拦截。") == "lesson"
    assert _run_result_kind("✗ Read night_runner.json failed。") == "problem"
    assert _run_result_kind("pytest 10 passed.") == "result"
