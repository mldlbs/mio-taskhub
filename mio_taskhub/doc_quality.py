# -*- coding: utf-8 -*-
"""文档质量校验器（写阶段拦住空心文档）。

三层手段中「写阶段能自动解决」的部分：
- 结构完整性：QUALITY_SPEC 定义的必需章节是否存在、章节内是否残留模板指引注释
- 填充度：必需表格是否有数据行、全文 TODO/指引注释残留数
- 链接有效：markdown 链接指向的 workspace 内 .md 文件是否存在
- 追溯缺口：requirement 的 FR-n 是否被 test 用例矩阵引用（跨文档，报告层）

写阶段解决不了的（内容对错）交由生命周期 review 流程（doc_lifecycle.py）。

质量分：score = 100 - 20*errors - 5*warns（下限 0）。
门控：推进到 review/approved/done 时 errors 必须为 0（可 force 绕过留痕）。
"""

import re

# kind -> 质量规格；未列入的 kind 无质量校验（quality=None）
#   sections: 必需 H2 章节标题（子串匹配，忽略编号）
#   min_table_rows: {章节标题子串: 最少数据行数}
QUALITY_SPEC = {
    'requirement': {
        'sections': ['背景与问题', '目标与非目标', '功能需求', '非功能需求'],
        'min_table_rows': {'功能需求': 1},
    },
    'architecture': {
        'sections': ['系统上下文', '总体架构', '关键数据流'],
        'min_table_rows': {},
    },
    'spec': {
        'sections': ['模块职责与边界', '详细设计'],
        'min_table_rows': {},
    },
    'data-model': {
        'sections': ['实体与字段', '状态机'],
        'min_table_rows': {'实体与字段': 1},
    },
    'api': {
        'sections': ['全局约定', '接口清单', '接口明细'],
        'min_table_rows': {'接口清单': 1},
    },
    'test': {
        'sections': ['验收标准', '测试范围', '用例清单'],
        'min_table_rows': {'用例清单': 1},
    },
    'runbook': {
        'sections': ['环境与依赖', '部署步骤', '回滚'],
        'min_table_rows': {},
    },
}

# 状态机推进到这些目标状态时做质量门控（errors=0 才放行）
GATED_TARGETS = ('review', 'approved', 'done')

# 各章节「该写什么」——把质量问题反哺成写作指引（revision prompt 的素材）
SECTION_HINTS = {
    'requirement': {
        '背景与问题': '写清现状、痛点、为什么现在做；引用真实场景或数据，不写空话',
        '目标与非目标': '目标可衡量（有指标/状态）；非目标明确排除项，防止范围蔓延',
        '功能需求': '表格逐条列：FR-n 编号 + 需求描述 + 优先级(P0/P1/P2) + 可验收的验收标准；避免「支持xx等」模糊表述',
        '非功能需求': '性能给具体数值（QPS/延迟/容量），安全写清边界与鉴权要求',
        '边界与约束': '时间、资源、合规、依赖的既有系统约束',
    },
    'architecture': {
        '系统上下文': '画或写清系统与外部依赖的关系（谁调用谁、数据流向）',
        '总体架构': '分层/模块划分 + 各模块一句话职责；复杂逻辑给 mermaid 图',
        '关键技术决策（ADR 摘要）': '决策 + 备选方案 + 选择理由，可追溯（对应 decision 文档/ADR-n）',
        '关键数据流': '核心链路从请求到返回走一遍，标注存储与转换点',
    },
    'spec': {
        '模块职责与边界': '一句话说清做什么、不做什么；边界模糊是返工主因',
        '输入 / 输出': '明确入参出参（类型/格式），隐式依赖写出来',
        '详细设计': '类/函数/算法/时序；复杂分支给流程图；涉及文件列清单',
        '异常与边界情况': '枚举失败场景与处理方式（抛错/降级/重试）',
    },
    'data-model': {
        '实体与字段': '表格列全：字段/类型/约束(唯一键、非空)/说明；含状态字段的枚举值',
        '状态机': '转移表：当前状态+事件→下一状态+副作用；非法转移的处理方式写明',
        '完整性约束': '唯一键、外键、业务不变量（invariant）',
        '迁移策略': '存量数据怎么迁、失败怎么回滚',
    },
    'api': {
        '全局约定': '鉴权方式、错误码结构、分页/过滤约定、版本策略',
        '接口清单': '表格列：Method + Path + 用途 + 权限；一览全量接口',
        '接口明细': '每个接口：请求/响应字段表 + 示例 + 错误码；字段给类型与是否必填',
    },
    'test': {
        '验收标准': '逐条对应需求规格的 FR-n 编号，全部 FR 被覆盖才可验收',
        '测试范围': '单元/集成/E2E 各层测什么、不测什么',
        '用例清单': 'TC-n 编号 + 覆盖的 FR-n + 前置/步骤/预期；编号必须能与需求对上',
        '验收流程': '谁验收、通过条件、不通过的回退路径',
    },
    'runbook': {
        '环境与依赖': '运行环境、版本要求、外部服务依赖写全（缺一项部署就卡）',
        '部署步骤': '从构建到上线的可执行命令序列，每步可复制粘贴执行',
        '回滚': '回滚触发条件 + 步骤；没有回滚方案的部署不算完成',
        '配置项': '表格列：配置名/默认值/说明',
    },
}

# error 文案的修法提示
_ISSUE_HINTS = {
    '缺少必需章节': '补写该章节（见 hint）；确属不适用的章节可整节删除后重写，但必需章节不可缺',
    '未填写': '把模板指引注释 <!-- --> 替换为实际内容；不适用的小节可删除',
    '表格数据行不足': '在表头下补数据行；确实无内容则说明该文档不适用此规格，需检查 kind 是否选对',
    '链接目标不存在': '修正链接路径，或先生成目标文档（taskhub_scaffold_docs 可一次补齐全链）',
}

_MD_LINK_RE = re.compile(r'\[[^\]]*\]\(([^)]+)\)')
_TABLE_SEP_RE = re.compile(r'^\|[\s:\-|]+\|\s*$')


def _sections_of(content: str):
    """解析 H2 章节标题 -> (标题行, 正文行列表)。忽略编号差异按子串匹配。"""
    out, cur_title, cur_body = [], None, []
    for line in content.splitlines():
        if line.startswith('## '):
            if cur_title is not None:
                out.append((cur_title, cur_body))
            cur_title, cur_body = line[3:].strip(), []
        elif cur_title is not None:
            cur_body.append(line)
    if cur_title is not None:
        out.append((cur_title, cur_body))
    return out


def _table_data_rows(body_lines):
    """章节内所有 markdown 表格的数据行总数（表头/分隔行不算）。"""
    rows, in_table = 0, False
    for i, line in enumerate(body_lines):
        s = line.strip()
        if s.startswith('|'):
            nxt = body_lines[i + 1].strip() if i + 1 < len(body_lines) else ''
            if _TABLE_SEP_RE.match(nxt):
                in_table = True
                continue
            if in_table:
                rows += 1
        else:
            in_table = False
    return rows


def _section_body(section_body_lines):
    return '\n'.join(section_body_lines)


def _hint_for(kind: str, msg: str) -> str:
    """按问题文案匹配修法提示；缺章节类附上该章节的写作指引。"""
    if '缺少必需章节' in msg:
        import re as _re
        m = _re.search(r'「(.+?)」', msg)
        sec = m.group(1) if m else ''
        return SECTION_HINTS.get(kind, {}).get(sec, '补写该章节；确属不适用可整节删除，但必需章节不可缺')
    for key, hint in _ISSUE_HINTS.items():
        if key in msg:
            return hint
    return '参照文档链模板（taskhub_scaffold_docs 可重新生成对照）'


def _attach_hints(kind: str, issues):
    return [f'{msg}（修法：{_hint_for(kind, msg)}）' for msg in issues]


def check_content(kind: str, content, ws_dir=None) -> dict:
    """校验一份文档内容，返回 {score, errors, warns, todo_left}（均含修法提示）。

    content 为 None（文件缺失）时返回单条 error。
    ws_dir 用于校验 markdown 链接指向的文件是否存在（None 则跳过链接检查）。
    """
    spec = QUALITY_SPEC.get(kind)
    if not spec:
        return None
    if content is None:
        return {'score': 0,
                'errors': ['文档文件不存在或未写入（修法：PUT /doc 写入正文或 taskhub_scaffold_docs 生成骨架）'],
                'warns': [], 'todo_left': 0}

    errors, warns = [], []
    sections = _sections_of(content)
    titles = [t for t, _ in sections]

    # 1) 必需章节存在 + 章节内无残留模板指引注释
    for req in spec['sections']:
        hit = next((t for t in titles if req in t), None)
        if hit is None:
            errors.append(f'缺少必需章节「{req}」')
            continue
        body = _section_body(next(b for t, b in sections if req in t))
        if '<!--' in body:
            errors.append(f'必需章节「{hit}」未填写（残留模板指引注释 <!-- -->）')

    # 2) 必需表格数据行
    for tbl, min_rows in spec.get('min_table_rows', {}).items():
        hit = next(((t, b) for t, b in sections if tbl in t), None)
        if hit and '<!--' not in _section_body(hit[1]):
            if _table_data_rows(hit[1]) < min_rows:
                errors.append(f'章节「{hit[0]}」表格数据行不足（需 ≥{min_rows}）')

    # 3) 链接有效（仅检查指向 workspace 内 .md 的相对链接）
    todo_left = content.count('<!--')
    if ws_dir:
        for m in _MD_LINK_RE.finditer(content):
            target = m.group(1).split('#')[0].strip()
            if not target or target.startswith(('http://', 'https://', '/')):
                continue
            if not target.lower().endswith('.md'):
                continue
            if not (ws_dir / target).is_file():
                warns.append(f'链接目标不存在：{target}')

    # 4) 可选章节的指引注释残留 → warn
    required_hits = {next((t for t in titles if r in t), None) for r in spec['sections']}
    for t, b in sections:
        if t not in required_hits and '<!--' in _section_body(b):
            warns.append(f'章节「{t}」仍有模板指引注释（未填或不需可删）')

    score = max(0, 100 - 20 * len(errors) - 5 * len(warns))
    return {'score': score, 'errors': _attach_hints(kind, errors),
            'warns': _attach_hints(kind, warns), 'todo_left': todo_left}


def revision_prompt(kind: str, content, quality: dict) -> str:
    """把质量结果反哺成可执行的修订指令——写的人（agent 或人）直接照做重写。

    产出一段 markdown：问题清单（带修法）+ 保留范围说明 + 完成标准。
    无问题时返回 None（不需要修订）。
    """
    if not quality or (not quality['errors'] and not quality['warns']):
        return None
    lines = [f'# {kind} 文档修订指令', '',
             f"当前质量分 {quality['score']}/100（{len(quality['errors'])} 个阻断问题、"
             f"{len(quality['warns'])} 个提示）。", '']
    if quality['errors']:
        lines += ['## 必须修复（阻断状态推进）', '']
        lines += [f'- [ ] {e}' for e in quality['errors']]
        lines.append('')
    if quality['warns']:
        lines += ['## 建议修复', '']
        lines += [f'- [ ] {w}' for w in quality['warns']]
        lines.append('')
    lines += ['## 修订要求', '',
              '- 保留已填写的内容，只修改上述问题涉及的章节',
              '- 章节标题保留原文；不适用的小节整节删除（必需章节除外）',
              '- 完成标准：重新写入后 quality.errors 为空，即可推进文档状态',
              '- 写完后用 PUT /doc 重新提交，响应里的 quality 会实时更新']
    return '\n'.join(lines)


def traceability(req_content, test_content) -> dict:
    """需求 FR-n ↔ 测试用例矩阵交叉追溯：找出未被任何用例引用的 FR。"""
    frs = sorted(set(re.findall(r'\bFR-\d+\b', req_content or '')))
    test_text = test_content or ''
    missing = [fr for fr in frs if fr not in test_text]
    return {'fr_total': len(frs), 'fr_uncovered': missing,
            'fr_covered': len(frs) - len(missing)}


def quality_of_file(kind: str, abs_path, ws_dir=None):
    """读文件后校验；文件不存在返回占位报告。"""
    try:
        content = abs_path.read_text(encoding='utf-8', errors='replace') if abs_path.is_file() else None
    except OSError:
        content = None
    return check_content(kind, content, ws_dir)
