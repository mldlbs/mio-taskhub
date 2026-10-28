# -*- coding: utf-8 -*-
"""素材来源种子（spec §3.10）。表为空时插入 10 个来源，幂等。"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlmodel import Session

from mio_taskhub.materials.models import MaterialSource
from mio_taskhub.materials.sanitize import SANITIZER_CONFIG_HASH, SANITIZER_VERSION

SOURCE_DEFS = [
    {
        "key": "hn", "origin": "external", "name": "Hacker News",
        "adapter": "hn", "mode": "active", "min_interval_s": 3600,
        "daily_cap": 300,
    },
    {
        "key": "github-trending", "origin": "external", "name": "GitHub Trending",
        "adapter": "github_trending", "mode": "active", "min_interval_s": 3600,
        "daily_cap": 300,
    },
    {
        "key": "doc.requirement", "origin": "internal", "name": "已批准需求文档",
        "adapter": "doc_requirement", "mode": "active", "min_interval_s": 600,
        "daily_cap": 500,
    },
    {
        "key": "doc.review", "origin": "internal", "name": "评审结论",
        "adapter": "doc_review", "mode": "active", "min_interval_s": 600,
        "daily_cap": 500,
    },
    {
        "key": "discussion.closed", "origin": "internal", "name": "已关闭讨论结论",
        "adapter": "discussion_closed", "mode": "active", "min_interval_s": 600,
        "daily_cap": 300,
    },
    {
        "key": "run.result", "origin": "task", "name": "Run 结果证据",
        "adapter": "run_result", "mode": "active", "min_interval_s": 120,
        "daily_cap": 500,
    },
    {
        "key": "run.failure", "origin": "task", "name": "Run 失败原因",
        "adapter": "run_failure", "mode": "active", "min_interval_s": 120,
        "daily_cap": 500,
    },
    {
        "key": "history.root_cause", "origin": "task", "name": "故障根因",
        "adapter": "history_root_cause", "mode": "active", "min_interval_s": 300,
        "daily_cap": 300,
    },
    {
        "key": "hub.console", "origin": "log", "name": "Hub 控制台日志",
        "adapter": "hub_console", "mode": "trial", "min_interval_s": 300,
        "daily_cap": 200,
    },
    {
        "key": "observer.serve", "origin": "log", "name": "Observer 服务日志",
        "adapter": "observer_serve", "mode": "trial", "min_interval_s": 300,
        "daily_cap": 200,
    },
]


def seed_material_sources(session: Session) -> int:
    count = session.execute(
        select(func.count()).select_from(MaterialSource)
    ).scalar_one()
    if int(count or 0) > 0:
        return 0
    inserted = 0
    for spec in SOURCE_DEFS:
        session.add(MaterialSource(
            **spec,
            independence_group=spec["key"],
            independence_version=1,
            extract_version=1,
            extract_config_hash="",
            sanitizer_version=SANITIZER_VERSION,
            sanitizer_config_hash=SANITIZER_CONFIG_HASH,
        ))
        inserted += 1
    session.commit()
    return inserted
