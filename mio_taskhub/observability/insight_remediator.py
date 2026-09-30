# -*- coding: utf-8 -*-
"""closed-loop insight consumer（P2-4）。

把「内置洞察」（insight 表 kind=anomaly）从"只记录"变成"有行动"：
未确认的 critical（可选 warning）洞察 → 自动创建 hub 跟进任务，形成
    metrics → insight → task → agent 行动 → 新数据
的闭环。

设计要点：
- **只建跟进任务，不自动执行修复**（避免越权/误操作）。
- **幂等去重**：同一 metric 的未确认洞察在已有未完成跟进任务时不重复建；
  任务 title 带 `[insight]` 前缀，label 带 `insight-auto` 便于识别。
- **可关闭**：env MIO_INSIGHT_AUTOTASK=0/off 关闭；默认开启。
- **可观测**：每次消费写 kind=remediation 的 insight 记录（复用 RemediationEngine.log）。
"""
import logging
import os
from typing import Optional

from sqlmodel import Session, select

from mio_taskhub.db import engine
from mio_taskhub.models import Task, TaskStage, TaskState

logger = logging.getLogger("mio_taskhub.observability.insight_remediator")

ON = ("1", "on", "true", "yes", "auto")
INSIGHT_LABEL = "insight-auto"
TITLE_PREFIX = "[insight]"


def autotask_enabled() -> bool:
    """是否启用「洞察→跟进任务」（P2-4）。默认开启。"""
    val = os.environ.get("MIO_INSIGHT_AUTOTASK", "1").strip().lower()
    return val in ON


def _severities_from_env() -> set:
    """触发建任务的 severity 集合，默认仅 critical。env MIO_INSIGHT_AUTOTASK_SEVERITY=critical,warning。"""
    raw = os.environ.get("MIO_INSIGHT_AUTOTASK_SEVERITY", "critical").strip()
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


class InsightsRemediator:
    def __init__(self, db_engine=None):
        self._engine = db_engine or engine

    def consume(self, insights: list) -> list:
        """消费洞察列表，为符合条件的未确认洞察建跟进任务。返回本轮回访创建的 tasks。"""
        if not autotask_enabled():
            return []
        want = _severities_from_env()
        created = []
        for ins in insights or []:
            sev = str(ins.get("severity", "")).lower()
            if sev not in want:
                continue
            if ins.get("acknowledged"):
                continue
            task = self._create_followup(ins)
            if task is not None:
                created.append(task)
        return created

    def _create_followup(self, ins: dict) -> Optional[Task]:
        metric = ins.get("metric_name") or ins.get("title") or "unknown"
        title = f"{TITLE_PREFIX} {metric}"
        with Session(self._engine) as db:
            # 去重：已有同标题、未完成的跟进任务 → 不重复建
            existing = db.exec(
                select(Task).where(
                    Task.title == title,
                    Task.state.not_in([TaskState.COMPLETED, TaskState.CANCELLED, TaskState.FAILED]),
                )
            ).first()
            if existing is not None:
                return None
            task = Task(
                title=title,
                description=(
                    f"来源：自动洞察消费（P2-4）\n\n"
                    f"指标：{metric}\n当前值：{ins.get('metric_value')}\n"
                    f"阈值：{ins.get('baseline')}\n严重度：{ins.get('severity')}\n\n"
                    f"说明：{ins.get('description', '')}\n\n"
                    f"建议：{ins.get('recommendation', '') or '（无）'}\n\n"
                    f"—— 本任务由洞察自动派生，请人工核实后处理或关闭。"
                ),
                priority=2,
                stage=TaskStage.READY,
                labels=[INSIGHT_LABEL, f"metric:{metric}"],
                project="agent-dev",
                workspace=os.environ.get("MIO_TASKHUB_WORKSPACE", ""),
            )
            db.add(task)
            db.commit()
            db.refresh(task)
            logger.info("insight remediator created follow-up task %s for metric=%s", task.id, metric)
        # 记录消费动作（可观测）
        try:
            from mio_taskhub.observability.remediation import RemediationEngine
            RemediationEngine().log(
                "insight_autotask",
                f"critical insight on {metric} → task {task.id} ({ins.get('description', '')[:120]})",
                success=True,
            )
        except Exception:  # noqa: BLE001 — 记录失败不影响建任务
            logger.exception("failed to log insight autotask")
        return task
