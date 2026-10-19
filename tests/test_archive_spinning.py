# -*- coding: utf-8 -*-
"""P11：归档脚本识别 idea 评审空转残留（task_kind=REVIEW 的 FAILED/CANCELLED）。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import archive_spinning_tasks as a  # noqa: E402
from mio_taskhub.models import Task, TaskKind, TaskState, TaskStage  # noqa: E402


def _mk(title, kind, state, stage):
    return Task(title=title, task_kind=kind, state=state, stage=stage)


def test_review_failed_is_spinning():
    t = _mk("「某想法」想法评审", TaskKind.REVIEW, TaskState.FAILED, TaskStage.IMPLEMENTING)
    assert a._is_spinning(t) is True


def test_review_completed_not_spinning():
    t = _mk("「某想法」想法评审", TaskKind.REVIEW, TaskState.COMPLETED, TaskStage.DONE)
    assert a._is_spinning(t) is False


def test_normal_failed_not_spinning():
    t = _mk("普通任务", TaskKind.NORMAL, TaskState.FAILED, TaskStage.IMPLEMENTING)
    assert a._is_spinning(t) is False


def test_cron_auto_still_spinning():
    t = _mk("别的标题", TaskKind.NORMAL, TaskState.CANCELLED, TaskStage.READY)
    t.labels = ["cron:abc", "auto"]
    assert a._is_spinning(t) is True
