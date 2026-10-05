# -*- coding: utf-8 -*-
"""危险操作调用点守卫（#5：policy check 并入调用点）。

分工（低耦合）：
- **Mio** 负责历史风险评估（`mio policy check`：trace + memory 证据）；
- **taskhub** 只负责调用点接线：删任务/删模板/删定时任务/删告警规则/应用更新。

规则：
- 只读、best-effort、**fail-open**：Mio 缺失/超时/报错 → riskLevel=unknown，放行；
- `riskLevel == high` 或 `guidance.hardGate == true` 且未 confirm → 409（带 policy 摘要）；
- 其余等级放行，policy 摘要塞进响应供 UI/审计展示。
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException

from mio_taskhub import mio_runtime

# 需要二次确认的风险等级（moderate 仅建议加验证，不阻断）
GATE_LEVELS = frozenset({"high"})


def guard_action(action: str, confirm: bool = False,
                 project: Optional[str] = None) -> dict:
    """执行前查 Mio 历史风险。高风险未确认 → 409；否则返回 policy 摘要。"""
    policy = mio_runtime.policy_check(action, project=project)
    gated = policy.get("riskLevel") in GATE_LEVELS or bool(policy.get("hardGate"))
    if gated and not confirm:
        risk = policy.get("riskLevel") or "high"
        raise HTTPException(status_code=409, detail={
            "detail": f"历史风险评估为 {risk}，需二次确认后执行",
            "error": "policy_risk_high",
            "risk": risk,
            "policy": policy,
            "hint": "确认无误后带 confirm=true 重试",
        })
    return policy
