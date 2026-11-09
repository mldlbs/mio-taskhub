# -*- coding: utf-8 -*-
"""E5 submit_review 回放（FR-05，任务 c8316737）：只读脚本。

回答假设 A-6：硬身份隔离（reviewer ≠ 执行者）在单人场景是否可行。

分析口径：
  1. reviewer 字符串分布（自由字符串的证据强度）
  2. 结论长度（summary+comments）
  3. 结论是否关联 FR-n（可追溯性）
  4. 模拟硬隔离阻塞率：approve 类 review 中，reviewer == 该任务最近一次
     执行 run 的 agent_name 的比例（自审占比）→ 阻塞率

判定（FR-05）：
  阻塞率 ≥50% → 单人场景硬隔离不可行，O4 降级或推迟
  ≤20%        → 已有多角色，可实施

只读保证：sqlite mode=ro；无任何写操作。
"""
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

DB_PATH = os.environ.get("MIO_TASKHUB_DB", os.path.expanduser("~/.mio_taskhub/taskhub.db"))
REPORT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "docs", "taskhub", "capability-v0.7", "evidence", "e5-report.md",
)

FR_PAT = re.compile(r"\bFR[-–]?\s?\d+", re.IGNORECASE)


def main():
    now = datetime.now(timezone.utc).isoformat()
    uri = f"file:{DB_PATH.replace(os.sep, '/')}?mode=ro&uri=true"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute(
        """
        SELECT rv.id, rv.task_id, rv.decision, rv.summary, rv.comments,
               rv.reviewer, rv.review_duration_sec, rv.created_at,
               (SELECT r.agent_name FROM run r WHERE r.task_id = rv.task_id
                 AND r.state='FINISHED' ORDER BY r.finished_at DESC LIMIT 1
               ) AS last_executor
        FROM taskreview rv
        ORDER BY rv.created_at ASC
        """
    ).fetchall()

    total = len(rows)
    reviewer_dist = {}
    fr_linked = 0
    approve_rows, self_approve = [], 0
    samples = []
    for row in rows:
        reviewer = (row["reviewer"] or "").strip() or "(空)"
        reviewer_dist[reviewer] = reviewer_dist.get(reviewer, 0) + 1
        text = (row["summary"] or "") + (row["comments"] or "")
        has_fr = bool(FR_PAT.search(text))
        if has_fr:
            fr_linked += 1
        if row["decision"] == "approve":
            approve_rows.append(row)
            exec_ = (row["last_executor"] or "").strip()
            if exec_ and reviewer == exec_:
                self_approve += 1
        samples.append({
            "review_id": row["id"][:8],
            "task_id": row["task_id"][:8],
            "decision": row["decision"],
            "reviewer": reviewer[:24],
            "last_executor": (row["last_executor"] or "-")[:24],
            "len": len(text),
            "fr": "Y" if has_fr else "-",
            "self": "Y" if row["decision"] == "approve" and (row["last_executor"] or "") == reviewer else "-",
        })
    con.close()

    n_approve = len(approve_rows)
    block_rate = (self_approve / n_approve * 100) if n_approve else 0.0
    avg_len = sum(s["len"] for s in samples) / total if total else 0.0

    if n_approve < 15:
        conclusion = "INSUFFICIENT_DATA（历史 review 不足 15 条）"
    elif block_rate >= 50:
        conclusion = f"阻塞率 {block_rate:.0f}% ≥50%：单人场景硬隔离不可行，O4 降级为 S4.3 或推迟"
    elif block_rate <= 20:
        conclusion = f"阻塞率 {block_rate:.0f}% ≤20%：已有多角色，可实施"
    else:
        conclusion = f"阻塞率 {block_rate:.0f}% 处于 20-50% 灰区：部分可行，硬隔离需谨慎评估"

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(f"""# E5 submit_review 回放报告（FR-05）

| 字段 | 值 |
|---|---|
| 历史 review 总数 | {total} |
| 其中 approve | {n_approve} |
| reviewer 去重数 | {len(reviewer_dist)} |
| 结论平均长度（summary+comments，字符） | {avg_len:.0f} |
| 结论关联 FR-n 的条数 | {fr_linked}（{fr_linked / total * 100 if total else 0:.0f}%） |
| 自审 approve 数（reviewer == 最近执行 agent） | {self_approve} |
| 模拟硬隔离阻塞率 | {block_rate:.1f}% |
| 生成时间 | {now} |
| 需求引用 | FR-05（任务 c8316737 requirement v1.0） |
| 只读声明 | sqlite mode=ro 打开，无任何写操作，未触发任何 409/状态机变更 |

## 结论

**{conclusion}**

## reviewer 分布

| reviewer | 条数 |
|---|---|
""")
        for k, v in sorted(reviewer_dist.items(), key=lambda x: -x[1]):
            f.write(f"| {k} | {v} |\n")
        f.write("\n## 逐条明细\n\n| review | task | decision | reviewer | 最近执行者 | 结论长度 | 关联FR | 自审 |\n|---|---|---|---|---|---|---|---|\n")
        for s in samples:
            f.write(
                f"| {s['review_id']} | {s['task_id']} | {s['decision']} | {s['reviewer']} "
                f"| {s['last_executor']} | {s['len']} | {s['fr']} | {s['self']} |\n"
            )

    print(f"total={total} approve={n_approve} reviewers={len(reviewer_dist)} "
          f"fr_linked={fr_linked} self_approve={self_approve} block_rate={block_rate:.1f}%")
    print(f"conclusion: {conclusion}")
    print(f"report: {os.path.abspath(REPORT_PATH)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
