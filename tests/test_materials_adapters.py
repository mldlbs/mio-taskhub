# -*- coding: utf-8 -*-
"""P3：真实来源适配器、来源健康与端到端入库。"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.materials.adapters import (
    read_discussions, read_doc_requirements, read_doc_reviews,
    read_github_trending, read_history_root_causes, read_hn, read_runs,
    source_health,
)
from mio_taskhub.materials.extract import extract_for_source
from mio_taskhub.materials.pipeline import RawRecord, ingest_records
from mio_taskhub.models import (
    Discussion, HistoryEvent, Run, RunState, Task, TaskState,
)


@pytest.fixture(autouse=True)
def _materials_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_MATERIALS_DIR", str(tmp_path / "materials"))


def _now():
    return datetime.now(timezone.utc)


def _write_observations(tmp_path, monkeypatch):
    base = tmp_path / "observer"
    obs = base / "observations"
    obs.mkdir(parents=True)
    (obs / "2026-10-27.json").write_text(json.dumps({
        "date": "2026-10-27",
        "observations": [
            {"id": "hn-1", "timestamp": "2026-10-27T10:00:00Z",
             "source": "hackernews",
             "content": "OpenLDK brings Java to Common Lisp (12 points by user)",
             "url": "https://example.com/1"},
            {"id": "gh-1", "timestamp": "2026-10-27T11:00:00Z",
             "source": "github-trending",
             "content": "【GitHub】owner/repo ⭐1 — A deterministic toolkit",
             "metadata": {"description": "A deterministic toolkit"}},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (obs / "2026-10-28.json").write_text(json.dumps({
        "date": "2026-10-28",
        "observations": [
            {"id": "hn-2", "timestamp": "2026-10-28T10:00:00Z",
             "source": "hackernews",
             "content": "SQLite WAL mode cuts write latency by 40% (3 points by user2)",
             "url": "https://example.com/2"},
            {"id": "gh-2", "timestamp": "2026-10-28T11:00:00Z",
             "source": "github-trending",
             "content": "【GitHub】acme/queue ⭐2 — SQLite job queue",
             "metadata": {"description": "SQLite &amp; WAL job queue"}},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MIO_OBSERVER_BASE_DIR", str(base))
    return base


def test_hn_adapter_and_cursor(tmp_path, monkeypatch):
    _write_observations(tmp_path, monkeypatch)
    batch = read_hn(days=30)
    assert len(batch.records) == 2
    assert {r.external_id for r in batch.records} == {"hn-1", "hn-2"}
    assert batch.records[0].payload["content"].startswith("OpenLDK")

    # 增量：只处理 last_date 之后的文件
    b2 = read_hn(days=30, cursor=batch.cursor)
    assert len(b2.records) == 0

    b3 = read_hn(days=30, cursor='{"last_date": "2026-10-27"}')
    assert [r.external_id for r in b3.records] == ["hn-2"]


def test_hn_limit_does_not_skip_mid_file(tmp_path, monkeypatch):
    _write_observations(tmp_path, monkeypatch)
    b1 = read_hn(days=30, limit=1)
    assert len(b1.records) == 1
    assert b1.cursor is None  # 第一份文件未处理完，不推进日期
    b2 = read_hn(days=30, limit=1)
    assert b2.records[0].external_id == b1.records[0].external_id


def test_github_adapter_builds_clean_content(tmp_path, monkeypatch):
    _write_observations(tmp_path, monkeypatch)
    batch = read_github_trending(days=30)
    assert len(batch.records) == 2
    by_id = {r.external_id: r for r in batch.records}
    assert by_id["gh-1"].payload["content"] == "owner/repo — A deterministic toolkit"
    assert by_id["gh-2"].payload["content"] == "acme/queue — SQLite & WAL job queue"
    assert by_id["gh-1"].payload["url"] == "https://github.com/owner/repo"


def test_doc_requirement_adapter(tmp_path):
    ws = tmp_path / "ws"
    (ws / "docs").mkdir(parents=True)
    (ws / "docs" / "req.md").write_text(
        "# 需求\n\n- FR-1 素材必须带出处。\n- FR-2 旧 witness 必须可回读。\n",
        encoding="utf-8",
    )
    with Session(engine) as s:
        s.add(Task(
            id="task-doc", title="doc task", workspace=str(ws),
            project="proj", state=TaskState.COMPLETED,
            completed_at=_now(),
            doc_paths={"requirement": "docs/req.md"},
            doc_statuses={"requirement": {
                "state": "approved", "at": "2026-10-27T00:00:00+00:00",
            }},
        ))
        s.add(Task(
            id="task-doc-unapproved", title="unapproved", workspace=str(ws),
            project="proj", state=TaskState.COMPLETED, completed_at=_now(),
            doc_paths={"requirement": "docs/req.md"},
            doc_statuses={"requirement": {"state": "review"}},
        ))
        s.commit()
        batch = read_doc_requirements(s, days=30)
    assert len(batch.records) == 1
    rec = batch.records[0]
    assert rec.source_key == "doc.requirement"
    assert rec.payload["project"] == "proj"
    assert "FR-1" in rec.payload["text"]


def test_doc_review_adapter():
    with Session(engine) as s:
        s.add(Task(
            id="task-review", title="review task", project="proj",
            state=TaskState.COMPLETED, completed_at=_now(),
            review_result="批准通过：证据链完整。",
        ))
        s.commit()
        batch = read_doc_reviews(s, days=30)
    assert len(batch.records) == 1
    assert batch.records[0].payload["task_id"] == "task-review"


def test_discussion_adapter():
    with Session(engine) as s:
        s.add(Task(id="task-disc", title="disc task", project="proj"))
        s.add(Discussion(
            id="disc-1", task_id="task-disc", topic="素材边界",
            status="closed", conclusions="1) 采用版本化 raw revision。",
            ended_at=_now(),
        ))
        s.commit()
        batch = read_discussions(s, days=30)
    assert len(batch.records) == 1
    assert batch.records[0].payload["project"] == "proj"
    assert "版本化" in batch.records[0].payload["conclusions"]


def test_run_adapters_split_by_exit_code():
    with Session(engine) as s:
        s.add(Task(id="task-run", title="run task", project="proj"))
        s.add(Run(
            id="run-ok", task_id="task-run", agent_name="agent",
            state=RunState.FINISHED, result="pytest 10 passed.", exit_code=0,
            finished_at=_now(),
        ))
        s.add(Run(
            id="run-bad", task_id="task-run", agent_name="agent",
            state=RunState.FINISHED, result="ValueError: broken", exit_code=1,
            finished_at=_now(),
        ))
        s.commit()
        ok = read_runs(s, source_key="run.result", days=30)
        bad = read_runs(s, source_key="run.failure", days=30)
    assert [r.external_id for r in ok.records] == ["run:run-ok"]
    assert [r.external_id for r in bad.records] == ["run:run-bad"]
    assert ok.records[0].payload["exit_code"] == 0
    assert bad.records[0].payload["exit_code"] == 1


def test_history_root_cause_adapter():
    with Session(engine) as s:
        s.add(Task(id="task-hist", title="hist task", project="proj"))
        s.add(HistoryEvent(
            id=1, task_id="task-hist", type="",
            payload=json.dumps({"root_cause": "唯一键缺失导致重复计数。"}),
            at=_now(),
        ))
        s.commit()
        batch = read_history_root_causes(s, days=30)
    assert len(batch.records) == 1
    assert batch.records[0].payload["project"] == "proj"
    assert "唯一键" in batch.records[0].payload["root_cause"]


def test_adapter_to_pipeline_end_to_end(tmp_path, monkeypatch):
    _write_observations(tmp_path, monkeypatch)
    batch = read_hn(days=30)
    stats = ingest_records(
        "hn", batch.records,
        lambda p, s: extract_for_source("hn", p, s),
        cursor=batch.cursor,
    )
    assert stats.accepted == 2
    with Session(engine) as s:
        health = {h["key"]: h for h in source_health(s)}
        assert health["hn"]["raw_total"] == 2
        assert health["hn"]["materials"] == 2
        assert health["hn"]["by_status"].get("active") == 2
