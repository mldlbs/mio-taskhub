# -*- coding: utf-8 -*-
"""研发评审会：rule-only 评审点提取器（正式模块）。

由 E:/tmp/review-meeting-docs/probe_batch_v2.py 转正。
不调 LLM、不写库，只按文档结构铺候选点；LLM 增强在调用方做。

固化下来的规则修复（勿回退，均有 H5 回放证据）：
1. 泛化铺点：不硬编码章节名，按 H2 → H3 → H4 → 表格行/列表项逐层下钻。
   H3 内有 ≥2 个 H4 必须下钻（§5.2 任务卡曾被吞成 1 点，N5 命中 0%）。
2. 表格 AND 列表并存：H3 内两者都逐条铺点（原 if/else 让 §4.3 列表全丢，N1 漏 5 项）。
3. 元信息过滤：SKIP_SECTIONS 整节跳过；META_ROW_KEYS 过滤 key/value 行。
4. 短标题过滤：仅过滤「编号+短词」且 quote 无结构化数据的空标题；
   短标题但下面有表格/列表的（§7.1 依赖、§7.2 待确认项）必须保留。
5. 内容指纹去重：norm() 归一化后全局去重，同内容只留一个点（NFR 行不改章节名）。
6. 版本口径：sha256: 前缀 + 16 位摘要，与 ReadEvidence 同源。

幂等：同 content + 同 task_id → 同 id 集合；quote 取全量列表项（600 字），不截断。
"""
import hashlib
import re

from ..doc_quality import _sections_of, _split_by_heading

MAX_PER_SECTION = 12
QUOTE_MAX = 600
CLAIM_MAX = 120

SKIP_SECTIONS = (
    '文档信息', '变更记录', '附录', '实施记录', '校验记录', '路径核对记录',
)

META_ROW_KEYS = (
    '文档版本', '最后更新', '状态', '编写日期', '维护者', '需求编号',
    '承接任务', '算法依据', '关联文档', '覆盖模块', '后端模块', '负责人',
    '适用范围', '契约版本', '任务', '评审日期', '版本', '日期', '作者',
)

HIGH_KW = ('待确认', '依赖', '风险', '缺失', '否决', '异常', '边界', '冲突',
           '遗留', '未定', '不确定', '阈值', '判据', '主键', '鉴权', 'p95',
           '超时', 'observe')
MED_KW = ('fr-', 'nfr-', '接口', '字段', '幂等', '回差', '采样', '昼夜')


def document_version(content: str) -> str:
    """正文版本：sha256: + 16 位摘要（与 ReadEvidence 同口径）。"""
    return 'sha256:' + hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


def task8_of(task_id: str) -> str:
    """从 task_id 取 8 位 hex，用作 RP-id 中段；不足补 0。"""
    m = re.search(r'[0-9a-fA-F]{2,8}', str(task_id))
    raw = m.group(0) if m else hashlib.md5(str(task_id).encode('utf-8')).hexdigest()
    return raw.lower().zfill(8)[:8]


def _cells_of(line: str) -> list:
    return [c.strip() for c in line.strip().strip('|').split('|')]


def _is_sep(cells: list) -> bool:
    return bool(cells) and all(re.fullmatch(r':?-{2,}:?', c) for c in cells)


def data_rows(body: list) -> list:
    """表格数据行（跳过表头与分隔行），返回 [(行号, 拼接文本)]。"""
    out, seen_sep = [], False
    for i, line in enumerate(body):
        s = line.strip()
        if not s.startswith('|'):
            continue
        cells = _cells_of(s)
        if _is_sep(cells):
            seen_sep = True
            continue
        if not seen_sep:
            continue
        out.append((i, ' / '.join(cells)))
    return out


def list_items(body: list) -> list:
    """markdown 列表项，返回 [(行号, 原文行)]。"""
    return [(i, line.strip()) for i, line in enumerate(body)
            if re.match(r'^\s*[-*]\s+', line)]


def is_meta_row(claim: str) -> bool:
    """两列 key/value 行且 key 命中元信息词 → True。"""
    parts = [p.strip() for p in claim.split('/')]
    if len(parts) == 2 and len(parts[0]) <= 12:
        return any(k == parts[0] or k in parts[0] for k in META_ROW_KEYS)
    return False


def is_bare_short_heading(claim: str, quote: str) -> bool:
    """过滤「编号+短词」且 quote 无结构化数据的空标题。

    修 2026-11-26：原实现按标题长度一律过滤，误杀 §7 依赖/待确认项
    （下面有表格）。判据必须同时看 quote 是否只有标题本身。
    """
    if not re.match(r'^\d+(?:\.\d+)+\s', claim.strip()):
        return False
    stripped = re.sub(r'^\d+(?:\.\d+)*\s*', '', claim.strip())
    if len(stripped) > 8:
        return False
    if quote and quote.strip() != claim.strip():
        return False
    return True


def infer_kind(text: str, title: str) -> str:
    """按行内容 + 章节标题推断类型，不依赖硬编码章节名。"""
    s = (text + ' ' + title).lower()
    if re.search(r'\bnfr\s*[-–]?\s*\d', s):
        return 'nfr'
    if re.search(r'\bfr\s*[-–]?\s*\d', s):
        return 'fr_contract'
    if re.search(r'\bac\s*[-–]?\s*\d', s):
        return 'acceptance'
    if re.search(r'\bc\s*[-–]\s*\d|\bq\d', s):
        return 'risk_assumption'
    if '错误码' in title or re.search(r'\b[45]\d\d\b', text):
        return 'error_contract'
    if '幂等' in title or '重试' in title:
        return 'idempotency'
    if '兼容' in title or '废弃' in title or 'deprecat' in s:
        return 'compat'
    if '范围' in title or '边界' in title:
        return 'scope_boundary'
    if '认证' in title or '鉴权' in title or '鉴权' in text:
        return 'premise'
    if '超时' in title or '并发' in title or '性能' in title:
        return 'risk'
    if '依赖' in title or '待确认' in title or '遗留' in title or '风险' in title:
        return 'risk_assumption'
    if '接口' in title or '枚举' in title:
        return 'functional'
    if '非功能' in title:
        return 'nfr'
    if '数据' in title or '字段' in title:
        return 'data_rule'
    return 'section_gap'


def infer_risk(text: str, title: str) -> str:
    s = (text + ' ' + title).lower()
    if any(k in s for k in HIGH_KW):
        return 'high'
    if any(k in s for k in MED_KW):
        return 'medium'
    return 'low'


def norm(claim: str) -> str:
    """内容指纹归一化：去标点空白，用于跨文档去重。"""
    return re.sub(r'[\s\*`\|\-—–/（）()【】\[\]：:，,。.]', '', claim).lower()


def extract_review_points(content: str, task_id: str, kind: str = None):
    """按文档结构铺评审点。

    返回 (points, stats)。points 每项含：
    id / kind / risk / section / anchor / claim / quote /
    dedup_key / origin=rule / status=candidate。
    stats 含 dup / sections（章节铺点统计）。
    """
    sections = _sections_of(content)
    prefix = task8_of(task_id)
    points = []
    seen = set()
    dup_count = 0
    seq = 0
    section_stats = []

    def _pave(body, anchor, limit=None):
        """把一段正文铺成 [(anchor_suffix, claim, quote)]。

        递归下钻：H3 → H4 → 表格行 + 列表项，每一层都检查，不再以
        「子节数量 >=2」为前提（单 H3 下仍有 H4 任务卡是真实场景）。
        """
        out = []
        subs = _split_by_heading(body, '###')
        if len(subs) >= 2:
            for h, hb in subs:
                out.extend(_pave(hb, anchor + ' > ' + h, limit))
            return out
        h4s = _split_by_heading(body, '####')
        if len(h4s) >= 2:
            for h4, h4b in h4s:
                items = list_items(h4b)
                if items:
                    sub_line = ' '.join(x[1] for x in items)
                else:
                    sub_line = ' / '.join(x[1] for x in data_rows(h4b))
                out.append((anchor + ' > ' + h4, h4[:CLAIM_MAX],
                            sub_line[:QUOTE_MAX]))
            return out
        rows = data_rows(body)
        items = list_items(body)
        for ri, claim in rows:
            out.append((anchor + ' 表L%d' % (ri + 1),
                        claim[:CLAIM_MAX], claim[:QUOTE_MAX]))
        for li, line in items:
            claim = re.sub(r'^\s*[-*]\s+', '', line)
            out.append((anchor + ' 列L%d' % (li + 1),
                        claim[:CLAIM_MAX], line[:QUOTE_MAX]))
        if not rows and not items:
            head = anchor.split(' > ')[-1]
            out.append((anchor, head[:CLAIM_MAX], head[:200]))
        return out

    for title, body in sections:
        if any(k in title for k in SKIP_SECTIONS):
            section_stats.append((title, 0, 'skip-meta'))
            continue

        units = _pave(body, title)
        added = 0
        for anchor, claim, quote in units:
            if is_meta_row(claim) or is_bare_short_heading(claim, quote):
                dup_count += 1
                continue
            key = norm(claim)
            if not key or key in seen:
                dup_count += 1
                continue
            seen.add(key)
            seq += 1
            points.append(dict(
                id='RP-%s-%02d' % (prefix, seq),
                kind=infer_kind(claim, title),
                risk=infer_risk(claim, title),
                section=title,
                anchor=anchor,
                claim=claim,
                quote=quote,
                dedup_key=key,
                origin='rule',
                status='candidate',
                source_kind=kind,
            ))
            added += 1
        section_stats.append((title, added, 'recursive'))

    return points, {'dup': dup_count, 'sections': section_stats}
