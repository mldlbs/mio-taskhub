# -*- coding: utf-8 -*-
"""FR-3 LLM 增强与降级（任务 a20f3f9f）。

核心约束（勿回退）：
  1. LLM 只产出结构化 JSON：why_it_matters / risk_level / suggested_action / cluster_id；
     逐条附 quote + anchor 用于对齐。
  2. **无权删点**：返回里的 delete/drop/remove 指令一律忽略，候选点数不减。
  3. 失败降级：超时 / 空返回 / 非法 JSON / 返回删点指令 → 保留全部 rule 点，
     LLM 字段留空、origin=rule；绝不因为 LLM 波动改变候选集合。

注入式设计： enhancer 是 callable(points, ctx) -> dict | None，由调用方提供。
默认 DEFAULT_ENHANCER=None（无 LLM 配置时直接降级，不发起任何网络调用）。
"""
import json
import logging

log = logging.getLogger('mio_taskhub.review.llm')

VALID_RISK = ('high', 'medium', 'low')
VALID_ACTION = ('confirm', 'clarify', 'decide', 'test', 'out_of_scope')


def parse_enhancement(raw, points):
    """把 LLM 原始返回解析为 {point_id: {field: value}}。

    只接受 dict / list[{id, ...}] 两种形态；任何异常返回 {}（降级）。
    """
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    if not isinstance(raw, dict) or 'points' not in raw:
        # 兼容直接返回 list 的 LLM
        if isinstance(raw, list):
            raw = {'points': raw}
        else:
            return {}
    items = raw.get('points')
    if not isinstance(items, list):
        return {}

    known = {p['id']: p for p in points}
    out = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        pid = str(it.get('id') or it.get('point_id') or '')
        if pid not in known:
            continue
        # 删点指令一律忽略（FR-3 硬约束）
        if any(k in it for k in ('delete', 'drop', 'remove', 'drop_point')):
            log.warning('LLM 返回删点指令，已忽略：%s', pid)
            continue
        fields = {}
        why = str(it.get('why_it_matters') or '').strip()
        if why:
            fields['why_it_matters'] = why[:1000]
        risk = str(it.get('risk_level') or '').strip().lower()
        if risk in VALID_RISK:
            fields['risk_level'] = risk
        action = str(it.get('suggested_action') or '').strip().lower()
        if action in VALID_ACTION:
            fields['suggested_action'] = action
        cid = str(it.get('cluster_id') or '').strip()
        if cid:
            fields['cluster_id_suggestion'] = cid[:64]
        if fields:
            out[pid] = fields
    return out


def enhance(points, enhancer=None, ctx=None):
    """对 rule 点做 LLM 增强，返回 (enhanced_points, applied_count, degraded_reason)。

    - 不修改传入 points 里除建议字段外的任何字段；
    - 不增删任何点（点数恒定），origin 始终保持 'rule'（LLM 只是建议层）；
    - enhancer 异常 / 超时（由 enhancer 自己控制） / 返回空 → 降级，applied=0。
    """
    if not enhancer or not points:
        return points, 0, 'no_enhancer' if points else 'no_points'
    try:
        raw = enhancer(points, ctx or {})
    except Exception as e:  # noqa: BLE001 —— 任何 LLM 异常都降级
        log.warning('LLM enhancer 异常，降级为纯 rule：%s', e)
        return points, 0, 'error:%s' % type(e).__name__
    parsed = parse_enhancement(raw, points)
    if not parsed:
        return points, 0, 'empty_or_invalid'

    for p in points:
        fields = parsed.get(p['id'])
        if not fields:
            continue
        if 'why_it_matters' in fields:
            p['why_it_matters'] = fields['why_it_matters']
        if 'risk_level' in fields:
            p['risk_level'] = fields['risk_level']
        if 'suggested_action' in fields:
            p['suggested_action'] = fields['suggested_action']
        # cluster_id_suggestion 只记为建议，不覆盖 rule 聚类结果（人工确认才合并）
        if 'cluster_id_suggestion' in fields:
            p['llm_cluster_suggestion'] = fields['cluster_id_suggestion']
    return points, len(parsed), None
