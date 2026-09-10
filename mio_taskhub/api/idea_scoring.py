from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import (Idea, IdeaStatus, IdeaType, Task, IdeaHistory)
from mio_taskhub.utils import _now
from mio_taskhub.api.ideas import _idea_json, transition_idea_status

router = APIRouter(prefix="/ideas", tags=["ideas"])


# ==================== Pydantic Models ====================

class IdeaSearchRequest(BaseModel):
    query: Optional[str] = None
    status: Optional[List[str]] = None
    idea_type: Optional[str] = None
    category: Optional[str] = None
    project: Optional[str] = None
    labels: Optional[List[str]] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    min_score: Optional[float] = None
    sort_by: str = "updated_at"
    sort_order: str = "desc"
    page: int = 1
    page_size: int = 20


class IdeaScoreResponse(BaseModel):
    idea_id: str
    title: str
    score: float
    factors: Dict[str, float]
    recommendation: str


class BulkActionRequest(BaseModel):
    idea_ids: List[str]
    action: str  # archive, advance_status, add_labels, remove_labels, set_project
    payload: Dict[str, Any] = {}


# ==================== Search ====================

@router.post("/search")
def search_ideas(request: IdeaSearchRequest, db: Session = Depends(get_session)):
    """Full-text search for ideas with advanced filters."""
    q = select(Idea)

    if request.query:
        query = request.query.strip()
        if query:
            q = q.where(
                or_(
                    Idea.title.contains(query),
                    Idea.description.contains(query),
                )
            )

    if request.status:
        try:
            statuses = [IdeaStatus(s) for s in request.status]
            q = q.where(Idea.status.in_(statuses))
        except ValueError:
            raise HTTPException(400, "invalid status")

    if request.idea_type:
        try:
            q = q.where(Idea.idea_type == IdeaType(request.idea_type))
        except ValueError:
            raise HTTPException(400, "invalid idea_type")

    if request.project:
        q = q.where(Idea.project == request.project)

    if request.labels:
        for label in request.labels:
            q = q.where(Idea.labels.contains([label]))

    if request.date_from:
        try:
            dt = datetime.fromisoformat(request.date_from)
            q = q.where(Idea.updated_at >= dt)
        except ValueError:
            pass

    if request.date_to:
        try:
            dt = datetime.fromisoformat(request.date_to)
            q = q.where(Idea.updated_at <= dt)
        except ValueError:
            pass

    sort_col = getattr(Idea, request.sort_by, Idea.updated_at)
    if request.sort_order == "desc":
        q = q.order_by(sort_col.desc())
    else:
        q = q.order_by(sort_col.asc())

    page = max(1, request.page)
    page_size = min(max(1, request.page_size), 100)
    offset = (page - 1) * page_size

    total = db.exec(select(func.count()).select_from(q.subquery())).one()
    rows = db.exec(q.offset(offset).limit(page_size)).all()

    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "ideas": [_idea_json(i) for i in rows],
    }


# ==================== Scoring ====================

def _calculate_idea_score(idea: Idea, db: Session) -> Dict[str, Any]:
    """Calculate priority score for an idea (RICE-like)."""
    factors = {}

    reach = 1.0
    if idea.project:
        reach += 0.5
    if idea.labels:
        reach += len(idea.labels) * 0.1
    factors["reach"] = min(reach, 5.0)

    impact = 1.0
    if idea.status in [IdeaStatus.FORMED, IdeaStatus.BROKEN_DOWN]:
        impact += 1.0
    if idea.idea_type == "adr":
        impact += 0.5
    if any(l in (idea.labels or []) for l in ["P0", "critical", "blocker"]):
        impact += 1.0
    factors["impact"] = min(impact, 5.0)

    confidence = 1.0
    if idea.description and len(idea.description) > 100:
        confidence += 0.5
    if idea.review_count > 0:
        confidence += idea.review_count * 0.3
    if idea.discussions:
        msg_count = sum(len(d.messages or []) for d in idea.discussions)
        confidence += min(msg_count * 0.1, 1.0)
    factors["confidence"] = min(confidence, 5.0)

    effort = 3.0
    tasks = db.exec(select(Task).where(Task.idea_id == idea.id)).all()
    if tasks:
        total_est = sum(t.est_duration_min or 30 for t in tasks)
        if total_est <= 60:
            effort = 5.0
        elif total_est <= 180:
            effort = 4.0
        elif total_est <= 480:
            effort = 3.0
        elif total_est <= 1440:
            effort = 2.0
        else:
            effort = 1.0
    else:
        desc_len = len(idea.description or "")
        if desc_len > 500:
            effort = 2.0
        elif desc_len > 200:
            effort = 3.0
        else:
            effort = 4.0
    factors["effort"] = effort

    rice = (factors["reach"] * factors["impact"] * factors["confidence"]) / factors["effort"]

    if rice >= 8:
        recommendation = "high-priority"
    elif rice >= 4:
        recommendation = "medium-priority"
    elif rice >= 2:
        recommendation = "low-priority"
    else:
        recommendation = "backlog"

    return {
        "idea_id": idea.id,
        "title": idea.title,
        "score": round(rice, 2),
        "factors": {k: round(v, 2) for k, v in factors.items()},
        "recommendation": recommendation,
    }


@router.get("/{idea_id}/score", response_model=IdeaScoreResponse)
def get_idea_score(idea_id: str, db: Session = Depends(get_session)):
    """Calculate priority score for an idea."""
    idea = db.get(Idea, idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")

    return _calculate_idea_score(idea, db)


@router.get("/scores")
def get_all_scores(
    status: Optional[str] = None,
    idea_type: Optional[str] = None,
    min_score: float = 0,
    db: Session = Depends(get_session)
):
    """Get scores for all ideas (or filtered)."""
    q = select(Idea)
    if status:
        try:
            q = q.where(Idea.status == IdeaStatus(status))
        except ValueError:
            raise HTTPException(400, "invalid status")
    if idea_type:
        try:
            q = q.where(Idea.idea_type == IdeaType(idea_type))
        except ValueError:
            raise HTTPException(400, "invalid idea_type")

    ideas = db.exec(q).all()
    scores = [_calculate_idea_score(i, db) for i in ideas]
    scores = [s for s in scores if s["score"] >= min_score]
    scores.sort(key=lambda x: x["score"], reverse=True)

    return {
        "count": len(scores),
        "scores": scores,
    }


# ==================== Bulk Operations ====================

@router.post("/bulk")
def bulk_action(request: BulkActionRequest, db: Session = Depends(get_session)):
    """Perform bulk actions on multiple ideas."""
    if not request.idea_ids:
        raise HTTPException(422, "idea_ids is required")

    ideas = db.exec(select(Idea).where(Idea.id.in_(request.idea_ids))).all()
    if not ideas:
        raise HTTPException(404, "no ideas found")

    results = {"success": [], "failed": []}

    for idea in ideas:
        try:
            if request.action == "archive":
                transition_idea_status(idea, IdeaStatus.ARCHIVED, db, actor="bulk", source="bulk_archive")

            elif request.action == "advance_status":
                current = idea.status
                next_map = {
                    IdeaStatus.NEW: IdeaStatus.FERMENTING,
                    IdeaStatus.FERMENTING: IdeaStatus.FORMED,
                    IdeaStatus.FORMED: IdeaStatus.BROKEN_DOWN,
                }
                if current in next_map:
                    transition_idea_status(idea, next_map[current], db, actor="bulk", source="bulk_advance")
                else:
                    raise HTTPException(422, f"cannot advance from {current.value}")

            elif request.action == "add_labels":
                labels = request.payload.get("labels", [])
                for label in labels:
                    if label not in (idea.labels or []):
                        idea.labels = (idea.labels or []) + [label]

            elif request.action == "remove_labels":
                labels = request.payload.get("labels", [])
                idea.labels = [l for l in (idea.labels or []) if l not in labels]

            elif request.action == "set_project":
                project = request.payload.get("project", "")
                idea.project = project

            else:
                raise HTTPException(400, f"unknown action: {request.action}")

            idea.updated_at = _now()
            db.add(idea)
            results["success"].append(idea.id)

        except Exception as e:
            results["failed"].append({"id": idea.id, "error": str(e)})

    db.commit()
    return results


# ==================== Statistics ====================

@router.get("/stats/summary")
def get_ideas_summary(db: Session = Depends(get_session)):
    """Get summary statistics for ideas."""
    total = db.exec(select(func.count(Idea.id))).one()

    by_status = {}
    for status in IdeaStatus:
        count = db.exec(
            select(func.count(Idea.id)).where(Idea.status == status)
        ).one()
        by_status[status.value] = count

    by_type = {}
    for itype in IdeaType:
        count = db.exec(
            select(func.count(Idea.id)).where(Idea.idea_type == itype)
        ).one()
        by_type[itype.value] = count

    by_project = db.exec(
        select(Idea.project, func.count(Idea.id))
        .where(Idea.project != "")
        .group_by(Idea.project)
    ).all()
    by_project = {p: c for p, c in by_project}

    week_ago = datetime.now() - timedelta(days=7)
    recent = db.exec(
        select(func.count(Idea.id)).where(Idea.updated_at >= week_ago)
    ).one()

    all_ideas = db.exec(select(Idea)).all()
    scores = [_calculate_idea_score(i, db) for i in all_ideas]
    avg_score = sum(s["score"] for s in scores) / len(scores) if scores else 0

    return {
        "total": total,
        "by_status": by_status,
        "by_type": by_type,
        "by_project": by_project,
        "recent_week": recent,
        "avg_score": round(avg_score, 2),
    }


# ==================== Related Ideas ====================

@router.get("/{idea_id}/related")
def get_related_ideas(idea_id: str, limit: int = 5, db: Session = Depends(get_session)):
    """Find related ideas based on title similarity, shared labels, project."""
    idea = db.get(Idea, idea_id)
    if not idea:
        raise HTTPException(404, "idea not found")

    related = []

    if idea.project:
        same_project = db.exec(
            select(Idea)
            .where(Idea.project == idea.project, Idea.id != idea_id)
            .limit(limit)
        ).all()
        for r in same_project:
            related.append({"idea": _idea_json(r), "reason": f"同项目: {idea.project}", "score": 0.8})

    if idea.labels:
        for label in idea.labels:
            labeled = db.exec(
                select(Idea)
                .where(Idea.labels.contains([label]), Idea.id != idea_id)
                .limit(limit)
            ).all()
            for r in labeled:
                if not any(x["idea"]["id"] == r.id for x in related):
                    related.append({"idea": _idea_json(r), "reason": f"共同标签: {label}", "score": 0.6})

    title_tokens = set((idea.title or "").lower().split())
    if title_tokens:
        all_others = db.exec(select(Idea).where(Idea.id != idea_id)).all()
        for r in all_others:
            if r.id in [x["idea"]["id"] for x in related]:
                continue
            other_tokens = set((r.title or "").lower().split())
            overlap = len(title_tokens & other_tokens)
            if overlap >= 2:
                related.append({"idea": _idea_json(r), "reason": f"标题关键词重叠 ({overlap}个)", "score": 0.4})

    related.sort(key=lambda x: x["score"], reverse=True)
    return {"related": related[:limit]}


# ==================== Export ====================

@router.get("/export")
def export_ideas(
    format: str = "json",
    status: Optional[str] = None,
    idea_type: Optional[str] = None,
    db: Session = Depends(get_session)
):
    """Export ideas as JSON or Markdown."""
    q = select(Idea)
    if status:
        try:
            q = q.where(Idea.status == IdeaStatus(status))
        except ValueError:
            raise HTTPException(400, "invalid status")
    if idea_type:
        try:
            q = q.where(Idea.idea_type == IdeaType(idea_type))
        except ValueError:
            raise HTTPException(400, "invalid idea_type")

    ideas = db.exec(q.order_by(Idea.updated_at.desc())).all()

    if format == "json":
        return {"ideas": [_idea_json(i) for i in ideas]}

    elif format == "markdown":
        lines = ["# Ideas Export", f"Generated: {datetime.now().isoformat()}", ""]
        for i in ideas:
            lines.append(f"## {i.title}")
            lines.append(f"**ID:** {i.id}  \n**Status:** {i.status.value}  \n**Type:** {i.idea_type.value}  \n**Project:** {i.project or '—'}  \n**Labels:** {', '.join(i.labels) if i.labels else '—'}  \n**Updated:** {i.updated_at.isoformat()}")
            lines.append("")
            lines.append(i.description or "_No description_")
            lines.append("")
            lines.append("---")
            lines.append("")
        return {"content": "\n".join(lines), "format": "markdown"}

    else:
        raise HTTPException(400, "format must be 'json' or 'markdown'")


# ==================== Health Check ====================

@router.get("/health")
def ideas_health():
    return {"status": "ok", "features": ["templates", "search", "scoring", "bulk", "export", "related"]}
