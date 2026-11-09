# -*- coding: utf-8 -*-
"""「待我处理」收件箱（E7 结论产品化，任务 c8316737 后续，2026-11-04）。

GET /api/v1/tasks/inbox —— 三个决策队列，机器噪声恒过滤（mio_taskhub.noise）：

  1. decide（待决策）：依赖阻塞 + open 决策（FR-03）。
     依赖阻塞——前置已取消/失败，任务卡死动不了，只有你能解。
     open 决策——Decision 实体登记的待裁决问题；agent 只登记，不替你裁决。
     最老的排前面（拖得最久的先看到）。
  2. review（待你验收）：Run 已完成（completed + review 阶段），等你采纳。
     最老的排前面（最拖的先看到）。
  3. failed（失败待处置）：重试 / 拆小重做 / 关掉。最新的排前面（还有救的先看到）。

设计要点：
  - 只读：不写任何字段、不落事件。
  - 每条带 action 提示——「你要做什么」写死在返回里，agent / 前端无需再翻译。
  - 队列条数封顶（LIMIT），count 给全量数字。
  - Web 端 DecisionInbox 组件、MCP taskhub_my_inbox 工具共用本端点口径。
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Decision, Task, TaskState, TaskStage
from mio_taskhub.noise import is_noise
from mio_taskhub.workflow.state_machine import is_terminal
from mio_taskhub.dependency import task_deps, dependency_satisfied

router = APIRouter(prefix="/tasks", tags=["inbox"])

# 每个队列返回的条数上限（全量数字在 count 里）
LIMIT = 20


def _stage_value(t: Task) -> str:
    return t.stage.value if hasattr(t.stage, "value") else str(t.stage)


def _age_days(ts) -> int:
    if not ts:
        return -1
    dt = ts if hasattr(ts, "tzinfo") else None
    try:
        if dt is None:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except (ValueError, TypeError):
        return -1


def _item(t: Task, action: str, **extra) -> dict:
    d = {
        "kind": "task",
        "id": t.id,
        "title": t.title,
        "state": t.state.value if hasattr(t.state, "value") else str(t.state),
        "stage": _stage_value(t),
        "age_days": _age_days(getattr(t, "created_at", None)),
        "action": action,
    }
    d.update(extra)
    return d


@router.get("/inbox")
def my_inbox(db: Session = Depends(get_session)):
    biz = [t for t in db.exec(select(Task)).all() if not is_noise(t)]

    decide, review, failed = [], [], []
    for t in biz:
        sv = _stage_value(t)
        # ① 待决策：依赖阻塞（与 board.py 告警同口径：非终态 + 前置取消/失败）
        deps = task_deps(t)
        if deps and not is_terminal(t) and sv in ("brainstorming", "design", "planning"):
            prereqs = [db.get(Task, d) for d in deps if d]
            bad = [p for p in prereqs
                   if p is not None and is_terminal(p) and not dependency_satisfied(p)]
            if bad:
                decide.append(_item(
                    t, "重开前置 / 换前置 / 砍后置（PATCH /tasks/{id} 改 depends_on，"
                      "或 DELETE /tasks/{id}?confirm=true 取消）",
                    blocked_by=[p.id for p in bad]))
                continue
        # ② 待你验收：做完等人工采纳（completed + review 阶段）
        if t.state == TaskState.COMPLETED and sv == "review":
            review.append(_item(
                t, "看改动 GET /tasks/{id}/diff → POST /tasks/{id}/reviews "
                  "（decision=approve/reject）→ POST /tasks/{id}/stage {\"target_stage\":\"done\"}"))
            continue
        # ③ 失败待处置
        if t.state == TaskState.FAILED:
            failed.append(_item(
                t, "重试 POST /tasks/{id}/retry / 拆小重做 / 关掉 DELETE /tasks/{id}?confirm=true"))
            continue

    # 排序：验收最老的在前（age 大优先）；失败最新的在前（age 小优先）
    review.sort(key=lambda x: x["age_days"] or 0, reverse=True)
    failed.sort(key=lambda x: x["age_days"] or 0)

    # FR-03（任务 89a90af6）：open 决策并入 decide 队列。
    # 决策也是「只有你能做的事」——agent 只登记，不替用户裁决。
    open_decisions = db.exec(
        select(Decision).where(Decision.status == "open")
    ).all()
    for d in open_decisions:
        decide.append({
            "kind": "decision",
            "id": d.id,
            "title": d.question,
            "question": d.question,
            "context": d.context,
            "options": (d.options or []),
            "recommendation": d.recommendation,
            "task_id": d.task_id,
            "deadline": d.deadline.isoformat() if d.deadline else None,
            "age_days": _age_days(d.created_at),
            "action": "POST /api/v1/decisions/{id}/resolve {\"choice\": ...}（或 supersede 作废）",
        })
    # decide 队列整体最老的在前：拖得最久的决策/阻塞先看到
    decide.sort(key=lambda x: x["age_days"] or 0, reverse=True)
    decision_count = len(open_decisions)

    return {
        "summary": {
            "decide": len(decide),
            "decide_tasks": len(decide) - decision_count,
            "decide_decisions": decision_count,
            "review": len(review),
            "failed": len(failed),
            "total": len(decide) + len(review) + len(failed),
        },
        "decide": decide[:LIMIT],
        "review": review[:LIMIT],
        "failed": failed[:LIMIT],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "机器噪声（idea-reviewer/insight/变更/溯因/自测）已恒过滤；"
                "各队列最多返回 %d 条，全量看 summary。" % LIMIT,
    }
