# -*- coding: utf-8 -*-
"""FR-3：LLM 只给建议、绝不删点、失败降级（任务 a20f3f9f）。"""
from mio_taskhub.review.llm import enhance, parse_enhancement


def _pts():
    return [
        dict(id='RP-a-01', claim='C-1 换算待校准', quote='原文一', anchor='§4',
             risk_level='medium', why_it_matters='', suggested_action=''),
        dict(id='RP-a-02', claim='回差缺失', quote='原文二', anchor='§5',
             risk_level='low', why_it_matters='', suggested_action=''),
    ]


def test_no_enhancer_degrades_without_change():
    pts = _pts()
    out, n, why = enhance(pts, None)
    assert out == pts and n == 0 and why == 'no_enhancer'
    assert [p['risk_level'] for p in out] == ['medium', 'low']


def test_enhancer_fills_suggestions_only():
    raw = {'points': [
        {'id': 'RP-a-01', 'why_it_matters': '无换算会导致误告警',
         'risk_level': 'high', 'suggested_action': 'clarify'},
        {'id': 'RP-a-02', 'why_it_matters': '抖动风险', 'cluster_id': 'C9'},
    ]}
    out, n, why = enhance(_pts(), lambda pts, ctx: raw)
    assert n == 2 and why is None
    assert out[0]['why_it_matters'] == '无换算会导致误告警'
    assert out[0]['risk_level'] == 'high'
    assert out[0]['origin'] if 'origin' in out[0] else True  # 不新增 origin
    assert out[1]['llm_cluster_suggestion'] == 'C9'
    # rule 聚类字段不被 LLM 覆盖
    assert out[1].get('cluster_id') is None


def test_delete_instruction_ignored_point_count_preserved():
    raw = {'points': [
        {'id': 'RP-a-01', 'delete': True, 'why_it_matters': '应删除'},
        {'id': 'RP-a-02', 'drop_point': True},
    ]}
    pts = _pts()
    out, n, why = enhance(pts, lambda pts, ctx: raw)
    assert len(out) == len(pts) == 2  # 点数不变
    assert n == 0
    assert all(p['why_it_matters'] == '' for p in out)


def test_empty_and_bad_json_degrade():
    pts = _pts()
    out, n, why = enhance(pts, lambda pts, ctx: '')
    assert n == 0 and why == 'empty_or_invalid' and out == pts
    out, n, why = enhance(pts, lambda pts, ctx: '{bad json')
    assert n == 0 and why == 'empty_or_invalid'


def test_exception_degrades():
    def boom(pts, ctx):
        raise RuntimeError('timeout')
    out, n, why = enhance(_pts(), boom)
    assert n == 0 and why == 'error:RuntimeError'
    assert len(out) == 2


def test_parse_only_known_ids_and_valid_enums():
    pts = _pts()
    parsed = parse_enhancement({'points': [
        {'id': 'RP-unknown', 'why_it_matters': 'x'},
        {'id': 'RP-a-01', 'risk_level': 'critical', 'suggested_action': 'arbitrary'},
    ]}, pts)
    assert 'RP-unknown' not in parsed
    assert 'RP-a-01' not in parsed  # 非法枚举全丢弃 → 不产生建议条目
