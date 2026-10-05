# -*- coding: utf-8 -*-
"""pre-push 钩子主体：对关联 task-<id> 的分支同时执行
  ② spec/api 已 approved 校验 与 ③ FR-n 可追溯校验。

由 scripts/git-hooks/pre-push（bash 入口）委托调用。读取 stdin 的 ref 行：
  <local-ref> <local-sha> <remote-ref> <remote-sha>
任一检查阻塞 → 整体退出非 0，拒绝推送。
"""
import os
import sys

# 让本文件能 import scripts/ 下的 check_* 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from doc_gate_common import parse_task_id_from_branch  # noqa: E402
from check_doc_approved import check_doc_approved      # noqa: E402
from check_fr_trace import check_fr_trace              # noqa: E402


def main():
    block = False
    saw_task = False
    for line in sys.stdin:
        parts = line.split()
        if len(parts) != 4:
            continue
        local_ref, local_sha, _remote_ref, remote_sha = parts
        if not local_ref.startswith("refs/heads/"):
            continue
        branch = local_ref[len("refs/heads/"):]
        task_id = parse_task_id_from_branch(branch)
        if not task_id:
            continue
        saw_task = True
        sys.stderr.write("[doc-gate] 分支 %s → 任务 %s\n" % (branch, task_id))

        try:
            allow1, reason1 = check_doc_approved(task_id)
        except Exception as e:  # noqa: BLE001 — 方案 §7：解析异常一律放行并告警
            allow1, reason1 = True, "check_doc_approved 异常（已放行）：%s" % e
        sys.stderr.write("  [docs]    " + reason1 + "\n")
        if not allow1:
            block = True

        try:
            allow2, reason2 = check_fr_trace(task_id, local_sha, remote_sha)
        except Exception as e:  # noqa: BLE001 — 同上，工具崩溃不得卡死推送
            allow2, reason2 = True, "check_fr_trace 异常（已放行）：%s" % e
        sys.stderr.write("  [FR-n]    " + reason2 + "\n")
        if not allow2:
            block = True

    if not saw_task:
        sys.stderr.write(
            "[doc-gate] 当前推送分支未关联 task-<id>，跳过文档门控。\n"
        )
    sys.exit(1 if block else 0)


if __name__ == "__main__":
    main()
