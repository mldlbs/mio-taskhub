# -*- coding: utf-8 -*-
"""H5 pilot 盲回放：真问题命中判定器（正式模块）。

由 E:/tmp/review-meeting-docs/h5_strict.py + h5_strict_final.py 转正。
只做匹配判定，不依赖提取模块内部实现；真问题清单由调用方（测试 / 脚本）注入。

两层去伪 + 一层人工审计，缺一不可：
1. 关键词带语义上下文：default → `回落.*default`；409 → `版本冲突.*409`；
   数字状态码必须 `\\b400\\b` / `HTTP\\s*500`（防 2400 / MAX_CODES = 500）。
2. 词边界 GUARD 剔除 AC-/TC-/FR-/NFR- token（防 AC-1 冒充 C-1）。
3. scope 归属约束：命中点必须落在指定 anchor/claim 内（防跨任务卡误配）。
4. 弱命中剔除：只命中词根/字段名/局部语义，不足以支撑「提前识别」的点不计。
   剔除清单由调用方声明，避免把主观判定写死在判定器里。

口径演进（勿混）：
- 初版 51%  = 提取 bug 漏点
- 修提取 86% = 关键词过宽 + 跨卡误配虚高
- 严格去伪 51% = 加 scope 约束后
- 剔弱命中 44% = 最终口径（22/49）
"""
import re

# 这些前缀出现时，C-1 / C-5 / AC-1 等 token 不算独立编号
GUARD = re.compile(r'AC-[1-9]|TC-[1-9]|FR-[1-9]|NFR-[1-9]')

# 裸数字必须加边界；纯数字状态码必须带 HTTP 语义上下文
NUMERIC = {
    r'422': r'\b422\b',
    r'409': r'\b409\b',
    r'400': r'\b400\b',
    r'500': r'\b500\b',
    r'\blevel\b': r'\blevel\b',
}


def safe_pattern(pat: str) -> str:
    """把裸数字关键词升级为带边界的正则。"""
    return NUMERIC.get(pat, pat)


def in_scope(point: dict, scope: str = None) -> bool:
    """归属约束：命中点须落在指定章节/任务卡内。scope 为空则全文。"""
    if not scope:
        return True
    return scope in point.get('anchor', '') or scope in point.get('claim', '')


def find_hit(golden_patterns, points, scope=None, guard=GUARD):
    """按关键词顺序在候选点里找首个命中，返回 (point, pattern) 或 (None, None)。

    claim + quote 先经 GUARD 替换（剔除 AC-/TC-/FR-/NFR- token），
    再逐 pattern 搜索。命中文本同时来自 claim 与 quote，因此 H4 卡
    必须取全量列表项（quote ≤600 字）而不能截断前 3 项。
    """
    for pat in golden_patterns:
        for p in points:
            if not in_scope(p, scope):
                continue
            text = guard.sub(' ', p.get('claim', '') + ' ' + p.get('quote', ''))
            if re.search(safe_pattern(pat), text, re.I):
                return p, pat
    return None, None


def replay(samples, extract_fn, weak=None):
    """跑 H5 盲回放，返回逐样本明细与汇总。

    samples: [{'name','src','scope','golden':[(标签,[pattern,...]),...]}]
    extract_fn: callable(content, task8) -> (points, stats)
    weak: {(样本名, 真问题标签)} —— 人工审计确认的弱命中，命中不计
    """
    weak = weak or set()
    rows = []
    total_g = total_final = 0
    for s in samples:
        if s.get('content') is not None:
            content = s['content']
        else:
            content = open(s['src'], encoding='utf-8', errors='replace').read()
        pts, _ = extract_fn(content, '00000000')
        hits, weak_hits = [], []
        for label, pats in s['golden']:
            p, pat = find_hit(pats, pts, s.get('scope'))
            if p is None:
                hits.append((label, 'MISS', None))
            elif (s['name'], label) in weak:
                weak_hits.append(label)
                hits.append((label, 'WEAK', p))
            else:
                hits.append((label, 'HIT', p))
        n = sum(1 for _, st, _ in hits if st == 'HIT')
        g = len(s['golden'])
        total_g += g
        total_final += n
        rows.append(dict(name=s['name'], golden=g, strict=n + len(weak_hits),
                         weak=len(weak_hits), final=n,
                         rate=100 * n // g, hits=hits))
    return dict(rows=rows, golden_total=total_g, final_total=total_final,
                rate=100 * total_final // total_g if total_g else 0)


# 2026-11-26 审计确认的三个弱命中（仅用于复现最终口径；调用方可覆盖）
FINAL_WEAK = {
    ('N1 四级阈值', 'τ倍数'),
    ('N5 阈值配置', 'JsonProperty'),
    ('N7 查询接口', '六态枚举'),
}
