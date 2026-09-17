# -*- coding: utf-8 -*-
"""文档生命周期状态机（按用户定义的 8 类文档角色表落地）。

| 类型      | 作用       | 生命周期                             |
|---------|----------|------------------------------------|
| PRD     | 为什么做    | Draft → Approved                   |
| Spec    | 怎么设计    | Draft → Review → Approved          |
| 接口契约  | 怎么调用    | Draft → Review → Approved          |
| ADR     | 为什么这么决定 | Proposed → Accepted → Superseded   |
| Plan    | 怎么实施    | Draft → Approved → Done            |
| Task    | 谁做什么    | Todo → Doing → Done（即任务本身 state 机，非文档）|
| Test    | 是否正确    | Planned → Passed/Failed            |
| Release | 发布了什么   | Planned → Released                 |
| Incident| 出了什么问题  | Open → Resolved → Closed           |

扩展（2026-09-17）：**接口契约 `api`** 与 Spec 同形（`draft → review → approved`）。
用户要求接口契约「事无巨细」，其质量规格是文档链里最严的一份；把它纳入生命周期后，
`set_doc_status` 的质量门控才会作用于它——否则质量 error 仅出现在写入响应与报告里、
不阻断任何推进（这是此前的缺口）。

kind 映射（复用 21+1 类）：PRD→requirement、ADR→decision、Release→milestone、
Incident→incident（新增 kind）、接口契约→api。未列入表的 kind 无生命周期，不支持设状态。
状态存 `task.doc_statuses`（kind -> {state, at, note}），转移事件走 events。
"""

# kind -> 状态机定义；transitions 只允许向前（严格线性，回退走重写文档重新起草）
DOC_LIFECYCLE = {
    'requirement': {   # PRD
        'states': ('draft', 'approved'),
        'transitions': {'draft': ('approved',)},
    },
    'spec': {
        'states': ('draft', 'review', 'approved'),
        'transitions': {'draft': ('review',), 'review': ('approved',)},
    },
    'api': {           # 接口契约（与 spec 同形；质量规格最严，见 doc_quality）
        'states': ('draft', 'review', 'approved'),
        'transitions': {'draft': ('review',), 'review': ('approved',)},
    },
    'decision': {      # ADR
        'states': ('proposed', 'accepted', 'superseded'),
        'transitions': {'proposed': ('accepted',), 'accepted': ('superseded',)},
    },
    'plan': {
        'states': ('draft', 'approved', 'done'),
        'transitions': {'draft': ('approved',), 'approved': ('done',)},
    },
    'test': {
        'states': ('planned', 'passed', 'failed'),
        'transitions': {'planned': ('passed', 'failed')},
    },
    'milestone': {     # Release
        'states': ('planned', 'released'),
        'transitions': {'planned': ('released',)},
    },
    'incident': {
        'states': ('open', 'resolved', 'closed'),
        'transitions': {'open': ('resolved',), 'resolved': ('closed',)},
    },
}

# kind -> 初始状态（文档首次写入时自动落位）
INITIAL_STATE = {
    'requirement': 'draft',
    'spec': 'draft',
    'api': 'draft',
    'decision': 'proposed',
    'plan': 'draft',
    'test': 'planned',
    'milestone': 'planned',
    'incident': 'open',
}


def lifecycle_of(kind):
    """返回该 kind 的生命周期定义；无生命周期的 kind 返回 None。"""
    return DOC_LIFECYCLE.get(kind)


def initial_state_of(kind):
    """该 kind 的初始状态；无生命周期返回 None。"""
    return INITIAL_STATE.get(kind)


def allowed_next(kind, state):
    """当前状态的合法后继状态列表（无生命周期/终态 → 空列表）。"""
    lc = DOC_LIFECYCLE.get(kind)
    if not lc or state not in lc['transitions']:
        return []
    return list(lc['transitions'][state])


def validate_transition(kind, current, target):
    """校验状态转移。返回 None 表示合法，否则返回错误文案。"""
    lc = DOC_LIFECYCLE.get(kind)
    if not lc:
        return f"kind '{kind}' has no document lifecycle"
    if target not in lc['states']:
        return (f"invalid state '{target}' for kind '{kind}'; "
                f"states: {', '.join(lc['states'])}")
    if current is None:
        if target != INITIAL_STATE.get(kind):
            return (f"first status of '{kind}' must be "
                    f"'{INITIAL_STATE.get(kind)}', got '{target}'")
        return None
    if current == target:
        return f"kind '{kind}' is already '{current}'"
    if target not in allowed_next(kind, current):
        return (f"illegal transition {current} -> {target} for kind '{kind}'; "
                f"allowed: {', '.join(allowed_next(kind, current)) or 'none (terminal)'}")
    return None
