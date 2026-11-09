"""任务阻塞源聚合：`GET /api/v1/tasks/{task_id}/blockers`。

回答的是「这个任务现在**为什么**动不了」，不是「能不能推进」——
能不能推进仍由 `advance_stage` / `claim` / `_release_dependencies` 判定，
这里只做解释，不新增任何规则。

文案原则（2026-11-04 用户反馈「因为什么阻塞我看不懂」后重写）：
  · 不出现 `stage=implementing` / `state=queued` 这种内部枚举，一律用界面上看得到的
    阶段名（构思/设计/排期/待领取/开发中/待验收）；
  · 不出现 HTTP 路径当操作指引，`todo` 只说**界面上点哪里**（按钮名、tab 名）；
    真需要调接口的场景把命令放 `api` 字段，前端当技术细节折叠显示；
  · 每条固定三段：because（为什么卡）→ consequence（卡住会导致什么）→ todo（怎么解）。

数据源全部取自既有门控（不重复实现，避免两套规则漂移）：

  state_failed        任务自身处于失败态
  runtime_block       `Task.block_reason` 非空（调度器或人工打的标记）
  dependency_dead     前置已 cancelled/failed → 依赖链断，自动放行永不会发生
  dependency_open     等前置完成 = 正常排队（info）；过了 ready 还挂着才是 warning
  discussion_required 下一阶段要求 discussion，一条都没有
  doc_gate            下一阶段累计门槛里的文档已落盘但未 approved
  decision_open       有 open 决策挂在该任务上
  deviation_open      高/严重偏差未定
  subtask_open        子任务排队情况（info，不是问题；当前完成门**不**校验子任务）
  subtask_blocked     子任务里标「阻塞」的条目——真正的阻塞：不额外解决就进行不了
"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.dependency import dependency_satisfied, task_deps
from mio_taskhub.doc_paths import doc_path_of
from mio_taskhub.models import Decision, Subtask, SubtaskStatus, Task

router = APIRouter(prefix="/tasks", tags=["blockers"])

# 正向流水线的下一阶段（done 的合法前置只有 review，cancelled 不是 stage）
_NEXT_STAGE = {
    "brainstorming": "design",
    "design": "planning",
    "planning": "ready",
    "ready": "implementing",
    "implementing": "review",
    "review": "done",
}
_DOC_GATED_STAGES = ("brainstorming", "design", "planning")
_TERMINAL = ("done", "cancelled")

# 界面上的中文名。文案里只准出现这些词，不准出现枚举值。
_STAGE_CN = {
    "brainstorming": "构思",
    "design": "设计",
    "planning": "排期",
    "ready": "待领取",
    "implementing": "开发中",
    "review": "待验收",
    "done": "已完成",
    "cancelled": "已取消",
}
_KIND_CN = {
    "requirement": "需求",
    "spec": "技术方案",
    "api": "接口契约",
    "plan": "实施计划",
    "test": "测试",
    "review": "验收",
    "architecture": "架构",
    "runbook": "运维手册",
    "changelog": "变更记录",
    "discussion": "讨论",
}
_DOC_STATE_CN = {
    "draft": "草稿",
    "review": "评审中",
    "approved": "已批准",
    "passed": "已通过",
    "failed": "未通过",
    "planned": "待执行",
}
_STATE_CN = {
    "queued": "排队中",
    "claimed": "已领取",
    "running": "执行中",
    "retrying": "等待重试",
    "failed": "失败",
    "completed": "已完成",
}

# severity：blocking = 光等没用、必须额外解决才动得了 / warning = 有隐患，
# 推进接口不拦 / info = 正常排队或背景信息，不算问题
#
# 顺序里有因果：open 决策与高偏差是「文档内容定稿」的上游——实施计划的批准
# 以设计问题关闭为前提，所以它们排在 doc_gate 前面，即使自身只是 warning。
_ORDER = {
    "state_failed": 0,
    "runtime_block": 1,
    "dependency_dead": 2,
    "dependency_open": 3,
    "discussion_required": 4,
    "decision_open": 5,
    "deviation_open": 6,
    "doc_gate": 7,
    "subtask_blocked": 8,
    "subtask_open": 9,
}


def _v(x) -> str:
    return str(getattr(x, "value", x))


def _cn(mapping: dict, key: str) -> str:
    return mapping.get(str(key), str(key))


# 性质：这条问题属于哪一类（与 severity 正交）——面板上作为分类标签展示。
# 参照「三方映射说明」的做法：每条都要能归类（门控 / 输入缺口 / 记录缺失 / 外部条件 / 执行顺序），
# 而不是只标一个严重度。
_NATURE = {
    "state_failed": "运行异常",
    "runtime_block": "运行异常",
    "dependency_dead": "前置异常",
    "dependency_open": "执行顺序",
    "discussion_required": "记录缺失",
    "decision_open": "待决策",
    "deviation_open": "记录缺失",
    "doc_gate": "门控",
    "subtask_blocked": "外部条件",
    "subtask_open": "执行顺序",
    "scope_unknown": "输入缺口",
    "acceptance_missing": "输入缺口",
    "deliverable_missing": "输入缺口",
    "doc_unregistered": "输入缺口",
    "doc_file_missing": "输入缺口",
    "doc_gap": "输入缺口",
    "context_docs_missing": "输入缺口",
    "contract_missing": "输入缺口",
    "retry_history": "风险",
    "bounce_history": "风险",
    "high_tier_unconfirmed": "风险",
}

# 「判定依据：…」从正文里抽出来单独成行（evidence），because 只留现象与机制。
# 面板上「依据」是可核查的字段/值与期望值，不是叙述的一部分。
_EVIDENCE_RE = re.compile(r"判定依据[：:]\s*(.+?)(?=。|$)")


def split_evidence(because: str) -> tuple[str, str]:
    """把正文里的「判定依据：…。」拆成 (正文, 依据)。没有就返回 (正文, "")。"""
    m = _EVIDENCE_RE.search(because or "")
    if not m:
        return because or "", ""
    evidence = m.group(1).strip().rstrip("。")
    rest = (because[:m.start()] + because[m.end():]).strip()
    return rest.replace("。。", "。"), evidence


def _item(kind, severity, title, because, consequence, todo="",
          api="", ref_id="", ref_title="", members=None,
          steps=None, owner="", nature="", evidence="") -> dict:
    """一条问题。四要素：because（现象+机制）/ consequence（影响）/ steps（动作）/ owner（谁做）。

    另有 nature（性质分类）与 evidence（判定依据：可核查的字段与期望值）。
    because 里若带「判定依据：…」，自动抽到 evidence，正文不再重复。
    steps 是给人看的编号动作；todo 保留成一句话版本，给只读字符串的调用方（MCP、旧前端）。
    """
    steps = [str(x).strip() for x in (steps or []) if str(x or "").strip()]
    if not todo:
        todo = " ".join(f"{i}) {x}" for i, x in enumerate(steps, 1))
    if not evidence:
        because, evidence = split_evidence(because)
    return {
        "kind": kind,
        "severity": severity,
        "nature": nature or _NATURE.get(kind, ""),
        "title": title,
        "because": because,
        "evidence": evidence,
        "consequence": consequence,
        "todo": todo,
        "steps": steps,
        "owner": owner,
        "api": api,
        "ref_id": ref_id or "",
        "ref_title": ref_title or "",
        "members": list(members or []),
    }


def _doc_state(task: Task, kind: str) -> str:
    entry = (task.doc_statuses or {}).get(kind)
    if isinstance(entry, dict):
        return str(entry.get("state") or "")
    return str(entry or "")


# 子任务标题的拆解约定：编号 + 名称 —— hash（时长[, dep 前置]）【备注】
# 例：N2 文档链一致性修正 api/spec —— 1592b3bd（120min, dep N1）【…】
#     B1 阻塞：数据路线未定（D-0）—— 确认单已生成 …；阻塞 N1 定稿/N4b/N6
_CODE_RE = re.compile(r"^(?P<code>[A-Z]{1,2}-?\d+(?:\.\d+)?[ab]?)\s+(?P<rest>.+)$")
_TAIL_RE = re.compile(r"\s*[-—]{1,2}\s+(?P<tail>.+)$")
_MIN_RE = re.compile(r"(\d+)\s*min")
_DEP_RE = re.compile(
    r"dep\s+((?:[A-Z]{1,2}-?\d+(?:\.\d+)?[ab]?)(?:\s*\+\s*[A-Z]{1,2}-?\d+(?:\.\d+)?[ab]?)*)")
_NOTE_RE = re.compile(r"【(.+?)】")
_HASH_RE = re.compile(r"(?<!/)\b[0-9a-f]{6,10}\b(?!/)")
_PAREN_MIN_RE = re.compile(r"[（(][^（）()]*min[^（）()]*[）)]")
# 正文里能当「子任务编号」读的 token：B1、N12、C4、D-0、N4b……
# 是否真的是编号由 code_names 查表决定，查不到的原样保留（V1、Q-2 这类不会误伤）
_CODE_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,2}-?\d+(?:\.\d+)?[ab]?)(?![A-Za-z0-9])")


def _short_name(name: str, limit: int = 22) -> str:
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    # 展开外层已经用了「」，名字里自带的「」降级成『』，避免嵌套读不清
    n = n.replace("「", "『").replace("」", "』")
    if len(n) <= limit:
        return n
    head = n[: limit - 1]
    # 英文标识符不从中间截断：有空格就退回上一个空格
    if " " in head and re.search(r"[A-Za-z]", head[-1:]):
        head = head[: head.rindex(" ")].rstrip() or head
    return head + "…"


def _expand_codes(text: str, name_map: dict) -> str:
    """把正文里的子任务编号就地展开成「编号「名字」」，同一编号只展开第一次。"""
    if not text or not name_map:
        return text
    seen: set[str] = set()

    def rep(m):
        code = m.group(1)
        if code not in name_map or code in seen:
            return code
        seen.add(code)
        return f"{code}「{_short_name(name_map[code])}」"

    return _CODE_TOKEN_RE.sub(rep, text)


# B 类阻塞的解决路径分类：按条目文本里的明确信号归类，给「具体要做啥」一个落点。
# 顺序即优先级（签字/勾选这类显式动作优先于泛泛的「未定」）。
_FIX_LABELS = {
    "confirm": "审批确认",
    "engineering": "工程修复",
    "assign": "指派责任方",
    "coordinate": "协调上游",
    "data": "补资料",
    "decision": "决策待定",
    "custom": "按说明处理",
}
_FIX_RULES = [
    ("confirm", ("签字", "勾选", "待确认", "待审批", "确认单")),
    ("engineering", ("纯工程", "可修", "工程修复", "漏落", "修掉")),
    ("assign", ("无外部责任方", "没责任方", "无人负责", "没人负责", "无 owner")),
    ("coordinate", ("提频", "上游", "对接", "采样", "接口未", "协调", "对方")),
    ("data", ("未实测", "缺失", "缺资料", "补齐", "补充")),
    ("decision", ("未定", "待定", "路线", "二选一", "怎么选", "方案未", "口径")),
]
_FIX_ACTION = {
    "confirm": "提交确认单，等对方签字",
    "engineering": "排期修复，修完复测",
    "assign": "指定责任方并登记",
    "coordinate": "向上游提出条件变更，取得书面回复",
    "data": "补测缺项，登记到文档",
    "decision": "提交决策，列出选项待定",
}
_FIX_OWNER = {
    "confirm": "业务方",
    "engineering": "本团队",
    "assign": "待指定",
    "coordinate": "上游 / 对接方",
    "data": "需求方",
    "decision": "业务负责人",
}


_FIX_STEPS = {
    "confirm": ["打开条目里写明的确认单或模板，把要确认的选项勾出来",
                "交{owner}签字确认，留书面记录，不要用口头结论",
                "在子任务清单把 {code} 的状态改为「已完成」"],
    "engineering": ["定位成因：按条目写的现象复现一次，确认是哪一段逻辑或数据落库出问题",
                    "修复并重跑受影响的链路，复测通过再收",
                    "在子任务清单把 {code} 的状态改为「已完成」"],
    "assign": ["指定一名负责人，写进子任务的备注里",
               "负责人确认承接，并给出完成时间点",
               "在子任务清单把 {code} 的状态改为「已完成」"],
    "coordinate": ["向上游（{owner}）提出条件变更，写明具体要哪个指标、什么频率",
                   "拿到书面回复，把结论补进相关文档",
                   "在子任务清单把 {code} 的状态改为「已完成」"],
    "data": ["补测缺失项，记录实测值、量纲、时间口径",
             "把结果写进对应文档章节，作为实现的依据",
             "在子任务清单把 {code} 的状态改为「已完成」"],
    "decision": ["列出候选路线及各自代价（工期、依赖、风险）",
                 "交{owner}拍板，把结论登记成一条决策",
                 "在子任务清单把 {code} 的状态改为「已完成」"],
    "custom": ["按条目里写的解决方式执行",
               "在子任务清单把 {code} 的状态改为「已完成」"],
}


def _cause_steps(p: dict, ftype: str, owner: str) -> list[str]:
    tpl = _FIX_STEPS.get(ftype) or []
    code = str(p.get("code") or "该条目")
    return [x.format(owner=owner or "责任方", code=code) for x in tpl]


def _cause_fix(p: dict) -> dict:
    """给 B 类阻塞标注解决路径：一步动作 + 责任方。

    文案口径：陈述句、给动作和责任方，不写劝说语（「别干等」「说死」这类一律不要）。
    """
    text = "；".join(str(p.get(k) or "") for k in ("name", "desc", "note"))
    step_ref = (re.search(r"[（(]([A-Z]{1,2}-?\d+(?:\.\d+)?)[）)]", str(p.get("name") or "")) or [None, ""])[1]
    ref = f"（{step_ref}）" if step_ref else ""
    m = re.search(r"解决[:：]\s*([^\n；;]+)", text)
    if m:
        return {"fix_type": "custom", "fix_label": _FIX_LABELS["custom"],
                "fix_action": m.group(1).strip(), "fix_owner": "",
                "fix_steps": _cause_steps(p, "custom", "")}
    for ftype, kws in _FIX_RULES:
        if any(k in text for k in kws):
            owner = _FIX_OWNER[ftype]
            return {"fix_type": ftype, "fix_label": _FIX_LABELS[ftype],
                    "fix_action": f"{_FIX_ACTION[ftype]}{ref}", "fix_owner": owner,
                    "fix_steps": _cause_steps(p, ftype, owner)}
    return {"fix_type": "", "fix_label": "", "fix_action": "", "fix_owner": "", "fix_steps": []}


def _blocks_items(blocks_txt: str, name_map: dict) -> list[dict]:
    """把「卡住 N1 定稿/N4b/N6 取数」拆成 [{code,name,action}]，界面逐项渲染。

    action 是原句里跟在编号后面的词（定稿 / 取数 / 验证），说明这件事卡住了哪一步。
    """
    out: list[dict] = []
    for tok in re.split(r"[/、,，]", str(blocks_txt or "")):
        tok = tok.strip()
        if not tok:
            continue
        m = re.match(r"^([A-Z]{1,2}-?\d+(?:\.\d+)?[ab]?)\s*(.*)$", tok)
        if not m:
            out.append({"code": "", "name": tok, "action": ""})
            continue
        code, rest = m.group(1), m.group(2).strip()
        # 卡住行只留主干名：去掉括号补充说明，避免「N1（四级阈值推导（客户 V1 口径…，定稿）」这种套娃
        raw = name_map.get(code, "")
        if not raw:
            # N4b 这类分步编号本身不在清单里，退回主编号 N4 的名字
            base = re.match(r"^([A-Z]{1,2}-?\d+)(?:\.\d+)?[ab]$", code)
            if base:
                raw = name_map.get(base.group(1), "")
        plain = re.sub(r"[（(][^（）()]*[）)]", "", raw).strip(" —-")
        # 表里存的是整条标题时，去掉开头的编号，避免出现「N1 N1 四级阈值推导」
        plain = re.sub(r"^[A-Z]{1,2}-?\d+(?:\.\d+)?[ab]?\s+", "", plain).strip(" —-")
        # 并列补充（「四级阈值推导 + 三级迁移说明」）只留第一项，全角半角加号都算分隔
        plain = re.split(r"\s*[+＋]\s*", plain)[0].strip(" —-")
        if not plain:  # 名字整段被括号包住，去括号后为空 → 退回原串，避免渲染出空名
            plain = re.sub(r"\s*[+＋]\s*", " ", raw).strip(" —-")
        name = _short_name(plain, 24) if plain else "清单中无此编号"
        out.append({"code": code, "name": name, "action": rest})
    return out


def _parse_subtask(title: str) -> dict:
    """把拆解器生成的子任务标题拆成结构化字段；解析不了就整句放 name，不丢信息。

    B1–B5 这类「阻塞：…」条目是业务/数据层面的卡点（is_cause=True），
    N 系是工作项；界面据此分两组，B 类的编号才有着落。
    """
    out = {"code": "", "name": "", "mins": None, "deps": [], "note": "",
           "desc": "", "blocks": [], "blocks_txt": "", "is_cause": False}
    s = str(title or "").strip()
    out["name"] = s
    if not s:
        return out
    m = _CODE_RE.match(s)
    if m:
        out["code"] = m.group("code")
        s = m.group("rest").strip()
    body, tail = s, ""
    m2 = _TAIL_RE.search(s)
    if m2:
        body = s[:m2.start()].strip(" —-")
        tail = m2.group("tail").strip()
    out["name"] = body or s
    if not tail:
        pass
    else:
        nm = _NOTE_RE.search(tail)
        if nm:
            out["note"] = nm.group(1).strip()
            tail = (tail[:nm.start()] + " " + tail[nm.end():]).strip()
        mm = _MIN_RE.search(tail)
        if mm:
            out["mins"] = int(mm.group(1))
        dm = _DEP_RE.search(tail)
        if dm:
            out["deps"] = [x for x in re.split(r"\s*\+\s*", dm.group(1)) if x]
        # desc = 去掉已被结构化吸收的部分（hash、时长括号、dep 片段）后剩下的说明
        cleaned = _HASH_RE.sub(" ", tail)
        cleaned = _PAREN_MIN_RE.sub(" ", cleaned)
        cleaned = re.sub(r"dep\s+[A-Za-z0-9+\-/\.,，、]+", " ", cleaned)
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ；;，,。—-")
        # 「卡住谁」单独拎出来（B 条目常以「；阻塞 N6 取数」收尾），desc 只留原因/现状
        bm = re.search(r"[；;，,]\s*阻塞[:：]?\s*(?P<b>.+)$", cleaned)
        if not bm:
            bm = re.match(r"^阻塞[:：]?\s*(?P<b>.+)$", cleaned)
        if bm and bm.group("b").strip():
            out["blocks_txt"] = bm.group("b").strip()
            cleaned = (cleaned[:bm.start()] if bm.start() else "").strip(" ；;，,。—-")
        out["desc"] = cleaned
        # 「不阻塞 N1/N2」是范围说明，不是阻塞对象——前置的「不」要排除掉
        bm2 = re.search(r"(?<!不)阻塞\s+([A-Za-z0-9+\-/\.,，、]+)\s*$", tail)
        if bm2:
            out["blocks"] = [x for x in re.split(r"[/+，,、]", bm2.group(1)) if x]
    cm = re.match(r"阻塞[:：]\s*(.+)$", out["name"])
    if cm:
        out["name"] = cm.group(1).strip()
        out["is_cause"] = True
    return out


def collect_blockers(db: Session, t: Task) -> list[dict]:
    """收集该任务当前的阻塞项（只读，不改任何状态）。"""
    from mio_taskhub.api.task_stages import (  # 复用门控实现，不复制规则
        _doc_written, _gates_upto, _requirement_approved, _stage_requirement,
    )
    from mio_taskhub.completion_gate import _unresolved_deviations

    items: list[dict] = []
    state, stage = _v(t.state), _v(t.stage)
    next_stage = _NEXT_STAGE.get(stage)
    terminal = stage in _TERMINAL or state == "cancelled"

    # 已终结（done / cancelled）的任务没有「动不了」这回事。残留的 block_reason、
    # 前置被取消都属于数据卫生问题（实测任务 4aca25a6 已完成却挂着 2 条 blocking），
    # 出现在阻塞面板里只会误导——这里直接清空。
    if terminal:
        return []

    # ① 失败态本身
    if state == "failed":
        steps = ["打开「动态」tab，看这条任务最后一次执行停在哪一步、报了什么错。"]
        if stage == "review":
            steps.append("失败原因是缺信息（缺文件、缺权限、缺参数）的，补完再点「重试」；"
                         "重试会把任务退回「待领取」从头跑一遍，不是原地续跑。")
            steps.append("产出其实合格、只是自动校验没过，点「人工验收」直接把结果验收掉；"
                         "这条任务确实不做了，点「取消任务」结束，避免一直占着清单。")
        else:
            steps.append("失败原因是缺信息（缺文件、缺权限、缺参数）的，补完再点「重试」；"
                         "重试会把任务退回「待领取」从头跑一遍，不是原地续跑。")
            steps.append("这条任务确实不做了，点「取消任务」结束，避免一直占着清单。")
        items.append(_item(
            "state_failed", "blocking",
            "任务执行失败，流程停住",
            f"这条任务上一次执行没有成功，状态被置成「失败」，目前停在「{_cn(_STAGE_CN, stage)}」阶段。"
            "失败态和「排队」不同：不会有 agent 再领到它，系统也不会自动推进它，"
            "必须有人从下面三条出路里选一条。判定依据：state = failed。",
            "不处理就会一直停在这里：既不重跑，也不会进入下一阶段，占着清单位置却不产出结果。",
            steps=steps,
            owner="任务负责人",
        ))

    # ② 运行时阻塞标记
    reason = str(getattr(t, "block_reason", "") or "").strip()
    if reason:
        items.append(_item(
            "runtime_block", "blocking",
            "任务被标记为阻塞",
            f"任务的 block_reason 字段有值：「{reason}」。这个字段是人工或调度器打的标记，"
            "调度器在挑选要派发的任务时会直接跳过带这个标记的任务，连尝试都不做。"
            "它和失败态的区别是：任务本身没坏，只是被按住了。判定依据：block_reason 非空。",
            "只要这个标记还在，就没有 agent 会领到这条任务。任务会一直停在当前状态，也不会产生重试记录，"
            "从列表上看它「好好地待着」，实际一步都不会动。",
            steps=["确认标记里写的原因是否还存在（相关依赖是否已修好、环境是否已恢复）。",
                   "原因消除后清空 block_reason（PATCH 任务时传空字符串），任务立刻回到正常调度队列。",
                   "原因仍然存在就不要清标记——清了它会带着同样的问题被派出去，失败一次再回来。"],
            owner="标记人 / 任务负责人",
            api=f"PATCH /api/v1/tasks/{t.id} {{\"block_reason\": \"\"}}",
        ))

    # ③ 依赖（前置）
    deps = [d for d in task_deps(t) if d]
    for did in deps:
        p = db.get(Task, did)
        if p is None:
            items.append(_item(
                "dependency_dead", "blocking",
                "依赖指向不存在的任务",
                f"这条任务的 depends_on 里记着任务 {did}，但库里已经查不到这条任务，说明它被删除过。"
                "系统判断「前置是否完成」的做法是去读那条任务的状态，找不到就永远算「没完成」，"
                "所以这不是等一等就能过的问题。判定依据：depends_on 里的 id 在 tasks 表不存在。",
                "本任务的依赖条件永远不成立，永远不会被自动放行进入「待领取」，只能人工改依赖。",
                steps=["打开「依赖」tab，确认这条依赖挂在哪个 id 上。",
                       "把它从 depends_on 里移除并保存，本任务立刻恢复可调度。",
                       "如果前置是被误删的，先恢复前置任务，再重新建立依赖。"],
                owner="任务负责人",
                api=f"PATCH /api/v1/tasks/{t.id} {{\"depends_on\": [...]}}",
                ref_id=did,
            ))
            continue
        if dependency_satisfied(p):
            continue

        p_state, p_stage = _v(p.state), _v(p.stage)
        p_state_cn = _cn(_STATE_CN, p_state)
        p_stage_cn = _cn(_STAGE_CN, p_stage)

        if p_state in ("cancelled", "failed") or p_stage == "cancelled":
            items.append(_item(
                "dependency_dead", "blocking",
                "前置已放弃，依赖无法满足",
                f"前置任务「{p.title}」当前状态是{p_state_cn}（阶段 {p_stage_cn}），它不会再推进到完成。"
                "系统判断本任务能否放行只有一个条件：前置是否完成。前置停在放弃状态，这个条件就永远不成立。"
                "判定依据：前置 state ∈ {cancelled, failed}，或前置 stage = cancelled。",
                "本任务一直等不到放行条件，会一直停在队列里；被指派出去也不会真正开工。",
                steps=["先判断这条前置还要不要做：不做，就把依赖从「依赖」tab 移除。",
                       "还要做，就把前置任务重开或重试，让它重新进入正常流程。",
                       "改完回来看一眼本任务的就绪度，确认这条问题已经消失。"],
                owner="任务负责人",
                api=f"PATCH /api/v1/tasks/{t.id} {{\"depends_on\": [...]}}",
                ref_id=p.id, ref_title=p.title,
            ))
        else:
            blocking_now = stage in _DOC_GATED_STAGES
            if blocking_now:
                # 等前置完成是正常排队，不是阻塞——排完自然轮到，不占「问题」数
                items.append(_item(
                    "dependency_open",
                    "info",
                    "等待前置任务完成",
                    f"前置任务「{p.title}」当前在「{p_stage_cn}」阶段，还没有完成。"
                    "这一条不是问题：系统按前置状态决定本任务什么时候进入「待领取」，"
                    "前置一完成，本任务会被自动放行，不需要人操作。判定依据：本任务尚未到「待领取」，依赖由系统接管。",
                    "不需要处理。唯一成本是等待时间取决于前置任务的进度，本任务自己没法提前。",
                    steps=["不需要为它做任何操作。",
                           "如果这条前置其实不必要（比如需求已经用别的方式满足了），"
                           "在「依赖」tab 移除它可以提前开工。"],
                    owner="前置任务负责人",
                    api=f"PATCH /api/v1/tasks/{t.id} {{\"depends_on\": [...]}}",
                    ref_id=p.id, ref_title=p.title,
                ))
            else:
                items.append(_item(
                    "dependency_open",
                    "warning",
                    "前置未完成，本任务已越过等待层",
                    f"前置任务「{p.title}」还停在「{p_stage_cn}」，但本任务已经推进到「{_cn(_STAGE_CN, stage)}」。"
                    "系统的依赖校验只在进入「待领取」之前做一次，之后不再检查，"
                    "所以这个缺口不会拦住你，同时也意味着没有东西帮你兜底。",
                    "真正开始实现时，可能才发现前置的产出（接口、数据结构、配置）还不存在；"
                    "那时改动已经铺开，返工范围比现在停下来确认要大得多。",
                    evidence=f"depends_on 含 {p.id}，其 stage 为 {_v(p.stage)}；本任务 stage 为 {stage}，"
                             "依赖校验只在进入「待领取」之前执行",
                    steps=["确认这条前置是否真的必须先完成；如果只是形式上的依赖，直接从「依赖」tab 移除。",
                           "如果确实需要，等它完成后再进入实现阶段，或者先做不依赖它的那部分。",
                           "在任务描述里写清「哪一部分依赖前置」，避免执行时误判范围。"],
                    owner="任务负责人",
                    api=f"PATCH /api/v1/tasks/{t.id} {{\"depends_on\": [...]}}",
                    ref_id=p.id, ref_title=p.title,
                ))

    # ④ 下一阶段要求 discussion（需求文档已批准时豁免：方向确认由需求文档承担，不重复收费）
    if next_stage:
        req = _stage_requirement(next_stage)
        if req.get("requires_discussion"):
            from mio_taskhub.models import Discussion
            has = db.exec(select(Discussion).where(Discussion.task_id == t.id)).first()
            if not has and not _requirement_approved(t):
                nxt_cn = _cn(_STAGE_CN, next_stage)
                req_state = _doc_state(t, "requirement")
                items.append(_item(
                    "discussion_required", "blocking",
                    f"进入「{nxt_cn}」前缺少方向确认",
                    f"进「{nxt_cn}」前需要一次方向确认，两种做法任选其一：留一条讨论记录"
                    "（topic 写这次做什么、conclusions 写定了哪条路），或把需求文档推进到「已批准」——"
                    "需求文档已批准时，方向确认由需求文档承担，讨论要求自动豁免。"
                    f"当前需求文档状态为「{_cn(_DOC_STATE_CN, req_state) if req_state else '未登记'}」，"
                    "且这条任务上一条讨论都没有，所以推进会被拒绝。"
                    "判定依据：requires_discussion=True、requirement 未到 approved、discussions=0。",
                    "任务卡在当前阶段：点「推进阶段」会报错（422），无法进入下一步；"
                    "用 stage/move 直接跳阶段同样受这条约束。",
                    steps=["做法一：打开「讨论」tab，点「新建讨论」。topic 写这次要做什么、为什么这么做；"
                           "结论填 conclusions，保存后这条记录就是「已结束」。",
                           "做法二：把需求文档推进到「已批准」，这条会自动消失。",
                           "任选其一完成后，回到顶部点「推进阶段」，校验通过。"],
                    owner="任务负责人",
                    api=f"POST /api/v1/tasks/{t.id}/discussions "
                        "{\"topic\": \"…\", \"agent\": \"…\", \"conclusions\": \"…\"}",
                ))

    # 上游未关闭的问题：open 决策与高偏差是文档内容定稿的前提，
    # 在 ⑤ 的文档门条目里要引用它们的数量（因果：先定内容，再批文档）。
    open_decs = db.exec(
        select(Decision).where(Decision.task_id == t.id, Decision.status == "open")
    ).all()
    unresolved_devs = _unresolved_deviations(t)
    upstream_parts = []
    if unresolved_devs:
        upstream_parts.append(f"{len(unresolved_devs)} 条未处理偏差")
    if open_decs:
        upstream_parts.append(f"{len(open_decs)} 条待拍板决策")
    upstream_note = ""
    upstream_step = ""
    if upstream_parts:
        upstream_note = ("另外注意先后顺序：文档内容的定稿以上游问题关闭为前提——当前还有 "
                         + "、".join(upstream_parts)
                         + "。先把它们处理完再推文档状态，否则批准之后内容还要改，状态得重推一遍。")
        upstream_step = "先处理上面列出的偏差与决策（文档内容定稿的前置），再回来推状态。"
    gate_blocked = False

    # ⑤ 下一阶段的文档门槛（累计口径，与 advance 完全一致）
    if next_stage:
        gate = _gates_upto(next_stage) or {}
        for kind, required in gate.items():
            if not doc_path_of(t, kind):
                continue           # 未登记 → 门控 fail-open，不算阻塞
            if not _doc_written(t, kind):
                continue           # 只登记路径、文件还没写 → 不阻断
            cur = _doc_state(t, kind)
            if cur == required:
                continue
            name = _cn(_KIND_CN, kind)
            want = _cn(_DOC_STATE_CN, required)
            nxt_cn = _cn(_STAGE_CN, next_stage)
            if not cur:
                title = f"{name}未登记状态"
                state_desc = "文档状态这一栏没有记录"
                cause = f"「{name}」的文件已经在任务里登记、也确实写出来了，但 doc_statuses 里没有它的状态记录。"
            elif cur == "failed":
                title = f"{name}评审未通过"
                state_desc = "上次评审结论为「未通过」"
                cause = f"「{name}」上一次评审的结论是「未通过」，之后没有改到「{want}」。"
            else:
                title = f"{name}未到「{want}」"
                state_desc = f"当前状态是「{_cn(_DOC_STATE_CN, cur)}」"
                cause = f"「{name}」的文件已经写出来了，但状态还停在「{_cn(_DOC_STATE_CN, cur)}」。"
            gate_blocked = True
            items.append(_item(
                "doc_gate", "blocking",
                title,
                cause +
                f"文档状态是阶段推进的硬校验条件：进入「{nxt_cn}」时，系统会逐份检查已登记文档的状态是否达到"
                f"「{want}」，不到就拒绝推进（这是门控 gate 的含义，不是提示）。判定依据："
                f"doc_statuses.{kind} 为 {cur or '空'}，目标状态 {required}。" + upstream_note,
                f"现在点「推进阶段」会被拒绝（返回 422）。就算绕过推进继续做，产出也是按"
                f"「{_cn(_DOC_STATE_CN, cur) or '未登记'}」状态的版本做的，等内容定稿后很可能需要返工。",
                steps=([upstream_step] if upstream_step else []) + [
                    f"打开「文档」tab，选中「{name}」。",
                    "先看质量分与缺口：有缺章节先补齐，否则改状态时仍会被质量检查拦下。",
                    f"把状态推进到「{want}」。若返回 422 且 detail.quality 里有 errors，按列出的章节补完再推。"],
                owner="文档作者",
                api=f"POST /api/v1/tasks/{t.id}/doc/{kind}/status {{\"status\": \"{required}\"}}",
                ref_id=kind, ref_title=required,
            ))

    # ⑥ open 决策（等人拍板）
    decisions = open_decs
    for d in decisions:
        options = [str(x) for x in (getattr(d, "options", None) or []) if str(x).strip()]
        opt_txt = f"候选方案：{' / '.join(options)}。" if options else ""
        items.append(_item(
            "decision_open", "blocking",
            "决策待拍板",
            f"有一条状态为 open 的决策没有结论：「{d.question}」。{opt_txt}"
            "决策在本系统里表示「这里必须由人选一个方向」。没选之前，任务继续往下做等于让执行方替你做选择。"
            "判定依据：该条决策 status = open，没有 chosen 值。",
            "执行方会按自己的理解选一条路。做完之后如果和你要的不一样，改动已经铺开，"
            "返工范围取决于选错的方向影响多少个文件。",
            steps=["打开这条决策的详情，看列出的候选方案。",
                   "选一个方案，写一句为什么这么选（rationale），保存。",
                   "如果这条决策已经没意义（方案变了、场景不成立），直接作废，不要留着 open。"],
            owner="业务负责人",
            api=f"POST /api/v1/decisions/{d.id}/resolve {{\"choice\": \"…\", \"rationale\": \"…\"}}",
            ref_id=d.id, ref_title=d.question,
        ))

    # ⑦ 高偏差未定
    for dev in unresolved_devs:
        dev_id = str(dev.get("id") or dev.get("title") or "?")
        desc = str(dev.get("description") or "").strip()
        if desc:
            if len(desc) > 140:
                cut = desc[:140]
                for sep in ("；", "，", "、"):
                    if sep in cut:
                        cut = cut[:cut.rindex(sep) + 1]
                        break
                desc_line = f"具体问题：{cut}…（完整描述见偏差记录）"
            else:
                desc_line = f"具体问题：{desc}"
        else:
            desc_line = "这条偏差没有填写问题描述，所以看不出具体哪里不一致。"
        items.append(_item(
            "deviation_open", "warning",
            f"偏差「{dev.get('title') or dev_id}」未处理",
            f"有一条偏差记录还没有处理完，影响级别 {getattr(dev, 'severity', '') or dev.get('severity') or '未标'}。"
            "偏差的意思是「实际做出来的东西和规格写的不一样」，系统把它单独记下来，"
            "收尾检查会核对有没有未处理完的偏差。" + desc_line,
            "高门控任务在收尾检查（completion_gate）时会被拦下，无法进入「已完成」；"
            "影响级别为 high 的偏差还会让验收结论失真——交付物和被认可的规格不一致。"
            + ("另外，这个任务还有文档没到「已批准」：这条偏差也是它们的前置——"
               "文档内容的定稿以设计问题关闭为前提，先解决这条偏差再推文档状态，"
               "顺序反过来就是「批准了又改、改完重推状态」。" if gate_blocked else ""),
            steps=["打开这条偏差记录，核对描述是否准确：不一致的地方具体是哪些。",
                   "确实要改就改实现，改完把它置为已解决。",
                   "不打算改（比如规格本身要调整），登记一条决策说明原因，不要让偏差一直挂着。"],
            owner="任务负责人 / 实现方",
            evidence=f"deviations[{dev_id}]：severity={getattr(dev, 'severity', '') or dev.get('severity') or '未标'}，"
                     f"status={dev.get('status') or '未标'}（未到 resolved / accepted 且无 decision_ref）",
            api=f"POST /api/v1/tasks/{t.id}/deviations/{dev_id}/resolve",
            ref_id=dev_id, ref_title=str(dev.get("spec_ref") or ""),
        ))

    # ⑧ 子任务：B 类是阻塞原因，N 系是工作项，分开报
    subs = db.exec(select(Subtask).where(Subtask.task_id == t.id)).all()
    parsed = {s.id: _parse_subtask(str(s.title or "")) for s in subs}
    # 编号 -> 名称：正文里出现的 B1/N2 等代号，只有这里能给出落点
    code_names: dict[str, str] = {}
    for s in subs:
        code = parsed[s.id]["code"]
        if code and code not in code_names:
            code_names[code] = parsed[s.id]["name"]
    code_done: dict[str, bool] = {
        parsed[s.id]["code"]: _v(s.status) == SubtaskStatus.DONE.value
        for s in subs if parsed[s.id]["code"]}

    open_subs = [s for s in subs if _v(s.status) != SubtaskStatus.DONE.value]
    if open_subs:
        causes, works = [], []
        for s in open_subs:
            p = dict(parsed[s.id])
            if p["is_cause"] or p["code"].startswith("B"):
                p["mins"] = None          # B 类没有时长语义，「提频 15min」不是耗时
                p.update(_cause_fix(p))   # 解决路径：要做什么、属于哪类（协调/拍板/补资料…）
                causes.append({**p, "status": _v(s.status)})
            else:
                p["dep_open"] = [d for d in p["deps"]
                                 if code_done.get(d) is False]
                p["dep_open_txt"] = "、".join(
                    f"{c}「{_short_name(code_names[c])}」" if c in code_names else c
                    for c in p["dep_open"])
                works.append({**p, "status": _v(s.status)})

        # 分两类：真的挡住某条具体工作的算 blocking；条目里写明「不阻塞」的，
        # 只说明这项能力还没人接，不影响本任务推进，降成 warning（否则会把计数报虚）。
        hard = [c for c in causes if c.get("blocks_txt") or c.get("blocks")]
        soft = [c for c in causes if not (c.get("blocks_txt") or c.get("blocks"))]
        causes = hard
        if causes:
            items.append(_item(
                "subtask_blocked", "blocking",
                f"{len(causes)} 项外部条件未落实",
                f"子任务清单里有 {len(causes)} 条被标成「阻塞」。这些不是「还没做」的工作项，"
                "而是做不下去的外部条件：业务没签字、上游采样频率不够、指标没实测、上游漏数据、"
                "某一摊事没有责任方。它们不解决，清单里被它们挡住的条目就算开工也拿不到确定输入。"
                "判定依据：子任务清单里 status = blocked 的条目。",
                "被这些条件挡住的条目实际不会推进。如果强行开工，会做出基于假设的实现，"
                "条件落实之后大概率要改，而且改的是数据结构和取数逻辑，波及面比补齐条件大。",
                steps=["从第 1 条开始逐条看：条目原文写的是哪件事、现状卡在哪、挡住了谁。",
                       "每条按它下面的「处理步骤」执行，动作和责任方都写在里面。",
                       "处理完把子任务清单里对应条目的状态改成「已完成」，全部清掉后回到这里刷新，这一项会消失。"],
                owner="见每条的责任方",
                members=causes,
                ref_id="subtasks",
            ))
        if soft:
            items.append(_item(
                "subtask_blocked", "warning",
                f"{len(soft)} 项外部条件待定，但不阻塞本任务",
                f"子任务清单里还有 {len(soft)} 条被标成「阻塞」，但条目说明里写明它们不挡住本任务的具体工作项："
                "属于「这件事暂时没人接」的状态，不影响本任务按现有范围推进。"
                "判定依据：条目 status = blocked，但条目说明里没有列出被挡住的编号。",
                "不影响本任务的进度。风险在于范围边界：如果后面要把这几项能力并入本任务，"
                "前提条件仍然缺，那时候需要重新评估工期。",
                steps=["确认这几项能力是否还在本次范围内；在范围内就要指定责任方，不在范围内保持现状即可。",
                       "如果决定不做，在条目里写明「不做」并置为已完成，避免统计上一直显示有阻塞。"],
                owner="业务负责人",
                members=soft,
                ref_id="subtasks",
            ))
        if works:
            ready_now = [w for w in works if not w["dep_open"]]
            if ready_now:
                because = (f"子任务清单还有 {len(works)} 条没有完成，其中 {len(ready_now)} 条的前置已经做完、"
                           "现在就能开工，其余的要等各自前置。")
            else:
                because = (f"子任务清单还有 {len(works)} 条没有完成，所有条目的前置都还没做完，"
                           "按顺序会依次轮到。")
            items.append(_item(
                "subtask_open", "info",
                f"子任务排队：{len(works)} 条未完成",
                because + "这一项不算问题：子任务是执行顺序，不是外部阻塞，系统不会因为它拦住任何操作。"
                "判定依据：子任务清单里 status != done 的条目。",
                "不处理没有风险。它列在这里是为了让你知道还剩多少活、哪些可以立刻开始，"
                "以及每条在等谁。",
                steps=["不需要为它做任何操作。",
                       "要开工时按每条标注的前置顺序领；前置没完成的会被排在后面。",
                       "某条确实不做了，就在子任务清单里删掉或标记取消，别让它一直挂着影响统计。"],
                owner="执行方",
                api=f"PATCH /api/v1/tasks/{t.id}/subtasks/<sid> {{\"status\": \"done\"}}",
                members=works,
                ref_id="subtasks",
            ))

    # ⑨ 代号就地展开：正文/备注/说明里出现的 B1、N2 等，只要能对上编号表，
    #    就在原位补上它的名字——读者不需要去别处查「B1 是什么」。
    #    编号表 = 子任务编号 + 偏差编号 + 决策标题里的 [D-x] 前缀。
    ref_map = dict(code_names)
    for dev in getattr(t, "deviations", None) or []:
        if not isinstance(dev, dict) or not dev.get("id"):
            continue
        desc = str(dev.get("title") or dev.get("description") or "").strip()
        if desc:
            ref_map.setdefault(str(dev["id"]), f"偏差：{_short_name(desc, 30)}")
    for d in db.exec(select(Decision).where(Decision.task_id == t.id)).all():
        m = re.match(r"^\s*[【\[](?P<code>[A-Z]{1,2}-?\d+(?:\.\d+)?)[】\]]\s*(?P<rest>.+)$",
                     str(d.question or ""))
        if m and m.group("rest").strip():
            ref_map.setdefault(m.group("code"), f"决策：{_short_name(m.group('rest'), 30)}")
    if ref_map:
        for it in items:
            # evidence 不做代号展开：它是可核查的字段路径（deviations[D-1]），
            # 插入名字会把路径读坏。
            for k in ("because", "consequence", "todo"):
                if it.get(k):
                    it[k] = _expand_codes(it[k], ref_map)
            for m in it.get("members") or []:
                if isinstance(m, dict):
                    if m.get("blocks_txt"):
                        # 先按原始文本拆项（拆分后再展开会把名字插进 token 里，拆不干净）
                        m["blocks_items"] = _blocks_items(m["blocks_txt"], ref_map)
                    for k in ("note", "desc", "name"):
                        if m.get(k):
                            m[k] = _expand_codes(m[k], ref_map)

    items.sort(key=lambda x: (_ORDER.get(x["kind"], 99), x["severity"] != "blocking"))
    return items


@router.get("/{task_id}/blockers")
def list_blockers(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    items = collect_blockers(db, t)
    blocking = [i for i in items if i["severity"] == "blocking"]
    return {
        "task_id": t.id,
        "state": _v(t.state),
        "stage": _v(t.stage),
        "next_stage": _NEXT_STAGE.get(_v(t.stage)),
        "blocked": bool(blocking),
        "blocking_count": len(blocking),
        "warning_count": sum(1 for i in items if i["severity"] == "warning"),
        "info_count": sum(1 for i in items if i["severity"] == "info"),
        "items": items,
    }
