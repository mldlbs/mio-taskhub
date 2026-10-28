# -*- coding: utf-8 -*-
"""P3 真实来源适配器：读取 observer / taskhub 表，产出 RawRecord（不写库）。

所有适配器只做“读源 + 构造 payload”；入库、脱敏、提炼由 pipeline 负责。
"""
from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from sqlalchemy import func
from sqlmodel import Session, select

from mio_taskhub import mio_runtime
from mio_taskhub.materials.common import canonical_json
from mio_taskhub.materials.pipeline import RawRecord
from mio_taskhub.models import Discussion, HistoryEvent, Run, RunState, Task
from mio_taskhub.utils import parse_utc

MAX_DOC_BYTES = 256 * 1024
_GITHUB_REPO = re.compile(r"^【GitHub】([^\s⭐]+)")


@dataclass
class AdapterBatch:
    records: List[RawRecord]
    cursor: Optional[str] = None


def _now():
    return datetime.now(timezone.utc)


def _cursor_payload(cursor: Optional[str]) -> Dict[str, Any]:
    if not cursor:
        return {}
    try:
        obj = json.loads(cursor)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _since(days: int, since: Optional[str], cursor: Optional[str]) -> datetime:
    cur = _cursor_payload(cursor)
    for candidate in (cur.get("since"), since):
        dt = parse_utc(candidate)
        if dt is not None:
            return dt
    return _now() - timedelta(days=max(1, days))


def _next_since(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return canonical_json({"since": parse_utc(dt).isoformat()})


def _file_cutoff(days: int, since: Optional[str],
                 cursor: Optional[str]) -> datetime:
    cur = _cursor_payload(cursor)
    last = parse_utc(cur.get("last_date"))
    if last is not None:
        return last + timedelta(days=1)
    dt = parse_utc(since)
    if dt is not None:
        return dt
    return _now() - timedelta(days=max(1, days))


def _observer_observations_dir() -> Path:
    return Path(mio_runtime.observer_base_dir()) / "observations"


def _obs_files(start: datetime) -> List[tuple[Path, datetime]]:
    base = _observer_observations_dir()
    out: List[tuple[Path, datetime]] = []
    for path in sorted(base.glob("*.json")):
        try:
            day = datetime.strptime(path.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day.date() < start.date():
            continue
        if day > _now() + timedelta(days=1):
            continue
        out.append((path, day))
    return out


def _load_obs(path: Path) -> List[dict]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = obj.get("observations") if isinstance(obj, dict) else None
    return items if isinstance(items, list) else []


def _obs_time(item: dict, fallback: datetime) -> datetime:
    return parse_utc(item.get("timestamp")) or fallback


def read_hn(session: Optional[Session] = None, *, days: int = 1,
            since: Optional[str] = None, cursor: Optional[str] = None,
            limit: Optional[int] = None, root: Optional[str] = None) -> AdapterBatch:
    cutoff = _file_cutoff(days, since, cursor)
    records: List[RawRecord] = []
    last_complete: Optional[datetime] = None
    for path, day in _obs_files(cutoff):
        complete = True
        for item in _load_obs(path):
            if item.get("source") != "hackernews":
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            records.append(RawRecord(
                source_key="hn",
                external_id=str(item.get("id") or f"hn:{path.stem}:{len(records)}"),
                observed_at=_obs_time(item, day),
                payload={"content": content, "url": item.get("url")},
            ))
            if limit is not None and len(records) >= limit:
                complete = False
                break
        if complete:
            last_complete = day
        if limit is not None and len(records) >= limit:
            break
    cur = canonical_json({"last_date": last_complete.date().isoformat()}) if last_complete else None
    return AdapterBatch(records, cur)


def read_github_trending(session: Optional[Session] = None, *, days: int = 1,
                         since: Optional[str] = None, cursor: Optional[str] = None,
                         limit: Optional[int] = None,
                         root: Optional[str] = None) -> AdapterBatch:
    cutoff = _file_cutoff(days, since, cursor)
    records: List[RawRecord] = []
    last_complete: Optional[datetime] = None
    for path, day in _obs_files(cutoff):
        complete = True
        for item in _load_obs(path):
            if item.get("source") != "github-trending":
                continue
            raw_content = str(item.get("content") or "")
            match = _GITHUB_REPO.match(raw_content)
            repo = match.group(1) if match else ""
            meta = item.get("metadata") or {}
            desc = html.unescape(str(meta.get("description") or "")).strip()
            if not repo or not desc:
                continue
            content = f"{repo} — {desc}"
            records.append(RawRecord(
                source_key="github-trending",
                external_id=str(item.get("id") or f"gh:{repo}@{path.stem}"),
                observed_at=_obs_time(item, day),
                payload={"content": content, "url": f"https://github.com/{repo}"},
            ))
            if limit is not None and len(records) >= limit:
                complete = False
                break
        if complete:
            last_complete = day
        if limit is not None and len(records) >= limit:
            break
    cur = canonical_json({"last_date": last_complete.date().isoformat()}) if last_complete else None
    return AdapterBatch(records, cur)


def _json_obj(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        obj = json.loads(str(value))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _workspace_root(workspace: Optional[str], root: Optional[str]) -> Path:
    if root:
        return Path(root)
    env = os.environ.get("MIO_TASKHUB_WORKSPACE_ROOT")
    if env:
        return Path(env)
    if workspace:
        p = Path(workspace)
        if p.is_absolute():
            return p
    return Path.cwd()


def _task_project(session: Session, task_id: str) -> str:
    if not task_id:
        return ""
    try:
        value = session.exec(
            select(Task.project).where(Task.id == task_id)
        ).first()
        return (value or "") or ""
    except Exception:
        return ""


def _read_doc(path: Path) -> str:
    try:
        data = path.read_bytes()[:MAX_DOC_BYTES]
    except Exception:
        return ""
    for enc in ("utf-8", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_doc_requirements(session: Session, *, days: int = 180,
                          since: Optional[str] = None, cursor: Optional[str] = None,
                          limit: Optional[int] = None,
                          root: Optional[str] = None) -> AdapterBatch:
    cutoff = _since(days, since, cursor)
    records: List[RawRecord] = []
    newest: Optional[datetime] = None
    rows = session.exec(select(
        Task.id, Task.project, Task.workspace, Task.doc_paths,
        Task.doc_statuses, Task.completed_at, Task.created_at,
    )).all()
    for task_id, project, workspace, doc_paths, doc_statuses, completed_at, created_at in rows:
        if not doc_paths:
            continue
        statuses = _json_obj(doc_statuses)
        req = statuses.get("requirement") if isinstance(statuses, dict) else None
        if not isinstance(req, dict) or req.get("state") != "approved":
            continue
        at = parse_utc(req.get("at")) or parse_utc(completed_at) or parse_utc(created_at)
        if at is None or at < cutoff:
            continue
        paths = _json_obj(doc_paths)
        rel = paths.get("requirement")
        if not rel:
            continue
        path = Path(rel)
        if not path.is_absolute():
            path = _workspace_root(workspace, root) / path
        text = _read_doc(path)
        if not text.strip():
            continue
        records.append(RawRecord(
            source_key="doc.requirement",
            external_id=f"doc.req:{task_id}:{rel}",
            observed_at=at,
            payload={"text": text, "path": rel, "project": project or "",
                     "task_id": task_id},
        ))
        newest = at if newest is None or at > newest else newest
        if limit is not None and len(records) >= limit:
            break
    return AdapterBatch(records, _next_since(newest))


def read_doc_reviews(session: Session, *, days: int = 180,
                     since: Optional[str] = None, cursor: Optional[str] = None,
                     limit: Optional[int] = None,
                     root: Optional[str] = None) -> AdapterBatch:
    cutoff = _since(days, since, cursor)
    records: List[RawRecord] = []
    newest: Optional[datetime] = None
    rows = session.exec(select(
        Task.id, Task.project, Task.review_result,
        Task.completed_at, Task.created_at,
    )).all()
    for task_id, project, review_result, completed_at, created_at in rows:
        text = (review_result or "").strip()
        if not text:
            continue
        at = parse_utc(completed_at) or parse_utc(created_at)
        if at is None or at < cutoff:
            continue
        records.append(RawRecord(
            source_key="doc.review",
            external_id=f"doc.review:{task_id}",
            observed_at=at,
            payload={"review_result": text, "task_id": task_id,
                     "project": project or ""},
        ))
        newest = at if newest is None or at > newest else newest
        if limit is not None and len(records) >= limit:
            break
    return AdapterBatch(records, _next_since(newest))


def read_discussions(session: Session, *, days: int = 180,
                     since: Optional[str] = None, cursor: Optional[str] = None,
                     limit: Optional[int] = None,
                     root: Optional[str] = None) -> AdapterBatch:
    cutoff = _since(days, since, cursor)
    records: List[RawRecord] = []
    newest: Optional[datetime] = None
    rows = session.exec(
        select(Discussion).where(Discussion.status == "closed")
    ).all()
    for d in rows:
        text = (d.conclusions or "").strip()
        if not text:
            continue
        at = parse_utc(d.ended_at) or parse_utc(d.started_at)
        if at is None or at < cutoff:
            continue
        project = _task_project(session, d.task_id)
        records.append(RawRecord(
            source_key="discussion.closed",
            external_id=f"discussion:{d.id}",
            observed_at=at,
            payload={"conclusions": text, "topic": d.topic or "讨论结论",
                     "project": project, "discussion_id": d.id},
        ))
        newest = at if newest is None or at > newest else newest
        if limit is not None and len(records) >= limit:
            break
    return AdapterBatch(records, _next_since(newest))


def _run_exit_code(run: Run) -> int:
    try:
        return int(run.exit_code or 0)
    except Exception:
        return 0


def read_runs(session: Session, *, source_key: str = "run.result",
              days: int = 180, since: Optional[str] = None,
              cursor: Optional[str] = None, limit: Optional[int] = None,
              root: Optional[str] = None) -> AdapterBatch:
    cutoff = _since(days, since, cursor)
    records: List[RawRecord] = []
    newest: Optional[datetime] = None
    rows = session.exec(
        select(Run).where(Run.state == RunState.FINISHED)
    ).all()
    for run in rows:
        at = parse_utc(run.finished_at)
        if at is None or at < cutoff:
            continue
        code = _run_exit_code(run)
        if source_key == "run.result" and code != 0:
            continue
        if source_key == "run.failure" and code == 0:
            continue
        text = (run.result or "").strip()
        if not text:
            continue
        project = _task_project(session, run.task_id)
        records.append(RawRecord(
            source_key=source_key,
            external_id=f"run:{run.id}",
            observed_at=at,
            payload={"result": text, "exit_code": code, "run_id": run.id,
                     "task_id": run.task_id, "project": project},
        ))
        newest = at if newest is None or at > newest else newest
        if limit is not None and len(records) >= limit:
            break
    return AdapterBatch(records, _next_since(newest))


def read_history_root_causes(session: Session, *, days: int = 180,
                             since: Optional[str] = None,
                             cursor: Optional[str] = None,
                             limit: Optional[int] = None,
                             root: Optional[str] = None) -> AdapterBatch:
    cutoff = _since(days, since, cursor)
    records: List[RawRecord] = []
    newest: Optional[datetime] = None
    rows = session.exec(
        select(HistoryEvent).where(HistoryEvent.payload.like("%root_cause%"))
    ).all()
    for h in rows:
        at = parse_utc(h.at)
        if at is None or at < cutoff:
            continue
        payload = _json_obj(h.payload)
        text = str(payload.get("root_cause") or "").strip()
        if not text:
            continue
        project = _task_project(session, h.task_id)
        records.append(RawRecord(
            source_key="history.root_cause",
            external_id=f"history:{h.id}",
            observed_at=at,
            payload={"root_cause": text, "event_id": h.id, "task_id": h.task_id,
                     "project": project},
        ))
        newest = at if newest is None or at > newest else newest
        if limit is not None and len(records) >= limit:
            break
    return AdapterBatch(records, _next_since(newest))


ADAPTERS: Dict[str, Callable[..., AdapterBatch]] = {
    "hn": read_hn,
    "github-trending": read_github_trending,
    "doc.requirement": read_doc_requirements,
    "doc.review": read_doc_reviews,
    "discussion.closed": read_discussions,
    "run.result": read_runs,
    "run.failure": read_runs,
    "history.root_cause": read_history_root_causes,
}


def run_adapter(source_key: str, session: Optional[Session], **kwargs: Any) -> AdapterBatch:
    fn = ADAPTERS.get(source_key)
    if fn is None:
        raise KeyError(f"no adapter for source: {source_key}")
    if source_key in ("run.result", "run.failure"):
        return fn(session, source_key=source_key, **kwargs)
    return fn(session, **kwargs)


def source_health(session: Session) -> List[Dict[str, Any]]:
    from mio_taskhub.materials.models import (
        Material, MaterialRaw, MaterialWitness, MaterialSource,
    )
    out: List[Dict[str, Any]] = []
    sources = session.exec(select(MaterialSource).order_by(MaterialSource.key)).all()
    for src in sources:
        raw_total = session.execute(
            select(func.count()).select_from(MaterialRaw).where(
                MaterialRaw.source_key == src.key)
        ).scalar_one()
        raw_rejected = session.execute(
            select(func.count()).select_from(MaterialRaw).where(
                MaterialRaw.source_key == src.key,
                MaterialRaw.extract_status == "rejected")
        ).scalar_one()
        material_ids = set(session.exec(
            select(MaterialWitness.material_id).where(
                MaterialWitness.source_key == src.key)
        ).all())
        by_status: Dict[str, int] = {}
        for mid in material_ids:
            m = session.get(Material, mid)
            if m is not None:
                by_status[m.status] = by_status.get(m.status, 0) + 1
        out.append({
            "key": src.key,
            "origin": src.origin,
            "mode": src.mode,
            "independence_group": src.independence_group,
            "last_run_at": src.last_run_at.isoformat() if src.last_run_at else None,
            "last_status": src.last_status,
            "last_error": src.last_error,
            "raw_total": int(raw_total or 0),
            "raw_rejected": int(raw_rejected or 0),
            "materials": len(material_ids),
            "by_status": by_status,
        })
    return out
