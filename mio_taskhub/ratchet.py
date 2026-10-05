# -*- coding: utf-8 -*-
"""棘轮基线（ratchet baseline）：把「历史最好值」记成只升不降的门槛。

动机：文档质量分、用例数这类指标，修好一次就应该**永远不低于那次**，
否则会出现「改了文档反而更差却照样通过」。棘轮只放行不下降，并在变好时自动抬高基线。

适用指标（按 kind）：
- 所有有质量规格的 kind：`score` —— 文档质量分
- `test`：`test_cases` —— 「用例清单」表格数据行数（用例只增不减）

语义：
- 首次在受控状态（review/approved/done）观察 → 记为基线；
- current >= baseline 放行；current > baseline → **抬高**基线（棘轮）；
- current < baseline → 阻断（`force=true` 可绕过并留痕）；
- 环境变量 `MIO_RATCHET_DISABLED=1` 关闭。
"""
import os
from datetime import datetime, timezone

from sqlmodel import Session, select

from mio_taskhub.models import RatchetBaseline

_ON = ('1', 'true', 'yes', 'on')

# 触发棘轮校验的目标状态：文档质量门的状态 + test 的 passed/failed
RATCHET_STATES = ('review', 'approved', 'done', 'passed', 'failed')


def enabled() -> bool:
    """棘轮门控是否开启（默认开）。MIO_RATCHET_DISABLED=1 关闭。"""
    return (os.environ.get('MIO_RATCHET_DISABLED') or '').strip().lower() not in _ON


def applies(state: str) -> bool:
    """该目标状态是否需要跑棘轮校验。"""
    return state in RATCHET_STATES


def _test_cases(content: str) -> int:
    """「用例清单」章节的表格数据行数（0 表示无从判定/为空）。"""
    from mio_taskhub.doc_quality import _sections_of, _table_data_rows
    hit = next(((t, b) for t, b in _sections_of(content) if '用例清单' in t), None)
    return _table_data_rows(hit[1]) if hit else 0


def current_metrics(kind: str, content) -> dict:
    """该 kind 的当前指标（只返回适用于它的）。content 为 None → {}。"""
    if content is None:
        return {}
    out = {}
    from mio_taskhub.doc_quality import QUALITY_SPEC, check_content
    if kind in QUALITY_SPEC:
        q = check_content(kind, content)
        if q and 'score' in q:
            out['score'] = int(q['score'])
    if kind == 'test':
        out['test_cases'] = _test_cases(content)
    return out


def _baseline_row(db: Session, task_id: str, kind: str, metric: str):
    return db.exec(select(RatchetBaseline).where(
        RatchetBaseline.task_id == task_id,
        RatchetBaseline.kind == kind,
        RatchetBaseline.metric == metric)).first()


def check_ratchet(db: Session, task_id: str, kind: str, content):
    """校验并（必要时）更新棘轮基线。

    返回 (blocked, bumps)：
    - blocked: [{'kind','metric','current','baseline'}]，非空即发生回退（调用方决定是否阻断）；
    - bumps:   [{'kind','metric','value',...}]，被抬高/新建的基线。
    只改内存对象，**commit 由调用方决定**（以便"部分抬高 + 部分阻断"时先落库再报错）。
    """
    if not enabled():
        return [], []
    now = datetime.now(timezone.utc)
    blocked, bumps = [], []
    for metric, value in current_metrics(kind, content).items():
        value = int(value)
        row = _baseline_row(db, task_id, kind, metric)
        if row is None:
            db.add(RatchetBaseline(task_id=task_id, kind=kind, metric=metric,
                                   value=value, at=now, note='基线初值'))
            bumps.append({'kind': kind, 'metric': metric, 'value': value, 'new': True})
        elif value < row.value:
            blocked.append({'kind': kind, 'metric': metric,
                            'current': value, 'baseline': row.value})
        elif value > row.value:
            old = row.value
            row.value = value
            row.at = now
            row.note = '棘轮抬高'
            db.add(row)
            bumps.append({'kind': kind, 'metric': metric, 'value': value, 'from': old})
    return blocked, bumps


def list_baselines(db: Session, task_id: str) -> list:
    rows = db.exec(select(RatchetBaseline)
                   .where(RatchetBaseline.task_id == task_id)
                   .order_by(RatchetBaseline.kind, RatchetBaseline.metric)).all()
    return [{'kind': r.kind, 'metric': r.metric, 'value': r.value,
             'at': r.at.isoformat() if r.at else None, 'note': r.note} for r in rows]
