# -*- coding: utf-8 -*-
"""机器噪声任务判定（共享模块，2026-11-04 E7 结论落地）。

口径必须与三处保持一致（改规则时全部同改）：
  - web/src/noise.js            （前端看板过滤 / 决策面板）
  - scripts/e7_decision_scan.py （E7 决策清单脚本）
  - 本模块                      （后端 /tasks/inbox 与 MCP taskhub_my_inbox）

背景：看板曾同时承载 1771 个任务，其中 88% 是机器自动生成、不需要人决策的
卡片（创意引擎想法评审 / 变更 / 溯因、洞察消费、自测任务）。任何「面向人的
决策视图」都必须先用本口径过滤，否则真实决策信息会被稀释 9:1。
"""
import re

from mio_taskhub.models import Task

NOISE_AGENT_TYPES = {"idea-reviewer"}
NOISE_TITLE_RE = re.compile(r"想法评审|^\[insight\]|^\[变更\]|^\[溯因\]|^__verify_")


def _field(task, name: str):
    """task 支持 ORM Task 或 dict 两种形态。"""
    if isinstance(task, dict):
        return task.get(name)
    return getattr(task, name, None)


def is_noise(task) -> bool:
    """判定一个任务（Task ORM 或 dict）是否为机器噪声。"""
    if task is None:
        return False
    if (_field(task, "target_agent_type") or "") in NOISE_AGENT_TYPES:
        return True
    return bool(NOISE_TITLE_RE.search(_field(task, "title") or ""))


def filter_noise(tasks):
    """迭代器：剔除噪声任务。"""
    for t in tasks or []:
        if not is_noise(t):
            yield t


def noise_stats(tasks):
    """[total, noise, biz] 三元组——E7 总览口径。"""
    from collections import Counter
    total = noise = 0
    for t in tasks or []:
        total += 1
        if is_noise(t):
            noise += 1
    return {"total": total, "noise": noise, "business": total - noise}
