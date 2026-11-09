"""执行就绪度（readiness）：这条任务现在**能不能真正开工**，缺什么、风险在哪、先解哪个。

与 `blockers.py` 的分工：

  · `blockers`  流程层的硬约束——依赖、文档门控、决策、子任务。「推不动」。
  · `readiness` 在流程约束之上回答四个问题：
      1. 能不能执行（flow 层：流程拦不拦）
      2. 缺什么输入（input 层：数据 / 参数 / 规则 / 上下文）
      3. 执行有什么风险（risk 层：历史失败、反复回收、高风险未确认）
      4. **先解哪个**（order + plan：按「严重度 → 层 → 改动成本」排序）

文案规则与 blockers 一致：不出现 `stage=xxx` 枚举，不出现 HTTP 命令当操作指引，
每条固定 because（为什么）→ consequence（会怎样）→ todo（界面上怎么解）。

关于噪音（实测数据决定的设计）：
全库 1544 条未完成任务里，96% 没有验收标准、99% 没有交付物、96% 没有工作区。
如果对所有任务都报「缺验收标准」，只会得到 1500 条一样的告警。所以 input 层
**只在 stage ∈ {ready, implementing} 且 state ∈ {queued, claimed, running} 时计算**——
也就是「马上要执行 / 正在执行」的任务才要求输入齐备；构思、设计、排期阶段本来
就还在定义输入，提前要求等于制造噪音。
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.api.blockers import (_DOC_STATE_CN, _NATURE, _ORDER as _FLOW_ORDER,
                                      _STAGE_CN, _cn, _v, collect_blockers, split_evidence)
from mio_taskhub.db import get_session
from mio_taskhub.models import Subtask, Task, TaskState

router = APIRouter(prefix="/tasks", tags=["readiness"])

_LAYER_ORDER = {"flow": 0, "input": 1, "risk": 2}
_LAYER_CN = {"flow": "流程约束", "input": "执行输入", "risk": "执行风险"}
_DOC_CN = {
    "requirement": "需求文档",
    "spec": "技术方案",
    "api": "接口契约",
    "plan": "实施计划",
    "test": "测试",
}
# 解一条要花多大劲：low = 界面上填一句就跑，mid = 要写/批一份东西，high = 要改结构或等人
_EFFORT = {
    # flow
    "state_failed": "low",
    "runtime_block": "low",
    "dependency_dead": "high",
    "dependency_open": "high",
    "discussion_required": "mid",
    "doc_gate": "mid",
    "decision_open": "mid",
    "deviation_open": "mid",
    "subtask_blocked": "high",
    "subtask_open": "mid",
    # input
    "scope_unknown": "low",
    "acceptance_missing": "low",
    "deliverable_missing": "low",
    "doc_unregistered": "mid",
    "doc_file_missing": "high",
    "doc_gap": "mid",
    "context_docs_missing": "mid",
    "contract_missing": "mid",
    # risk
    "retry_history": "mid",
    "bounce_history": "mid",
    "high_tier_unconfirmed": "low",
}
_EFFORT_RANK = {"low": 0, "mid": 1, "high": 2}
# 各阶段「开工前必须齐」的输入类别，按阶段递进——越接近真正执行，要求越全。
# 构思阶段什么都不要求（本来就还在定义），这也避免全库上千条任务一起报「缺验收标准」。
_PHASE_NEEDS = {
    "design": {"scope", "doc"},
    "planning": {"scope", "acceptance", "doc"},
    "ready": {"scope", "acceptance", "deliverable", "doc", "contract"},
    "implementing": {"scope", "acceptance", "deliverable", "doc", "contract"},
}
_EXEC_STATES = (TaskState.QUEUED, TaskState.CLAIMED, TaskState.RUNNING)


def _item(kind, layer, severity, title, because, consequence, todo="",
          api="", ref_id="", ref_title="", steps=None, owner="",
          nature="", evidence="") -> dict:
    steps = [str(x).strip() for x in (steps or []) if str(x or "").strip()]
    if not todo:
        todo = " ".join(f"{i}) {x}" for i, x in enumerate(steps, 1))
    if not evidence:
        because, evidence = split_evidence(because)
    return {
        "kind": kind,
        "layer": layer,
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
        "effort": _EFFORT.get(kind, "mid"),
        "ref_id": ref_id or "",
        "ref_title": ref_title or "",
    }


def _json_obj(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (TypeError, ValueError):
        return {}


def _text_list(raw) -> list:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _doc_exists(t: Task, kind: str | None) -> bool:
    """登记路径对应的文档是否真的在盘上（按文档解析器口径，不依赖 workspace）。"""
    if not kind:
        return False
    try:
        from mio_taskhub.read_evidence import resolve_doc_path
        p = resolve_doc_path(t, str(kind))
        return bool(p is not None and p.is_file())
    except Exception:  # noqa: BLE001 — 判断不了当不存在，交给上层保守报缺
        return False


def _doc_gap_items(t: Task, tier: str) -> list[dict]:
    """已落盘文档缺必需章节 —— 这才是「缺参数 / 缺规则」的可执行形态。

    例如接口契约缺「请求参数」、技术方案缺「状态机」：执行时这部分只能靠猜。
    """
    out: list[dict] = []
    from mio_taskhub.doc_paths import doc_path_of
    from mio_taskhub.doc_quality import quality_of_file
    from mio_taskhub.read_evidence import resolve_doc_path

    severity = "blocking" if tier in ("standard", "high") else "warning"
    for kind in ("requirement", "spec", "api", "plan"):
        if not doc_path_of(t, kind):
            continue
        try:
            p = resolve_doc_path(t, kind)
            if p is None or not p.is_file():
                continue
            q = quality_of_file(kind, p, ws_dir=str(t.workspace or "") or None)
        except Exception:  # noqa: BLE001 — 单个文档检查失败不影响其它项
            continue
        if not isinstance(q, dict):
            # quality_of_file 对不认识的 kind / 读不动的文件会返回 None
            continue
        missing = [str(e) for e in (q.get("errors") or []) if "缺少必需章节" in str(e)]
        if not missing:
            continue
        names = "、".join(
            m.replace("缺少必需章节", "").strip("「」:： ") for m in missing[:3]
        )
        out.append(_item(
            "doc_gap", "input", severity,
            f"{_DOC_CN.get(kind, kind)}缺关键章节",
            f"「{_DOC_CN.get(kind, kind)}」这个文件已经落盘，但质量检查发现它缺少必需章节：「{names}」。"
            "这些章节是执行时唯一的依据来源——数据从哪来、参数怎么定、边界条件怎么处理都写在里面，"
            "缺了这一节，实现方只能自己定。判定依据：质量检查的 errors 里包含「缺少必需章节」。",
            "执行方会按自己的理解补这些空白。做出来的东西和你预期不一致时，改动已经发生，"
            "要么按实现改文档，要么按文档改实现，两条路都要花时间。",
            steps=["打开这份文档，按缺口列出的章节名把内容补齐。",
                   "补完重新跑一次质量检查，确认 errors 清零。",
                   "门控要求「已批准」的，再把状态推到「已批准」。"],
            owner="文档作者",
            ref_id=kind,
        ))
    return out


def _input_items(db: Session, t: Task) -> list[dict]:
    """执行输入缺口：数据 / 参数 / 规则 / 上下文 / 产物契约。"""
    items: list[dict] = []
    stage = _v(t.stage)
    needs = _PHASE_NEEDS.get(stage)
    if not needs or t.state not in _EXEC_STATES:
        return items

    # ① 不知道改哪里：工作区与文件都空
    if "scope" in needs and not _text_list(t.files) and not str(t.workspace or "").strip():
        items.append(_item(
            "scope_unknown", "input", "warning",
            "未指定工作区与文件",
            "这条任务既没有设置 workspace（工作目录），files 里也没有列任何文件。"
            "执行方需要一个明确的范围，才能知道该在哪个代码库里动手、改哪几个文件。"
            "判定依据：workspace 为空，且 files 为空。",
            "执行方会自己在仓库里找位置，可能改到相邻模块，也可能把改动铺得比预期大；"
            "评审时很难分清哪些改动是这次要的、哪些是顺手带上的。",
            steps=["在任务信息里填「工作区」目录（绝对路径）。",
                   "在「文件」里列出这次要动的主要文件，列 2-3 个关键的也够。",
                   "保存后回来看一眼就绪度，这一项应该消失。"],
            owner="任务负责人",
        ))

    # ② 没有验收标准 → 干完也无法判定对错
    if "acceptance" in needs and not str(t.acceptance_criteria or "").strip():
        items.append(_item(
            "acceptance_missing", "input", "warning",
            "未填写验收标准",
            "任务的 acceptance_criteria 是空的，也就是没写「做成什么样算做完」。"
            "验收标准是判定完成与否的唯一依据：没有它，执行方交付后无法自检，"
            "你验收时也只能通读一遍凭印象判断。判定依据：acceptance_criteria 为空。",
            "会出现「他说做完了、你说没做到」的反复。同一件事判断标准不一致，"
            "每轮都要重新解释一遍要求，进度被拖在返工里。",
            steps=["写一条能被验证的标准，例如「接口返回 200，且 nodeExtrema 不为 null」。",
                   "有多项要求就分行写，每项都要能靠一条命令或一次点击验证。",
                   "保存后再开工，验收时照这份清单逐项核对。"],
            owner="任务负责人",
        ))

    # ③ 交付物为空
    if "deliverable" in needs and not _text_list(t.deliverables):
        items.append(_item(
            "deliverable_missing", "input", "warning",
            "未填写交付物",
            "任务的 deliverables 是空的，没写这次要交什么：代码、文档、配置、脚本都算。"
            "执行方只能按标题自行理解该交什么，而人默认只交代码。判定依据：deliverables 为空。",
            "收尾核验时才发现少了配套文档或配置，需要再开一轮补交；"
            "如果下游任务等着这些产物，还会连带把它卡住。",
            steps=["列出这次要交的产物，写文件路径或产物名。",
                   "容易漏的项单独列一条：迁移脚本、配置项、文档章节、部署说明。",
                   "验收时按这份清单逐项打勾，缺哪项当场要。"],
            owner="任务负责人",
        ))

    # ④ 门控档要求的文档：没登记 / 登记了但文件不在 / 写了但缺关键章节
    tier = str(t.gate_tier or "light").lower()
    if "doc" in needs:
        try:
            from mio_taskhub.completion_gate import build_readiness
            ready = build_readiness(db, t)
        except Exception:  # noqa: BLE001 — 就绪度不能因为单项检查失败整体 500
            ready = {"ready": True, "missing": []}
        for m in ready.get("missing") or []:
            kind_cn = {"requirement": "需求", "spec": "技术方案", "api": "接口契约",
                       "plan": "实施计划", "test": "测试", "review": "验收"}.get(m.get("kind"), m.get("kind"))
            if m.get("reason") == "registered_but_file_missing":
                # completion_gate 是把登记路径当相对路径、拼在 workspace 下判存在的：
                # 没有 workspace 或登记的是绝对路径时它会误判。这里用文档解析器再核一次，
                # 文件真在就不报「文件不见了」（否则会同时出现「文件不见了」+「缺章节」）。
                if _doc_exists(t, m.get("kind")):
                    continue
                items.append(_item(
                    "doc_file_missing", "input", "blocking",
                    f"{kind_cn}文档文件不存在",
                    f"任务里登记了{kind_cn}的路径「{m.get('path') or '空'}」，但这个路径下没有文件。"
                    "登记路径和实际文件脱节，系统读这份文档时拿到的是空的。"
                    "判定依据：resolve_doc_path 按登记路径找不到文件。",
                    "执行方读不到文档内容，只能凭任务标题猜做法；质量检查也会因为读不到内容而报缺章节，"
                    "同一个问题在两处重复出现。",
                    steps=["核对登记的路径是否写错：相对路径会按工作区目录解析，工作区没设时就会落空。",
                           "文件还在别的地方就移回来；路径变了就重新登记新路径。",
                           "确认后刷新就绪度，这一项和附带的缺章节提示会一起变准。"],
                    owner="任务负责人",
                    ref_id=str(m.get("kind") or ""),
                ))
            elif m.get("reason") == "required_but_not_registered":
                items.append(_item(
                    "doc_unregistered", "input",
                    "blocking" if tier in ("standard", "high") else "warning",
                    f"缺{kind_cn}文档",
                    f"当前门控档是「{tier}」，这一档要求任务上有{kind_cn}。"
                    "系统只认登记过的文档：没登记，等于这份文档不存在，质量检查和门控都不会去读它。"
                    "判定依据：doc_paths 里没有该文档的条目。",
                    "领取任务时的就绪检查不通过，执行方领不走这条任务；推进阶段时也会被门控拒绝。",
                    steps=[f"在「文档」tab 新建{kind_cn}。",
                           "写完跑一次质量检查，把 errors 清零。",
                           "把状态推到门控要求的档位（通常是「已批准」）。"],
                    owner="文档作者",
                ))

        # 文档写了但缺关键章节 —— 这才是「缺参数 / 缺规则」的具体形态：
        # 例如接口契约缺「请求参数」、技术方案缺「状态机」，执行时只能靠猜。
        items.extend(_doc_gap_items(t, tier))

    # ⑤ 上下文包：拆解出来的子任务要读的文档读不出内容
    pkg = _json_obj(getattr(t, "context_package", None))
    for doc in (pkg.get("documents") or []):
        if not isinstance(doc, dict):
            continue
        if int(doc.get("chars") or 0) > 0:
            continue
        name = doc.get("path") or doc.get("kind") or "上下文文档"
        items.append(_item(
            "context_docs_missing", "input", "blocking",
            "上下文文档内容为空",
            f"拆解任务时指定要读取的上下文文档「{name}」，现在读出来是空的："
            "文件可能被删了，或者章节标题被改过、导致抽取不到内容。"
            "上下文包是子任务开工时唯一的背景材料来源。",
            "子任务会按不完整的信息执行，做出来的东西和预期不一致；"
            "返工范围取决于缺的是哪一部分背景。",
            steps=["确认该文件是否还存在、章节标题有没有被改过。",
                   "让父任务重新拆解一次，重新生成上下文包。",
                   "或者手动把这段背景补进子任务的描述里。"],
            owner="任务负责人",
            evidence=f"context_package.documents 中「{name}」的 chars={int(doc.get('chars') or 0)}",
            ref_id=str(name),
        ))

    # ⑥ 有下游依赖，但没定义产物契约
    downstream = db.exec(
        select(Task).where(Task.state.in_(_EXEC_STATES))
    ).all()
    consumers = [x for x in downstream if t.id in _text_list(x.depends_on)]
    contracts = (pkg.get("contracts") or {}) if isinstance(pkg.get("contracts"), dict) else {}
    if consumers and not contracts.get("outputs"):
        items.append(_item(
            "contract_missing", "input", "warning",
            "产物契约未定义",
            f"有 {len(consumers)} 条任务依赖本任务的产出，但上下文包里没有定义 outputs"
            "（产出路径、字段、结构）。下游拿到的是「会有个东西」，不是一个能照着对接的契约。",
            "下游会按各自的理解对接。本任务一旦调整产出格式，下游要跟着改，"
            "改动会沿着依赖链一路传下去。",
            steps=["在上下文包里写 outputs：产物路径 + 关键字段 / 结构。",
                   "把这份定义同步给下游（写在任务的依赖说明里）。",
                   "产出要变时先改契约，再改实现。"],
            owner="任务负责人",
            evidence=f"下游依赖任务 {len(consumers)} 条，context_package.contracts.outputs 为空",
        ))

    return items


def _risk_items(t: Task) -> list[dict]:
    """执行风险：历史失败、反复回收、高风险未确认。"""
    items: list[dict] = []

    attempt = int(t.attempt or 0)
    if attempt >= 2:
        items.append(_item(
            "retry_history", "risk", "warning",
            f"已重试 {attempt} 次仍未完成",
            f"这条任务被重试了 {attempt} 次，每次的输入信息（描述、文件、工作区、上下文）都没有变化。"
            "重试只是把同一条任务重新排队，不会改变导致失败的条件。",
            "继续原样重试只会再消耗一轮时间和额度，结果大概率还是一样；"
            "重试次数还会继续累加，掩盖真正的失败原因。",
            steps=["打开「动态」tab，看每次失败的报错是否相同。",
                   "如果失败原因是缺信息（缺文件、缺权限、缺参数），补完再重试。",
                   "如果是任务太大一次做不完，先拆成两条小任务再分别派。"],
            owner="任务负责人",
            evidence=f"attempt={attempt}（阈值 ≥2），输入指纹未变化",
        ))

    bounce = int(t.bounce_count or 0)
    if bounce >= 3:
        items.append(_item(
            "bounce_history", "risk", "warning",
            f"被回收 {bounce} 次",
            f"这条任务被领取后又退回队列 {bounce} 次。回收的触发条件是「领取后没有开工，或心跳超时」，"
            "也就是执行方拿到了任务却启动不了。",
            "反复回收会持续占用调度名额，但任务本身一步都没往前走；"
            "次数多了这条任务会被判定为不稳定，调度时被排到后面。",
            steps=["确认执行方所在机器能访问任务的工作区目录：路径是否存在、权限够不够。",
                   "确认执行方进程还活着、心跳正常。",
                   "任务本身过大导致启动慢的，先拆小再派。"],
            owner="环境维护者",
            evidence=f"bounce_count={bounce}（阈值 ≥3），最后一次回收原因见「动态」tab",
        ))

    if str(t.gate_tier or "").lower() == "high" and not t.human_confirmed_at:
        items.append(_item(
            "high_tier_unconfirmed", "risk", "warning",
            "高风险任务未人工确认",
            "这条任务的门控档是 high，表示改动影响面大，但 human_confirmed_at 为空——"
            "还没有人看过方案并签字确认。高门控任务的收尾检查会核对这条记录。",
            "做到收尾才被拦下要求确认。那时如果真的发现方案有问题，"
            "前面基于这个方案的实现要重做，越晚发现代价越大。",
            steps=["把方案写进任务描述：改哪些文件、影响哪些接口和调用方。",
                   "看过之后做一次人工确认，记录确认人和结论。",
                   "确认完成后再进入实现阶段。"],
            owner="业务负责人 / 技术负责人",
            evidence=f"gate_tier={t.gate_tier}，human_confirmed_at 为空",
        ))

    return items


def _sort_key(i: dict):
    # 先按严重度（blocking → warning → info），再按层（流程 → 输入 → 风险），
    # 同层内按改动成本（一句话能补的排前面），最后按 kind 固定序。
    return (
        {"blocking": 0, "warning": 1}.get(i["severity"], 2),
        _LAYER_ORDER.get(i["layer"], 9),
        _EFFORT_RANK.get(i["effort"], 9),
        _FLOW_ORDER.get(i["kind"], 99),
    )


def _aggregate_doc_gate(items: list[dict]) -> list[dict]:
    """同一个目标状态的 doc_gate 合成一条：「3 份文档没到已批准」+ 3 个成员。

    截图教训：需求/技术方案/接口契约三条的 because/consequence/todo 除文档名外
    逐字相同，在界面上重复计费三遍。聚合放 API 层，plan 和 counts 才能吃到同口径。
    """
    groups: dict[str, list[dict]] = {}
    for i in items:
        if i.get("kind") == "doc_gate" and i.get("ref_title"):
            groups.setdefault(i["ref_title"], []).append(i)
    if not any(len(g) > 1 for g in groups.values()):
        return items
    emitted: set[str] = set()
    out: list[dict] = []
    for i in items:
        if i.get("kind") != "doc_gate" or not i.get("ref_title"):
            out.append(i)
            continue
        want = i["ref_title"]
        if want in emitted:
            continue
        emitted.add(want)
        grp = groups[want]
        if len(grp) == 1:
            out.append(i)
            continue
        head = grp[0]
        labels = [_DOC_CN.get(m.get("ref_id") or "", m.get("ref_id") or "文档")
                  for m in grp]
        names = "、".join(f"「{x}」" for x in labels)
        want_cn = _cn(_DOC_STATE_CN, want)
        kinds = "、".join(f"doc_statuses.{m.get('ref_id')}" for m in grp)
        # 因果说明：文档内容定稿以上游问题（偏差/决策）关闭为前提，先定内容再批文档
        n_dev = sum(1 for i in items if i.get("kind") == "deviation_open")
        n_dec = sum(1 for i in items if i.get("kind") == "decision_open")
        upstream = ""
        upstream_step = ""
        if n_dev or n_dec:
            parts = []
            if n_dev:
                parts.append(f"{n_dev} 条未处理偏差")
            if n_dec:
                parts.append(f"{n_dec} 条待拍板决策")
            upstream = ("另外注意先后顺序：文档内容的定稿以上游问题关闭为前提——当前还有 "
                        + "、".join(parts)
                        + "。先把它们处理完再推文档状态，否则批准之后内容还要改，状态得重推一遍。")
            upstream_step = "先处理上面列出的偏差与决策（文档内容定稿的前置），再回来推状态。"
        out.append({**head,
                    "title": f"{len(grp)} 份文档没到「{want_cn}」",
                    "because": f"{names}这几份文档的文件都已经写出来了，但状态都还没达到「{want_cn}」。"
                    "文档状态是阶段推进的硬校验条件：进入下一阶段时，系统会逐份检查已登记文档的状态，"
                    "不到就拒绝推进——这是门控（gate）的含义，不是提示。" + upstream,
                    "evidence": f"{kinds} 均未到 {want}",
                    "steps": ([upstream_step] if upstream_step else []) + [
                        "下面列出的每份文档都要单独推进：打开「文档」tab 找到它。",
                        "先看质量分与缺口，有缺章节先补齐，否则改状态时仍会被质量检查拦下。",
                        f"把状态推到「{want_cn}」；返回 422 且 detail.quality 有 errors 时，"
                        "按列出的章节补完再推。"],
                    "members": labels})
    return out


def _inputs_updated_at(db: Session, t: Task) -> Optional[str]:
    """这份就绪度的输入里，最新的一次改动时间。

    取三者的最大值：任务行 updated_at、子任务 updated_at、已登记文档文件的 mtime。
    用户据此判断「面板上的结论是不是比手上的改动还旧」。
    """
    stamps: list[datetime] = []
    if getattr(t, "updated_at", None):
        stamps.append(t.updated_at)
    for s in db.exec(select(Subtask).where(Subtask.task_id == t.id)).all():
        if getattr(s, "updated_at", None):
            stamps.append(s.updated_at)
    try:
        from mio_taskhub.read_evidence import resolve_doc_path
        for kind in (t.doc_paths or {}):
            p = resolve_doc_path(t, kind)
            if p is not None and p.is_file():
                stamps.append(datetime.fromtimestamp(p.stat().st_mtime))
    except Exception:  # noqa: BLE001 — 取时间失败不影响结论
        pass
    return max(stamps).isoformat(timespec="seconds") if stamps else None


def collect_readiness(db: Session, t: Task) -> dict:
    flow = []
    for i in collect_blockers(db, t):
        flow.append({**i, "layer": "flow", "effort": _EFFORT.get(i["kind"], "mid")})
    flow = _aggregate_doc_gate(flow)

    items = flow + _input_items(db, t) + _risk_items(t)
    items.sort(key=_sort_key)
    for idx, i in enumerate(items, start=1):
        i["order"] = idx

    blocking = [i for i in items if i["severity"] == "blocking"]
    plan = [
        {"order": i["order"], "kind": i["kind"], "title": i["title"],
         "why": _plan_reason(i)}
        for i in blocking[:3]
    ]
    return {
        "task_id": t.id,
        "state": _v(t.state),
        "stage": _v(t.stage),
        "stage_cn": _cn(_STAGE_CN, _v(t.stage)),
        # 结论是每次请求现算的（不缓存、不落库），这两个时间戳用来判断新不新
        "computed_at": datetime.now().isoformat(timespec="seconds"),
        "inputs_updated_at": _inputs_updated_at(db, t),
        "ready": not blocking,
        "counts": {
            "flow": sum(1 for i in items if i["layer"] == "flow"),
            "input": sum(1 for i in items if i["layer"] == "input"),
            "risk": sum(1 for i in items if i["layer"] == "risk"),
            "blocking": len(blocking),
            "warning": sum(1 for i in items if i["severity"] == "warning"),
            "info": sum(1 for i in items if i["severity"] == "info"),
        },
        "plan": plan,
        "items": items,
    }


def _plan_reason(i: dict) -> str:
    """「为什么先解这条」的一句话。"""
    if i["layer"] == "flow":
        return "不解决，这条任务根本动不了"
    if i["layer"] == "input":
        return "不补上，干出来的东西大概率要返工"
    return "先知道风险，再决定怎么做"


@router.get("/{task_id}/readiness")
def get_readiness(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    return collect_readiness(db, t)
