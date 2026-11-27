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
import re
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
from mio_taskhub.review import (
    document_version, enhance_with_llm, extract_review_points, task8_of,
)

# FR-3：LLM enhancer 注入点。默认 None（无 LLM 配置 → 直接降级，不联网）。
# 部署方可在启动时 set_llm_enhancer(callable) 接入自己的代理；测试可 monkeypatch。
_LLM_ENHANCER = None
_LLM_TIMELIMIT_SEC = 8


def set_llm_enhancer(fn):
    """注册 LLM enhancer：callable(points:list[dict], ctx:dict) -> dict|None。"""
    global _LLM_ENHANCER
    _LLM_ENHANCER = fn


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


def norm_key(text: str) -> str:
    """claim 归一化，用于同簇内判冲突（粗粒度：去标点空白）。"""
    return re.sub(r'[\s\W_]+', '', str(text or '')).lower()


def _cluster_id(dedup_key: str, seq_by_key: dict) -> str:
    """FR-5：同 task 内同 dedup_key（跨文档）归同一簇 C1/C2/...。"""
    if not dedup_key:
        return None
    if dedup_key not in seq_by_key:
        seq_by_key[dedup_key] = 'C%d' % (len(seq_by_key) + 1)
    return seq_by_key[dedup_key]


def _force_shortlist(p: ReviewPoint, kind_conflicts: set) -> tuple[bool, str]:
    """FR-6 强制入会判定，返回 (是否强制, 原因)。

    三条件满足任一即强制：risk=high / 跨文档同簇冲突 / premise 无样本量说明。
    """
    if p.risk_level == 'high':
        return True, '高风险'
    if p.cluster_id and p.cluster_id in kind_conflicts:
        return True, '跨文档同簇冲突'
    if p.kind == 'premise' and not re.search(r'样本|样本量|sample|n\s*=', p.claim + p.quote, re.I):
        return True, '前提缺样本量说明'
    return False, ''


def _apply_shortlist(points: list, existing: dict) -> dict:
    """FR-6：强制入会 + 每 kind ≥1 + 总数 5-10，返回 {point_id: reason}。

    已有状态（candidate/shortlisted/discussing/终态）一律尊重，不回退。
    """
    selected = {}
    # 1. 强制入会
    for p in points:
        if p.status in TERMINAL:
            continue
        force, reason = _force_shortlist(p, existing.get('conflicts', set()))
        if force:
            selected[p.id] = reason
    # 2. 每个出现的 kind 至少 1 点（候选按 risk 降序挑）
    by_kind = {}
    for p in points:
        if p.status in TERMINAL:
            continue
        by_kind.setdefault(p.kind, []).append(p)
    for k, group in by_kind.items():
        if not any(p.id in selected for p in group):
            top = sorted(group, key=lambda x: {'high': 0, 'medium': 1, 'low': 2}.get(x.risk_level, 3))[0]
            selected[top.id] = f'{k} 至少 1 点'
    # 3. 总数裁剪到 10（按 risk 降序保留，超出的不勾选但仍是 candidate 永久保留）
    if len(selected) > 10:
        order = {'high': 0, 'medium': 1, 'low': 2}
        ranked = sorted(selected, key=lambda pid: (
            order.get(next((p.risk_level for p in points if p.id == pid), 'low'), 3), pid))
        selected = {pid: selected[pid] for pid in ranked[:10]}
    return selected


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

    # 全局 seq（跨文档连续，避免 RP-id 撞 (task_id,seq) 唯一约束）
    max_seq = db.exec(select(ReviewPoint.seq).where(ReviewPoint.task_id == t.id)
                      .order_by(ReviewPoint.seq.desc())).first() or 0
    prefix = task8_of(t.id)
    seq = max_seq
    # FR-5：同 task 内已有簇映射（重跑时保持 cluster_id 稳定）
    existing_rows = db.exec(select(ReviewPoint).where(ReviewPoint.task_id == t.id)).all()
    seq_by_key = {}
    for old in sorted(existing_rows, key=lambda x: x.seq):
        if old.dedup_key and old.dedup_key not in seq_by_key:
            seq_by_key[old.dedup_key] = old.cluster_id or _cluster_id(old.dedup_key, seq_by_key)
    # 跨文档同簇但 claim 冲突 → 强制入会判定用
    cluster_claims = {}
    for old in existing_rows:
        if old.cluster_id:
            cluster_claims.setdefault(old.cluster_id, set()).add(norm_key(old.claim))

    # 先收集本批所有候选 dict（带全局 id / source / cluster），再统一跑 LLM（FR-3）
    candidates, skipped, versions = [], 0, {}
    for k in kinds:
        content, rel, state = _doc_content(t, k)
        if not content:
            continue
        ver = document_version(content)
        versions[k] = ver
        pts, _stats = extract_review_points(content, t.id, kind=k)
        for p in pts:
            dkey = p['dedup_key']
            exists = db.exec(select(ReviewPoint).where(
                ReviewPoint.task_id == t.id,
                ReviewPoint.dedup_key == dkey,
                ReviewPoint.source_kind == k,
            )).first()
            if exists:
                skipped += 1
                if exists.cluster_id:
                    cluster_claims.setdefault(exists.cluster_id, set()).add(norm_key(p['claim']))
                continue
            cid = _cluster_id(dkey, seq_by_key)
            if cid:
                cluster_claims.setdefault(cid, set()).add(norm_key(p['claim']))
            seq += 1
            p.update({
                'id': 'RP-%s-%02d' % (prefix, seq), 'seq': seq,
                'source_kind': k, 'source_path': rel, 'source_version': ver,
                'source_status': state, 'cluster_id': cid,
                'why_it_matters': '', 'suggested_action': '',
                'origin': 'rule', 'rule_version': 'v1',
            })
            candidates.append(p)

    # FR-3：LLM 增强（默认无 enhancer → 直接降级；不增删点，删点指令被忽略）
    llm_applied, degraded = 0, None
    include_llm = bool(body.get('include_llm', False))
    if include_llm and candidates:
        raw = None
        if _LLM_ENHANCER:
            try:
                raw = _LLM_ENHANCER(candidates, {'task_id': t.id, 'kinds': kinds})
            except Exception as e:  # noqa: BLE001
                degraded = 'error:%s' % type(e).__name__
        candidates, llm_applied, degraded = enhance_with_llm(
            candidates, lambda pts, ctx: raw, {'task_id': t.id}) if raw else (candidates, 0, degraded or 'no_enhancer')

    # 建 row
    out_points = []
    for p in candidates:
        row = ReviewPoint(
            id=p['id'], task_id=t.id, seq=p['seq'],
            source_kind=p['source_kind'], source_path=p['source_path'],
            source_version=p['source_version'], source_status=p['source_status'],
            kind=p['kind'], section=p['section'], anchor=p['anchor'],
            claim=p['claim'], quote=p['quote'],
            why_it_matters=p.get('why_it_matters', ''),
            risk_level=p.get('risk') or p.get('risk_level') or 'medium',
            suggested_action=p.get('suggested_action', ''),
            status='candidate', origin='rule', dedup_key=p['dedup_key'],
            cluster_id=p.get('cluster_id'), rule_version='v1',
        )
        db.add(row)
        out_points.append(row)

    # FR-6：强制入会（cluster 冲突判定基于本批 + 历史）
    conflicts = {cid for cid, claims in cluster_claims.items() if len(claims) > 1}
    forced = _apply_shortlist(out_points, {'conflicts': conflicts})
    for row in out_points:
        if row.id in forced:
            row.status = 'shortlisted'

    emit_event(db, type='review_extracted', entity='task', entity_id=t.id,
               payload={'kinds': kinds, 'points': len(out_points),
                        'skipped': skipped, 'forced': len(forced),
                        'llm_applied': llm_applied, 'degraded': degraded})
    db.commit()
    return {
        'task_id': t.id,
        'version': next(iter(versions.values()), ''),
        'points': [_serialize_point(p) for p in out_points],
        'skipped': skipped,
        'forced': len(forced),
        'llm': {'applied': llm_applied, 'degraded': degraded},
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
            status='open', task_id=p.task_id, source=p.id,  # source = RP-{id} = 点 ID 本身
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
