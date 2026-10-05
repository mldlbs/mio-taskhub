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
#   sections: 必需 H2 章节标题（子串匹配，忽略编号）——缺失记 error（阻断状态推进）
#   recommended: 建议 H2 章节标题——缺失记 warn（不阻断，但计入评分）
#   min_table_rows: {章节标题子串: 最少数据行数}
#   detail_rule: 明细单元完整性（如「接口契约的每个接口」必须写全子块）
QUALITY_SPEC = {
    'requirement': {
        'sections': ['文档信息', '背景与问题', '目标与非目标', '功能需求', '非功能需求'],
        'min_table_rows': {'文档信息': 2, '功能需求': 1},
    },
    'architecture': {
        'sections': ['文档信息', '系统上下文', '总体架构', '关键数据流'],
        'min_table_rows': {'文档信息': 2},
    },
    'spec': {
        'sections': ['文档信息', '模块职责与边界', '详细设计'],
        'min_table_rows': {'文档信息': 2},
    },
    'data-model': {
        'sections': ['文档信息', '实体与字段', '状态机'],
        'min_table_rows': {'文档信息': 2, '实体与字段': 1},
    },
    # 接口契约是唯一一份「必须事无巨细」的文档：字段级、错误码级、示例级都要落地。
    # 因此必需章节数量最多，且额外用 detail_rule 约束「每个接口」都要写全四个子块。
    'api': {
        'sections': [
            '文档信息', '文档信息与范围', '环境与基础地址', '认证与鉴权', '通用响应结构',
            '统一错误码', '字段命名与类型规范', '幂等性与重试',
            '接口清单', '接口明细', '变更记录',
        ],
        'recommended': [
            '版本策略', '通用请求头', '分页', '时间 / 数值 / 空值约定', '枚举全集',
            '限流与配额', '超时与并发', '文件上传与下载', '安全与脱敏',
            '兼容性与废弃策略', '附录',
        ],
        'min_table_rows': {'文档信息': 2, '通用响应结构': 1, '统一错误码': 1, '接口清单': 1},
        'detail_rule': {
            'section': '接口明细',
            'unit_label': '接口',
            'min_units': 1,
            # 每个接口小节（### ）内必须有的子块（#### ）-> 最少表格数据行
            # 0 表示只要求该子块存在（如「示例」多为代码块，不强制表格）
            'unit_blocks': {'请求参数': 1, '响应字段': 1, '错误码': 1, '示例': 0},
        },
    },
    'test': {
        'sections': ['文档信息', '验收标准', '测试范围', '用例清单'],
        'min_table_rows': {'文档信息': 2, '用例清单': 1},
    },
    'runbook': {
        'sections': ['文档信息', '环境与依赖', '部署步骤', '回滚'],
        'min_table_rows': {'文档信息': 2},
    },
}

# 状态机推进到这些目标状态时做质量门控（errors=0 才放行）
GATED_TARGETS = ('review', 'approved', 'done')

# 「文档信息」元信息节（模板由 doc_chain.render_chain 统一注入；见 doc_chain.info_block）
INFO_TITLE = '文档信息'
INFO_DATETIME_PLACEHOLDER = 'YYYY-MM-DD HH:MM'
INFO_DATE_PLACEHOLDER = INFO_DATETIME_PLACEHOLDER   # 兼容旧引用
# 占位判定：同时命中新格式（含时间）与旧格式（仅日期），避免历史文档漏检
_PLACEHOLDER_RE = re.compile(r'YYYY-MM-DD(?:[ T]HH:MM)?')

# 高规格文档的最低质量分：即使 errors=0，分数低于此线也不得推进（force 可绕过）。
# 依据：需求/设计/契约是全链里规格最严的三份，「0 error 但一堆章节缺失」不应算达标
# （2026-09-23：实测某任务 api 45 分、缺 11 个建议章节，仍被批准，故加分数底线）。
MIN_SCORE = {
    'requirement': 80,
    'spec': 80,
    'api': 80,
}

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
        # ── 必需章节 ──
        '文档信息与范围': '文档版本/契约版本/状态/最后更新/负责人/适用范围，并写清「明确不含」什么，防止被当成全量接口清单',
        '环境与基础地址': '每个环境一行（dev/staging/prod）给完整 Base URL，禁止写「同上」；经网关导致路径前缀被剥离或重写的必须写明',
        '认证与鉴权': '认证方式、凭证来源与传递位置、有效期与刷新、权限模型；401（未认证）与 403（越权）必须区分，并写明各自业务码',
        '通用响应结构': '成功失败共用同一外壳，逐字段给类型与必填；data 为 null 与 {} 的语义差异要写明；有返回裸数组/二进制的接口必须在此显式列为例外',
        '统一错误码': '每行写全 HTTP 状态 + 业务码 + 含义 + 触发条件 + 处理建议；禁止只写「参数错误」，要指明是哪个参数违反了哪条约束',
        '字段命名与类型规范': '命名风格（并给反例）、ID/布尔/金额/数组的类型约定；「字段缺失」与「显式 null」的区别必须写明——这是调用方最容易踩的坑',
        '幂等性与重试': '逐接口标明是否幂等、幂等键如何生成与有效期、重复提交返回什么；列出可安全重试与绝不可重试的状态码，给退避策略',
        '接口清单': '一行一个接口，列 Method + Path + 用途 + 权限 + 幂等 + 限流 + 关联状态；必须与接口明细一一对应，不允许只在清单出现而无明细',
        '接口明细': '每个接口一个「### <Method> <Path>」小节，必须含四个子块：请求参数 / 响应字段 / 错误码 / 示例；前三个子块各需一张有数据行的表',
        '变更记录': '每次改契约追加一行（日期 + 契约版本 + 变更内容 + 兼容/破坏 + 影响方），破坏性变更标出影响方并链接通知记录',
        # ── 建议章节 ──
        '版本策略': '版本放在哪里（路径/请求头/查询参数，选一种写明）、版本号规则、多版本并存与旧版本下线流程',
        '通用请求头': '表格列出每个 Header 的必填性/类型/默认值/说明；自定义追踪头要写明是否透传回响应',
        '分页': '分页与排序过滤的参数名、默认值、上限；响应分页字段含义；游标 vs 偏移的选择理由；超限是截断还是报错、报哪个码',
        '时间 / 数值 / 空值约定': '时间格式与时区（UTC 还是本地）、精度；数值范围与溢出处理；空值语义——这几项不统一会导致跨服务对接反复返工',
        '枚举全集': '全项目枚举集中登记（枚举名 + 取值 + 含义 + 出现在哪些接口），明细里只引用不重复定义；写明调用方必须能处理未知取值',
        '限流与配额': '逐范围给阈值/窗口/超限响应；429 是否带 Retry-After 要写明，否则调用方无法做退避',
        '超时与并发': '服务端处理超时与建议客户端超时；乐观锁字段与并发冲突响应；长任务是同步阻塞还是异步返回任务 ID + 轮询',
        '文件上传与下载': '上传方式（multipart/分片/预签名 URL）、单文件上限、允许类型、下载与鉴权方式；不涉及文件则整节删除而不是留空',
        '安全与脱敏': '敏感字段清单及脱敏规则、传输要求、日志禁止记录的字段、越权与注入防护',
        '兼容性与废弃策略': '明确定义什么算破坏性变更、什么算兼容变更；通知方式与提前期；废弃三阶段（标记→双写双读→下线）各阶段时长；契约校验方式',
        '附录': '状态码全集 / 错误码全集 / 枚举全集 / 数据字典，与正文保持一致；列出关联文档（状态模型、测试验收）链接',
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

# error 文案的修法提示（按插入顺序匹配，越具体越靠前）
_ISSUE_HINTS = {
    '缺少必需章节': '补写该章节（见 hint）；确属不适用的章节可整节删除后重写，但必需章节不可缺',
    '缺少建议章节': '补写该章节（见 hint）能让契约更完整；确属不适用可留空缺省，但会扣分',
    '未填写': '把模板指引注释 <!-- --> 替换为实际内容；不适用的小节可删除',
    '明细不足': '把每个接口写成一个独立小节「### <Method> <Path>」，直接从模板范例整块复制骨架',
    '子块': '接口小节内必须含 请求参数 / 响应字段 / 错误码 / 示例 四个「#### 」子块，'
            '且前三个各配一张至少 1 行数据的表——参数名、类型、必填、约束、示例都不能省',
    '表格数据行不足': '在表头下补数据行；确实无内容则说明该文档不适用此规格，需检查 kind 是否选对',
    '链接目标不存在': '修正链接路径，或先生成目标文档（taskhub_scaffold_docs 可一次补齐全链）',
    '缺少「': '按模板复制该子块的表头并逐行填全；字段级细节不能省',
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


def _split_by_heading(lines, prefix='###'):
    """按指定级别的标题切分为 [(标题, 正文行列表)]。

    用于明细单元解析：prefix='###' 切接口小节，prefix='####' 切小节内的子块。
    更高级别的标题（#### 在 ### 切分时）会落入正文，不会误切。
    """
    out, cur, body = [], None, []
    marker = prefix + ' '
    for line in lines:
        if line.startswith(marker):
            if cur is not None:
                out.append((cur, body))
            cur, body = line[len(marker):].strip(), []
        elif cur is not None:
            body.append(line)
    if cur is not None:
        out.append((cur, body))
    return out


def _table_data_rows(body_lines):
    """章节内所有 markdown 表格的数据行总数（表头行与分隔行都不算）。

    判定规则：以 `|` 开头的行中，排除分隔行（`|---|`），排除「下一行是分隔行」
    的表头行，剩下的才是数据行。注意必须排除分隔行本身——否则 0 数据行的空表
    会被算成 1 行，导致 min_table_rows 永远满足（历史缺陷，2026-09-17 修复）。
    """
    rows = 0
    for i, line in enumerate(body_lines):
        s = line.strip()
        if not s.startswith('|') or _TABLE_SEP_RE.match(s):
            continue
        nxt = body_lines[i + 1].strip() if i + 1 < len(body_lines) else ''
        if _TABLE_SEP_RE.match(nxt):
            continue  # 表头行
        rows += 1
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

    # 1a) 「文档信息」的『最后更新』不得留占位符（推进状态时系统会自动写入真实日期时间）
    info_hit = next(((t, b) for t, b in sections if INFO_TITLE in t), None)
    if info_hit and _PLACEHOLDER_RE.search(_section_body(info_hit[1])):
        errors.append('「文档信息」的『最后更新』仍是占位符 %s'
                      '（修法：填真实日期时间；推进状态时系统会自动写入）'
                      % INFO_DATETIME_PLACEHOLDER)

    # 2) 必需表格数据行
    for tbl, min_rows in spec.get('min_table_rows', {}).items():
        hit = next(((t, b) for t, b in sections if tbl in t), None)
        if hit and '<!--' not in _section_body(hit[1]):
            if _table_data_rows(hit[1]) < min_rows:
                errors.append(f'章节「{hit[0]}」表格数据行不足（需 ≥{min_rows}）')

    # 2a) 建议章节缺失 → warn（不阻断，但计入评分）
    for rec in spec.get('recommended', []):
        if not any(rec in t for t in titles):
            warns.append(f'缺少建议章节「{rec}」')

    # 2b) 明细单元完整性：每个单元必须写全指定子块，且子块表格有数据行
    #     （接口契约的「每个接口都要有 请求参数/响应字段/错误码」就靠这条兜住）
    detail = spec.get('detail_rule')
    if detail:
        holder = next(((t, b) for t, b in sections if detail['section'] in t), None)
        if holder and '<!--' not in _section_body(holder[1]):
            label = detail.get('unit_label', '单元')
            units = _split_by_heading(holder[1], '###')
            min_units = detail.get('min_units', 1)
            if len(units) < min_units:
                errors.append(f'{label}明细不足：需 ≥{min_units} 个（当前 {len(units)}）')
            for u_title, u_body in units:
                subs = _split_by_heading(u_body, '####')
                for block, min_rows in detail.get('unit_blocks', {}).items():
                    sub = next((b for t, b in subs if block in t), None)
                    if sub is None:
                        errors.append(f'{label}「{u_title}」缺少「{block}」子块')
                    elif min_rows and _table_data_rows(sub) < min_rows:
                        errors.append(
                            f'{label}「{u_title}」的「{block}」子块表格数据行不足（需 ≥{min_rows}）')

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
