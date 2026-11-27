# -*- coding: utf-8 -*-
"""研发评审会：提取规则 + H5 去伪口径单元测试。

证据来源：2026-11-26 对 csps-app-fxyj/srm-web-fcs/docs/taskhub
62 份文档批量回放 + 5 份 H5 盲回放（最终 22/49 = 44%）。
"""
import pytest

from mio_taskhub.review.extract import (
    document_version, task8_of, data_rows, list_items, is_meta_row,
    is_bare_short_heading, norm, extract_review_points,
)
from mio_taskhub.review.h5 import (
    GUARD, find_hit, in_scope, safe_pattern, replay, FINAL_WEAK,
)


def _pt(claim='', quote='', anchor=''):
    return dict(claim=claim, quote=quote, anchor=anchor)


class TestParsing:
    def test_data_rows_skip_header_and_sep(self):
        body = ['| 项 | 值 |', '|---|---|', '| A | B |', '| C | D |']
        rows = data_rows(body)
        assert rows == [(2, 'A / B'), (3, 'C / D')]

    def test_list_items(self):
        assert list_items(['text', '- first', '* second', '  - third']) == [
            (1, '- first'), (2, '* second'), (3, '- third')]

    def test_meta_row_filter(self):
        assert is_meta_row('文档版本 / v1.0') is True
        assert is_meta_row('状态 / approved') is True
        assert is_meta_row('A-1 / 某个评审点') is False

    def test_bare_short_heading(self):
        # 空标题（下面只有正文）→ 过滤
        assert is_bare_short_heading('2.1 目标', '2.1 目标') is True
        # 短标题但有结构化数据 → 保留（N1 §7 误杀修复）
        assert is_bare_short_heading('7.1 依赖', 'FR-1 / 依赖 / 详见表格') is False
        # 长标题 → 保留
        assert is_bare_short_heading('4.3 阈值定稿 v1（观察期后校准）', '') is False

    def test_norm_dedup_across_sections(self):
        assert norm('**文档版本** v1.0') == norm('文档版本 v1.0')


class TestExtract:
    def test_version_and_id_format(self):
        content = '## A\n\n- x\n'
        assert document_version(content).startswith('sha256:')
        assert len(task8_of('a20f3f9f')) == 8
        pts, _ = extract_review_points(content, 'a20f3f9f')
        assert pts[0]['id'] == 'RP-a20f3f9f-01'
        assert pts[0]['origin'] == 'rule'
        assert pts[0]['status'] == 'candidate'

    def test_idempotent_same_content(self):
        content = '## A\n\n### A.1\n\n| k | v |\n|---|---|\n| x | y |\n'
        a, _ = extract_review_points(content, 'abc12345')
        b, _ = extract_review_points(content, 'abc12345')
        assert [p['id'] for p in a] == [p['id'] for p in b]
        assert [p['claim'] for p in a] == [p['claim'] for p in b]

    def test_skip_meta_sections(self):
        content = ('## 文档信息\n\n| 项 | 值 |\n|---|---|\n| 状态 | approved |\n\n'
                   '## 3. 功能需求\n\n- FR-1 / 定义 / 四级阈值\n')
        pts, stats = extract_review_points(content, 'abcd1234')
        assert all('文档信息' not in p['anchor'] for p in pts)
        assert any('FR-1' in p['claim'] for p in pts)

    def test_h4_drilldown(self):
        """§5.2 任务卡下多张 H4 卡必须各自铺点（N5 0% 根因）。"""
        h4s = '\n'.join('#### N%d 任务卡（30 min）\n\n- 字段 tauMin 派生只读\n'
                        % i for i in range(1, 6))
        content = '## 5. 实施计划\n\n### 5.2 任务卡\n\n' + h4s
        pts, _ = extract_review_points(content, 'abcd1234')
        h4_points = [p for p in pts if '任务卡' in p['anchor'] and 'N' in p['claim']]
        assert len(h4_points) >= 5
        assert any('tauMin' in p['quote'] for p in pts)

    def test_table_and_list_both_paved(self):
        """H3 内表格与列表必须同时铺点（N1 §4.3 漏 5 项根因）。"""
        content = ('## 4. 分级规则\n\n### 4.3 阈值定稿\n\n'
                   '| 档 | 值 |\n|---|---|\n| 黄 | 20 cm |\n\n'
                   '- C-1 液位换算待校准\n- C-5 净高缺失 3 条\n')
        pts, _ = extract_review_points(content, 'abcd1234')
        claims = [p['claim'] for p in pts]
        assert any('黄' in c for c in claims)
        assert any('C-1' in c for c in claims)
        assert any('C-5' in c for c in claims)

    def test_quote_full_not_truncated(self):
        """H4 卡的 quote 必须含第 4 条之后的明细（N5 30% 中间态根因）。"""
        items = '\n'.join('- item%d 关键字段 tauMin' % i for i in range(1, 8))
        content = ('## 5. 计划\n\n### 5.2 任务卡\n\n'
                   '#### N5 阈值配置\n\n' + items + '\n'
                   '#### N6 其他\n\n- yy\n')
        pts, _ = extract_review_points(content, 'abcd1234')
        n5 = next(p for p in pts if 'N5' in p['claim'])
        assert 'item7' in n5['quote']

    def test_dedup_by_content_fingerprint(self):
        content = ('## 4. NFR\n\n### 4.1\n\n- NFR-1 超时 8s\n\n'
                   '## 8. 非功能复核\n\n### 8.1\n\n- NFR-1 超时 8s\n')
        pts, stats = extract_review_points(content, 'abcd1234')
        assert sum('NFR-1' in p['claim'] for p in pts) == 1
        assert stats['dup'] >= 1


class TestH5Matching:
    def test_guard_blocks_false_token(self):
        assert GUARD.search('AC-1 验收') is not None
        text = GUARD.sub(' ', 'AC-1 与 C-1 不同')
        assert 'AC-1' not in text
        assert 'C-1' in text

    def test_numeric_word_boundary(self):
        assert safe_pattern(r'400') == r'\b400\b'
        pts = [_pt(claim='MAX_CODES = 500', quote='2400 条记录')]
        assert find_hit([r'\b400\b'], pts)[0] is None
        assert find_hit([r'\b500\b'], pts)[0] is not None

    def test_context_required_for_status_code(self):
        pts = [_pt(claim='MAX_CODES = 500', quote='配置上限')]
        assert find_hit([r'HTTP\s*500'], pts)[0] is None
        assert find_hit([r'code=500'], pts)[0] is None

    def test_scope_blocks_cross_card_match(self):
        n1 = _pt(claim='N1 卡', quote='tauMin 配置', anchor='§5.2 > N1')
        n5 = _pt(claim='N5 卡', quote='无', anchor='§5.2 > N5')
        assert find_hit([r'tauMin'], [n1, n5], scope='N5 卡')[0] is None
        assert find_hit([r'tauMin'], [n1, n5], scope='N1 卡')[0] is n1

    def test_in_scope_without_scope(self):
        assert in_scope(_pt(claim='x')) is True

    def test_weak_hit_marked_not_counted(self):
        """弱命中由调用方声明；判定器只标记 WEAK，不当 HIT。"""
        class FakeExtract:
            def __call__(self, content, task8):
                return [_pt(claim='G-3 回差 + 最短持续 τ',
                            anchor='2.1 目标', quote='')], {}
        sample = dict(name='N1', scope=None, content='## 占位\n\n- x\n',
                      golden=[('τ倍数', [r'τ'])])
        r = replay([sample], FakeExtract(), weak={('N1', 'τ倍数')})
        assert r['rows'][0]['final'] == 0
        assert r['rows'][0]['weak'] == 1
        assert r['rows'][0]['hits'][0][1] == 'WEAK'

    def test_final_weak_set_has_three_items(self):
        assert len(FINAL_WEAK) == 3
        assert ('N5 阈值配置', 'JsonProperty') in FINAL_WEAK


@pytest.mark.parametrize('content,expected_kind,expected_risk', [
    ('- NFR-1 超时 8s', 'nfr', 'high'),
    ('- FR-1 / 四级阈值定义', 'fr_contract', 'high'),
    ('- 400 / 参数非法', 'error_contract', 'low'),
    ('- AC-1 / 验收标准', 'acceptance', 'low'),
])
def test_kind_and_risk_inference(content, expected_kind, expected_risk):
    pts, _ = extract_review_points('## X\n\n### X.1\n\n' + content, 'abcd1234')
    hit = next(p for p in pts if expected_kind == p['kind'])
    assert hit['risk'] == expected_risk
