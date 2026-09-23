# -*- coding: utf-8 -*-
"""文档 Consult 门控脚本的纯逻辑测试（不触网）。

覆盖：
- 分支名 / 文本解析任务 id
- FR-n 抽取（含 diff 新增行语义）
- ③ FR 可追溯：缺失编号阻塞、全部存在放行、未引用放行
- ② spec/api 批准：mock task 状态分别验证放行/阻塞
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import doc_gate_common as c  # noqa: E402
import check_fr_trace as fr  # noqa: E402
import check_doc_approved as ca  # noqa: E402
import install_hooks as ih  # noqa: E402


def test_parse_task_id_from_branch():
    # 真实 id 是 8 位 hex：不能被截断、不能漏匹配
    assert c.parse_task_id_from_branch("task-b3f970b4") == "b3f970b4"
    assert c.parse_task_id_from_branch("task-2f5db835") == "2f5db835"
    assert c.parse_task_id_from_branch("feature/task_2f5db835-fix") == "2f5db835"
    # 向后兼容纯数字
    assert c.parse_task_id_from_branch("task-123") == "123"
    assert c.parse_task_id_from_branch("feature/task_45") == "45"
    assert c.parse_task_id_from_branch("task/7-foo") == "7"
    # 无关联 / 词内 task 不误判
    assert c.parse_task_id_from_branch("main") is None
    assert c.parse_task_id_from_branch("multitask-123") is None


def test_parse_task_id_from_text():
    assert c.parse_task_id_from_text("fix(task:88): xxx") == "88"
    assert c.parse_task_id_from_text("task:b3f970b4 实现") == "b3f970b4"
    assert c.parse_task_id_from_text("no ref") is None


def test_extract_frs_from_text():
    assert c.extract_frs_from_text("见 FR-1 与 FR-12 的实现") == {"FR-1", "FR-12"}
    assert c.extract_frs_from_text("no refs here") == set()


def test_collect_diff_frs_semantics():
    # 仅新增行（+ 且非 +++）计入；删除行/上下文不计
    diff = (
        "diff --git a/x b/x\n"
        "index 111..222 100644\n"
        "--- a/x\n"
        "+++ b/x\n"
        "+ 新增实现 FR-3\n"
        " context FR-9\n"
        "- 删除 FR-99\n"
        "+ 又引 FR-3 和 FR-5\n"
    )
    frs = set()
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            frs |= c.extract_frs_from_text(line)
    assert frs == {"FR-3", "FR-5"}


def test_check_fr_trace_missing_blocks(monkeypatch):
    content = "需求：FR-1 登录；FR-2 登出"
    monkeypatch.setattr(fr, "collect_diff_frs", lambda *a, **k: {"FR-3"})
    allow, reason = fr.check_fr_trace("1", "L", "R", requirement_content=content)
    assert allow is False
    assert "FR-3" in reason


def test_check_fr_trace_all_present(monkeypatch):
    content = "需求：FR-1 登录；FR-2 登出"
    monkeypatch.setattr(fr, "collect_diff_frs", lambda *a, **k: {"FR-1", "FR-2"})
    allow, reason = fr.check_fr_trace("1", "L", "R", requirement_content=content)
    assert allow is True


def test_check_fr_trace_no_fr(monkeypatch):
    monkeypatch.setattr(fr, "collect_diff_frs", lambda *a, **k: set())
    allow, _ = fr.check_fr_trace("1", "L", "R", requirement_content="FR-1")
    assert allow is True


def test_check_doc_approved_ok(monkeypatch):
    monkeypatch.setattr(ca, "get_task", lambda tid: {
        "doc_statuses": {"spec": {"state": "approved"}, "api": {"state": "approved"},
                         "plan": {"state": "approved"}},
    })
    allow, reason = ca.check_doc_approved("1")
    assert allow is True


def test_check_doc_approved_blocks(monkeypatch):
    monkeypatch.setattr(ca, "get_task", lambda tid: {
        "doc_statuses": {"spec": {"state": "approved"}, "api": {"state": "draft"}},
    })
    allow, reason = ca.check_doc_approved("1")
    assert allow is False
    assert "api" in reason


def test_check_doc_approved_plan_blocks(monkeypatch):
    # plan 已纳入门控：spec/api 批准但 plan 未批准 → 阻塞
    monkeypatch.setattr(ca, "get_task", lambda tid: {
        "doc_statuses": {"spec": {"state": "approved"}, "api": {"state": "approved"},
                         "plan": {"state": "draft"}},
    })
    allow, reason = ca.check_doc_approved("1")
    assert allow is False
    assert "plan" in reason


def test_check_doc_approved_untracked_allows(monkeypatch):
    # 未登记任何文档 → 放行（向后兼容，不卡死历史任务）
    monkeypatch.setattr(ca, "get_task", lambda tid: {
        "doc_statuses": {}, "doc_paths": {},
    })
    allow, _ = ca.check_doc_approved("1")
    assert allow is True


def test_check_doc_approved_untracked_strict_blocks(monkeypatch):
    monkeypatch.setenv("MIO_DOC_GATE_STRICT", "1")
    monkeypatch.setattr(ca, "get_task", lambda tid: {
        "doc_statuses": {}, "doc_paths": {},
    })
    allow, _ = ca.check_doc_approved("1")
    assert allow is False


def test_render_pre_push_embeds_abs_root():
    content = ih.render_pre_push(r"E:\work\code\agent-dev\mio-taskhub")
    assert "E:/work/code/agent-dev/mio-taskhub" in content
    assert "pre_push_runner.py" in content
    assert content.startswith("#!/bin/sh")


def test_find_repos(tmp_path):
    repo = tmp_path / "proj"
    (repo / ".git").mkdir(parents=True)
    (tmp_path / "plain").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep" / ".git").mkdir(parents=True)
    found = ih.find_repos(str(tmp_path))
    assert str(repo) in found
    assert all("node_modules" not in p for p in found)


def test_check_doc_approved_unreachable(monkeypatch):
    import urllib.error

    def boom(tid):
        raise urllib.error.URLError("connrefused")

    monkeypatch.setattr(ca, "get_task", boom)
    allow, _ = ca.check_doc_approved("1")
    # 不可达 → 放行（不阻塞开发）
    assert allow is True
