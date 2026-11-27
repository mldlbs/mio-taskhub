# -*- coding: utf-8 -*-
"""研发评审会 REST 端点（任务 a20f3f9f，契约见 docs/taskhub/review-meeting/api.md §18）。

POST   /reviews/extract                      规则提取 + 落 review_point
GET    /reviews/points                       列表（status/kind/cluster 筛选 + 分页）
GET    /reviews/points/{point_id}             单点详情
PATCH  /reviews/points/{point_id}/shortlist   勾选/取消 shortlist
POST   /reviews/points/{point_id}/decide      六动作裁决
POST   /reviews/meetings/open                 创建 Discussion(mode=review) 会议容器
POST   /reviews/meetings/{id}/close           关会门控 + 五段 review + TaskReview
GET    /reviews/meetings/{id}/trace           全链回溯

设计要点（对齐契约）：
  - 写操作校验顺序固定 404 → 403（点已有 reviewer 且不同人） → 状态/422。
  - 422 人话化，带「你要做的是」。
  - 不改文档状态、不改任务 stage；关会只写 TaskReview / Discussion / Subtask。
  - 提取走 mio_taskhub.review.extract，不在此重复解析规则。
"""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.doc_quality import check_content
from mio_taskhub.doc_paths import doc_path_of
from mio_taskhub.events import emit_event
from mio_taskhub.models import (
    Decision, Discussion, DiscussionMessage, ReviewPoint, Subtask, Task, TaskReview,
)
from mio_taskhub.review import document_version, extract_review_points

router = APIRouter(prefix="/reviews", tags=["reviews"])

EXTRACT_KINDS = ("requirement", "spec", "api", "decision")
POINT_STATUSES = ("candidate", "shortlisted", "discussing", "resolved",
                  "rejected", "deferred")
POINT_KINDS = ("section_gap", "fr_contract", "api_contract", "data_rule",
               "risk_assumption", "decision_point", "premise", "nfr",
               "acceptance", "error_contract", "idempotency", "compat",
               "scope_boundary", "functional", "risk")
ACTIONS = ("confirm", "amend", "reject", "decide", "test", "defer")
TERMINAL = ("resolved", "rejected", "deferred")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat() if dt else None


def _err(status: int, msg: str):
    raise HTTPException(status_code=status, detail=msg)


def _task_or_404(task_id: str, db: Session) -> Task:
    t = db.get(Task, task_id)
    if not t:
        _err(404, f"任务 {task_id} 不存在。你要做的是：核对任务 ID 后重试")
    return t


def _doc_content(t: Task, kind: str) -> tuple[str, str, str]:
    """返回 (content, rel_path, doc_state)；文件不存在返回空正文。"""
    rel = doc_path_of(t, kind) or ''
    ws = (t.workspace or '').strip()
    p = Path(rel)
    if not p.is_absolute() and ws:
        p = Path(ws) / p
    content = ''
    if rel and p.is_file():
        try:
            content = p.read_text(encoding='utf-8', errors='replace')
        except OSError:
            content = ''
    state = (t.doc_statuses or {}).get(kind, {}).get('state', '') if isinstance(t.doc_statuses, dict) else ''
    return content, rel, state


def _serialize_point(p: ReviewPoint) -> dict:
    return {
        'id': p.id,
        'task_id': p.task_id,
        'seq': p.seq,
        'meeting_id': p.meeting_id,
        'source_doc': {
            'kind': p.source_kind, 'path': p.source_path,
            'version': p.source_version, 'status': p.source_status,
        },
        'kind': p.kind,
        'section': p.section,
        'anchor': p.anchor,
        'claim': p.claim,
        'quote': p.quote,
        'why_it_matters': p.why_it_matters,
        'risk_level': p.risk_level,
        'suggested_action': p.suggested_action,
        'status': p.status,
        'origin': p.origin,
        'dedup_key': p.dedup_key,
        'cluster_id': p.cluster_id,
        'reject_reason': p.reject_reason,
        'annotations': p.annotations or [],
        'reviewer': p.reviewer,
        'decision_id': p.decision_id,
        'decided_at': _iso(p.decided_at),
        'fr_refs': p.fr_refs or [],
        'tc_refs': p.tc_refs or [],
        'rule_version': p.rule_version,
        'created_at': _iso(p.created_at),
        'updated_at': _iso(p.updated_at),
    }


# ── 18.1 extract ──────────────────────────────────────────────────────────

@router.post('/extract')
def extract_points(body: dict, db: Session = Depends(get_session)):
    task_id = str(body.get('task_id') or '').strip()
    if not task_id:
        _err(422, 'task_id 必填。你要做的是：传任务 ID，如 "a20f3f9f"')
    t = _task_or_404(task_id, db)

    kinds = body.get('kinds')
    if kinds is None:
        kinds = list(EXTRACT_KINDS)
    if not isinstance(kinds, list) or not kinds:
        _err(422, 'kinds 必须是非空数组。你要做的是：传 ["requirement","spec","api","decision"] 的子集')
    for k in kinds:
        if k not in EXTRACT_KINDS:
            _err(422, f'kinds 含不支持类型「{k}」。你要做的是：只看 requirement/spec/api/decision 四类文档')

    # 质量门：errors>0 或分数<80 → 409（契约 §18.1）
    for k in kinds:
        content, _, _ = _doc_content(t, k)
        q = check_content(k, content or None)
        if q and (q.get('errors') or q.get('score', 100) < 80):
            _err(409, f'文档 {k} 质量门未过（errors={len(q.get("errors") or [])}，'
                      f'score={q.get("score")}）。你要做的是：先修文档质量再提取')

    out_points, skipped, versions = [], 0, {}
    for k in kinds:
        content, rel, state = _doc_content(t, k)
        if not content:
            continue
        ver = document_version(content)
        versions[k] = ver
        pts, _stats = extract_review_points(content, t.id, kind=k)
        for p in pts:
            exists = db.exec(select(ReviewPoint).where(
                ReviewPoint.task_id == t.id,
                ReviewPoint.dedup_key == p['dedup_key'],
                ReviewPoint.source_kind == k,
            )).first()
            if exists:
                skipped += 1
                continue
            row = ReviewPoint(
                id=p['id'], task_id=t.id, seq=int(p['id'].rsplit('-', 1)[-1]),
                source_kind=k, source_path=rel, source_version=ver, source_status=state,
                kind=p['kind'], section=p['section'], anchor=p['anchor'],
                claim=p['claim'], quote=p['quote'],
                why_it_matters='', risk_level=p['risk'], suggested_action='',
                status='candidate', origin='rule', dedup_key=p['dedup_key'],
                cluster_id=None, rule_version='v1',
            )
            db.add(row)
            out_points.append(_serialize_point(row))
    emit_event(db, type='review_extracted', entity='task', entity_id=t.id,
               payload={'kinds': kinds, 'points': len(out_points), 'skipped': skipped})
    db.commit()
    return {
        'task_id': t.id,
        'version': next(iter(versions.values()), ''),
        'points': out_points,
        'skipped': skipped,
    }


# ── 18.2 points list ──────────────────────────────────────────────────────

@router.get('/points')
def list_points(
    task_id: str = Query(...),
    status: str | None = None,
    cluster_id: str | None = None,
    kind: str | None = None,
    page: int = 1,
    size: int = 50,
    sort: str = 'risk_level',
    order: str = 'desc',
    db: Session = Depends(get_session),
):
    _task_or_404(task_id, db)
    if page < 1 or page > 1000 or size < 1 or size > 200:
        _err(422, '分页参数越界：page 须 1-1000，size 须 1-200。你要做的是：调小参数')
    if status and status not in POINT_STATUSES:
        _err(422, f'status 非法「{status}」。你要做的是：用 {"/".join(POINT_STATUSES)} 之一')
    if sort not in ('risk_level', 'seq', 'created_at'):
        _err(422, 'sort 须为 risk_level/seq/created_at。你要做的是：换合法排序字段')
    if order not in ('asc', 'desc'):
        _err(422, 'order 须为 asc/desc。你要做的是：换合法排序方向')

    q = select(ReviewPoint).where(ReviewPoint.task_id == task_id)
    if status:
        q = q.where(ReviewPoint.status == status)
    if cluster_id:
        q = q.where(ReviewPoint.cluster_id == cluster_id)
    if kind:
        q = q.where(ReviewPoint.kind == kind)
    total = len(db.exec(q).all())
    col = {'risk_level': ReviewPoint.risk_level, 'seq': ReviewPoint.seq,
           'created_at': ReviewPoint.created_at}[sort]
    q = q.order_by(col.asc() if order == 'asc' else col.desc()).offset((page - 1) * size).limit(size)
    return {'total': total, 'page': page, 'size': size,
            'items': [_serialize_point(p) for p in db.exec(q).all()]}


# ── 18.3 point detail ─────────────────────────────────────────────────────

@router.get('/points/{point_id}')
def get_point(point_id: str, db: Session = Depends(get_session)):
    p = db.get(ReviewPoint, point_id)
    if not p:
        _err(404, f'评审点 {point_id} 不存在。你要做的是：核对 RP-id 后重试')
    return _serialize_point(p)


def _point_or_404(point_id: str, db: Session) -> ReviewPoint:
    p = db.get(ReviewPoint, point_id)
    if not p:
        _err(404, f'评审点 {point_id} 不存在。你要做的是：核对 RP-id 后重试')
    return p


def _check_reviewer(p: ReviewPoint, actor: str):
    """轻量所有权：点已有 reviewer 且不同人 → 403（本机信任域无强鉴权）。"""
    if p.reviewer and actor and p.reviewer != actor:
        _err(403, f'评审点 {p.id} 由 {p.reviewer} 主审，{actor} 无权改。你要做的是：换回主审操作')


# ── 18.4 shortlist ────────────────────────────────────────────────────────

@router.patch('/points/{point_id}/shortlist')
def set_shortlist(point_id: str, body: dict, db: Session = Depends(get_session)):
    p = _point_or_404(point_id, db)
    _check_reviewer(p, str(body.get('actor') or ''))
    if p.status in TERMINAL:
        _err(409, f'评审点已终态（status={p.status}）。你要做的是：查 GET 确认历史，勿重复操作')
    if 'selected' not in body or not isinstance(body['selected'], bool):
        _err(422, 'selected 必填且须为布尔值。你要做的是：传 {"selected":true} 或 {"selected":false}')
    p.status = 'shortlisted' if body['selected'] else 'candidate'
    p.updated_at = _now()
    db.add(p)
    emit_event(db, type='review_shortlist', entity='review_point', entity_id=p.id,
               payload={'status': p.status})
    db.commit()
    return {'id': p.id, 'status': p.status}


# ── 18.5 decide ───────────────────────────────────────────────────────────

@router.post('/points/{point_id}/decide')
def decide_point(point_id: str, body: dict, db: Session = Depends(get_session)):
    p = _point_or_404(point_id, db)
    reviewer = str(body.get('reviewer') or body.get('actor') or 'user')
    _check_reviewer(p, reviewer)
    if p.status in TERMINAL:
        _err(409, f'评审点已终态（status={p.status}）。你要做的是：查 GET 确认历史，勿重复裁决')
    if p.status not in ('shortlisted', 'discussing'):
        _err(409, f'评审点尚未入会（status={p.status}）。你要做的是：先 PATCH shortlist 或 open meeting')

    action = body.get('action')
    if action not in ACTIONS:
        _err(422, f'action 须为 {"/".join(ACTIONS)} 之一。你要做的是：传合法动作')
    rationale = str(body.get('rationale') or '').strip()
    if action in ('reject', 'defer') and len(rationale) < 8:
        _err(422, f'{action} 必须填 ≥8 字理由（当前 {len(rationale)} 字）。你要做的是：说明为什么驳回/暂缓')

    status_map = {'confirm': 'resolved', 'amend': 'resolved', 'reject': 'rejected',
                  'decide': 'resolved', 'test': 'resolved', 'defer': 'deferred'}
    p.status = status_map[action]
    p.reviewer = reviewer
    p.decided_at = _now()
    p.updated_at = _now()
    if action in ('reject', 'defer'):
        p.reject_reason = rationale
    db.add(p)

    decision_id = None
    if action == 'decide':
        options = body.get('options')
        if not isinstance(options, list) or not options:
            _err(422, 'action=decide 时 options 必填。你要做的是：传 [{"label":"方案A","note":"理由"}]')
        d = Decision(
            question=p.claim or p.anchor, context=p.quote,
            options=[{'label': str(o.get('label') or ''), 'note': str(o.get('note') or '')}
                     if isinstance(o, dict) else {'label': str(o), 'note': ''}
                     for o in options],
            recommendation=str(body.get('recommendation') or ''),
            status='open', task_id=p.task_id, source=f'RP-{p.id}',
        )
        db.add(d)
        db.flush()
        decision_id = d.id
        p.decision_id = decision_id

    if action == 'test':
        st = Subtask(task_id=p.task_id, title=f'[TC] {p.claim[:80]}', status='pending')
        db.add(st)
        db.flush()
        p.tc_refs = (p.tc_refs or []) + [st.id]

    msg = DiscussionMessage(
        discussion_id=p.meeting_id or '', author=reviewer,
        role=str(body.get('role') or 'product'),
        content=f'[{action}] {p.id} {p.claim}｜理由：{rationale or "无"}',
    )
    db.add(msg)
    db.flush()
    emit_event(db, type='review_decided', entity='review_point', entity_id=p.id,
               payload={'action': action, 'status': p.status, 'decision_id': decision_id})
    db.commit()
    return {'id': p.id, 'status': p.status, 'decision_id': decision_id,
            'message_id': str(msg.id)}


# ── 18.6 meeting open ─────────────────────────────────────────────────────

@router.post('/meetings/open')
def open_meeting(body: dict, db: Session = Depends(get_session)):
    task_id = str(body.get('task_id') or '').strip()
    if not task_id:
        _err(422, 'task_id 必填。你要做的是：传任务 ID')
    t = _task_or_404(task_id, db)

    active = db.exec(select(Discussion).where(
        Discussion.task_id == task_id, Discussion.mode == 'review',
        Discussion.status == 'open')).first()
    if active:
        _err(409, f'任务已有 active 评审会议 {active.id}。你要做的是：用现有会议，勿重复创建')

    roles = body.get('roles') or ['product']
    if not isinstance(roles, list) or any(not isinstance(r, str) or not r.strip() for r in roles):
        _err(422, 'roles 必须是非空字符串数组。你要做的是：传 ["product","tech"]')
    allowed = {'product', 'tech', 'redteam', 'qa'}
    extra = set(roles) - allowed
    if extra:
        _err(422, f'roles 含不支持角色 {sorted(extra)}。你要做的是：只用 {sorted(allowed)}')

    pts = db.exec(select(ReviewPoint).where(
        ReviewPoint.task_id == task_id, ReviewPoint.status == 'shortlisted')).all()
    if not pts:
        _err(422, '无 shortlist 点，不能开会。你要做的是：先 PATCH /points/{id}/shortlist 勾选 5-10 个点')

    d = Discussion(
        task_id=task_id, topic=f'研发评审会 {task_id}',
        agent=str(body.get('actor') or 'user'), status='open', stage='review',
        mode='review', roles=roles,
        prompt_snapshot={'roles': roles, 'prompt_version': str(body.get('prompt_version') or 'current')},
    )
    db.add(d)
    db.flush()
    for p in pts:
        p.status = 'discussing'
        p.meeting_id = d.id
        p.updated_at = _now()
        db.add(p)
        db.add(DiscussionMessage(
            discussion_id=d.id, author='system', role='product',
            content=f'[{p.id}] {p.claim}｜锚点：{p.anchor}｜原文：{p.quote[:120]}',
        ))
    emit_event(db, type='review_meeting_opened', entity='discussion', entity_id=d.id,
               payload={'task_id': task_id, 'point_count': len(pts)})
    db.commit()
    return {'discussion_id': d.id, 'mode': 'review', 'roles': roles, 'point_count': len(pts)}


# ── 18.7 meeting close ────────────────────────────────────────────────────

@router.post('/meetings/{discussion_id}/close')
def close_meeting(discussion_id: str, body: dict, db: Session = Depends(get_session)):
    d = db.get(Discussion, discussion_id)
    if not d or d.mode != 'review':
        _err(404, f'评审会议 {discussion_id} 不存在。你要做的是：核对会议 ID 后重试')
    if d.status == 'closed':
        _err(409, '会议已关闭，重复关会不允许。你要做的是：查 GET 确认历史')

    decision = body.get('decision')
    if decision not in ('approve', 'reject', 'comment'):
        _err(422, 'decision 须为 approve/reject/comment。你要做的是：传合法结论')
    summary = str(body.get('summary') or '').strip()
    if decision in ('approve', 'reject') and len(summary) < 8:
        _err(422, f'approve/reject 需 ≥8 字 summary（当前 {len(summary)}）。你要做的是：写纪要摘要')

    pts = db.exec(select(ReviewPoint).where(ReviewPoint.meeting_id == discussion_id)).all()
    review = body.get('review') or {}
    gaps = []
    not_terminal = [p.id for p in pts if p.status not in TERMINAL]
    if not_terminal:
        gaps.append(f'还有 {len(not_terminal)} 点未终态：{",".join(not_terminal[:5])}')
    decisions = review.get('decisions') or []
    if not isinstance(decisions, list) or len(decisions) < 2:
        gaps.append('decisions 需 ≥2（采纳/修改 + 转 Decision 登记均计入）')
    items = review.get('action_items') or []
    if not isinstance(items, list) or not items:
        gaps.append('action_items 需 ≥1')
    if gaps:
        _err(409, '门控未过：' + '；'.join(gaps))

    # 五段 review（复用 discussions._validate_review 的结构门控）
    from mio_taskhub.api.discussions import _validate_review
    try:
        normalized = _validate_review(review)
    except HTTPException as e:
        _err(422, f'review 结构不合法：{e.detail}')

    subtask_ids = []
    for it in normalized['action_items']:
        st = Subtask(task_id=d.task_id, title=f'[评审行动] {it["action"][:80]}', status='pending')
        db.add(st)
        db.flush()
        subtask_ids.append(st.id)

    checklist = {p.id: p.status in ('resolved',) for p in pts}
    tr = TaskReview(
        task_id=d.task_id, decision=decision, checklist=checklist,
        summary=summary or normalized['suggestions'],
        comments=normalized['divergences'],
        artifacts=[p.source_path for p in pts if p.source_path],
        reviewer=str(body.get('reviewer') or body.get('actor') or 'user'),
    )
    db.add(tr)
    d.status = 'closed'
    d.review = normalized
    d.summary = summary or d.summary
    d.conclusions = summary or normalized['suggestions']
    d.ended_at = _now()
    db.add(d)
    emit_event(db, type='review_meeting_closed', entity='discussion', entity_id=d.id,
               payload={'task_review_id': tr.id, 'subtask_ids': subtask_ids})
    db.commit()
    return {'discussion_id': d.id, 'status': 'closed', 'task_review_id': tr.id,
            'subtask_ids': subtask_ids}


# ── 18.8 trace ────────────────────────────────────────────────────────────

@router.get('/meetings/{discussion_id}/trace')
def meeting_trace(discussion_id: str, point_id: str | None = None,
                  db: Session = Depends(get_session)):
    d = db.get(Discussion, discussion_id)
    if not d or d.mode != 'review':
        _err(404, f'评审会议 {discussion_id} 不存在。你要做的是：核对会议 ID 后重试')
    q = select(ReviewPoint).where(ReviewPoint.meeting_id == discussion_id)
    if point_id:
        q = q.where(ReviewPoint.id == point_id)
        if not db.exec(q).first():
            _err(404, f'评审点 {point_id} 不在会议 {discussion_id} 中。你要做的是：核对 RP-id')
    pts = db.exec(q).all()
    trace = []
    for p in pts:
        trace.append({
            'id': p.id, 'quote': p.quote, 'anchor': p.anchor,
            'version': p.source_version, 'status': p.status,
            'decision_id': p.decision_id, 'subtask_ids': p.tc_refs or [],
            'reviewer': p.reviewer, 'decided_at': _iso(p.decided_at),
        })
    return {'discussion_id': discussion_id, 'trace': trace, 'count': len(trace)}
