# -*- coding: utf-8 -*-
"""P3：采集调度日上限与单源故障隔离。"""
import json

import pytest
from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.materials.adapters import AdapterBatch
from mio_taskhub.materials.jobs import MaterialIngestJob
from mio_taskhub.materials.models import MaterialRaw, MaterialSource


@pytest.fixture(autouse=True)
def _materials_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MIO_MATERIALS_DIR", str(tmp_path / "materials"))


def _observer(tmp_path, monkeypatch):
    base = tmp_path / "observer"
    obs = base / "observations"
    obs.mkdir(parents=True)
    (obs / "2026-10-28.json").write_text(json.dumps({
        "date": "2026-10-28",
        "observations": [
            {"id": "hn-a", "timestamp": "2026-10-28T10:00:00Z",
             "source": "hackernews",
             "content": "First signal is sufficiently long (1 point by a)",
             "url": "https://example.com/a"},
            {"id": "hn-b", "timestamp": "2026-10-28T11:00:00Z",
             "source": "hackernews",
             "content": "Second signal is sufficiently long (2 points by b)",
             "url": "https://example.com/b"},
        ],
    }), encoding="utf-8")
    monkeypatch.setenv("MIO_OBSERVER_BASE_DIR", str(base))


def test_job_daily_cap(tmp_path, monkeypatch):
    _observer(tmp_path, monkeypatch)
    with Session(engine) as s:
        src = s.get(MaterialSource, "hn")
        src.daily_cap = 1
        src.last_run_at = None
        s.commit()

    job = MaterialIngestJob()
    job._run_source("hn")
    job._run_source("hn")

    with Session(engine) as s:
        assert len(s.exec(select(MaterialRaw)).all()) == 1
        src = s.get(MaterialSource, "hn")
        assert src.last_status == "ok"


def test_job_isolates_source_failure(monkeypatch):
    import mio_taskhub.materials.jobs as jobs

    def fake(key, session, **kwargs):
        if key == "hn":
            raise RuntimeError("boom")
        return AdapterBatch([])

    monkeypatch.setattr(jobs, "run_adapter", fake)
    with Session(engine) as s:
        for key in ("hn", "github-trending"):
            src = s.get(MaterialSource, key)
            src.last_run_at = None
            src.min_interval_s = 30
        s.commit()

    MaterialIngestJob().tick()

    with Session(engine) as s:
        hn = s.get(MaterialSource, "hn")
        gh = s.get(MaterialSource, "github-trending")
        assert hn.last_status == "error"
        assert "boom" in (hn.last_error or "")
        assert gh.last_status == "ok"
