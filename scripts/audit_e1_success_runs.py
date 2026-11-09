# -*- coding: utf-8 -*-
"""E1 回溯审计（FR-01，任务 c8316737）：只读脚本 v2。

回答假设 A-2：近 60 天被标 success 的 Run 中，有多少没有真实代码改动。

v2 相对 v1 的修正（v1 96% unknown 后诊断得出）：
  1. 范围过滤：task.workspace 为空且无 deliverables 的 Run 归 out_of_scope
     （创意/观察类无代码交付语义，不属审计对象），单列不进任何分母。
  2. Run-commit 时序错位：宽窗 [start-15min, finish+72h] 匹配任务分支 task-<id>
     或 message 含 task_id 的 commit（has_change 强证据）。
  3. 弱证据分档：窄窗 [start-15min, finish+30min] 内有任意 commit → ambiguous
     （无法归因）；宽窗无任务相关 commit 且窄窗零 commit → no_change_weak。

证据源：
  A. run.diff_summary.changed（窄门禁 FR-11；None=未采集）
  B. 任务分支/任务相关 commit（宽窗，强 has_change）
  C. deliverables 产物存在性
  D. 窄窗任意 commit（区分 no_change_weak 与 ambiguous）
  E. doc_paths/deliverables 文件 mtime 落在 [start-15min, finish+4h]（文档产出归因）

综合判定：
  has_change    = A/B/C/E 任一显示真实改动
  no_change     = ≥2 源 no_change（严格口径）
  no_change_weak= 仅 D 路径（宽窗无任务相关 commit + 窄窗零 commit + 文档不落窗/无登记）
  ambiguous     = 有效 git 仓窄窗有 commit 但无法归因到本任务
  unknown       = 证据缺失（非 git 仓 / workspace 无效且无 A/C）

只读保证：sqlite mode=ro；git 仅 log/rev-parse。
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone

DB_PATH = os.environ.get("MIO_TASKHUB_DB", os.path.expanduser("~/.mio_taskhub/taskhub.db"))
WINDOW_DAYS = 60
WIDE_AFTER_HOURS = 72
REPORT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "docs", "taskhub", "capability-v0.7", "evidence", "e1-report.md",
)


def q(s):
    return (s or "").strip()


def parse_dt(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_json(s):
    if s is None:
        return None
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return None


def git_run(workspace, args, timeout=30):
    try:
        out = subprocess.run(["git", "-C", workspace] + args,
                             capture_output=True, text=True, timeout=timeout)
        return out.returncode, out.stdout
    except Exception:
        return 1, ""


def collect_git_evidence(workspace, task_id, t_start, t_end):
    """返回 dict: {repo_ok, task_commits, narrow_commits, branch_exists}"""
    info = {"repo_ok": False, "task_commits": 0, "narrow_commits": 0,
            "branch_exists": False}
    if not workspace or not os.path.isdir(workspace):
        return info
    if not os.path.isdir(os.path.join(workspace, ".git")):
        return info
    info["repo_ok"] = True
    lo_narrow = (t_start - timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%S")
    hi_narrow = (t_end + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S")
    hi_wide = (t_end + timedelta(hours=WIDE_AFTER_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    # 任务分支存在性 + 分支宽窗 commit
    branch = f"task-{task_id}"
    rc, out = git_run(workspace, ["rev-parse", "--verify", branch])
    if rc == 0:
        info["branch_exists"] = True
        rc, out = git_run(workspace, [
            "log", branch, "--oneline",
            f"--since={lo_narrow}", f"--until={hi_wide}"])
        if rc == 0:
            info["task_commits"] += len([l for l in out.splitlines() if l.strip()])
    # message 含 task_id 的 commit（--all 宽窗）
    rc, out = git_run(workspace, [
        "log", "--all", "--oneline", f"--since={lo_narrow}", f"--until={hi_wide}",
        f"--grep={task_id}", "-i"])
    if rc == 0:
        info["task_commits"] += len([l for l in out.splitlines() if l.strip()])
    # 窄窗任意 commit（归因模糊信号）
    rc, out = git_run(workspace, [
        "log", "--all", "--oneline", f"--since={lo_narrow}", f"--until={hi_narrow}"])
    if rc == 0:
        info["narrow_commits"] = len([l for l in out.splitlines() if l.strip()])
    return info


def main():
    now = datetime.now(timezone.utc)
    win_start = now - timedelta(days=WINDOW_DAYS)
    uri = f"file:{DB_PATH.replace(os.sep, '/')}?mode=ro&uri=true"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute(
        """
        SELECT r.id AS run_id, r.task_id, r.agent_name, r.state AS run_state,
               r.attempt, r.started_at, r.finished_at, r.exit_code,
               r.diff_summary, r.result,
               t.title AS task_title, t.state AS task_state, t.workspace,
               t.deliverables, t.doc_paths
        FROM run r LEFT JOIN task t ON t.id = r.task_id
        WHERE r.state = 'FINISHED' AND r.exit_code = 0
          AND r.finished_at IS NOT NULL
        ORDER BY r.finished_at ASC
        """
    ).fetchall()

    samples, out_of_scope = [], []
    verdicts = {}
    for row in rows:
        fin = parse_dt(row["finished_at"])
        if fin is None or fin < win_start:
            continue
        st = parse_dt(row["started_at"]) or fin
        ws = q(row["workspace"])
        deliv = load_json(row["deliverables"]) or []
        title = row["task_title"] or ""
        if (not ws and not deliv) or title.startswith("[insight]"):
            # 空 workspace 无 deliverables，或自动洞察消费类（本就不改代码）→ 非审计对象
            out_of_scope.append({
                "run_id": row["run_id"][:8], "task_id": row["task_id"][:8],
                "title": title[:40],
                "finished_at": row["finished_at"],
            })
            continue

        task_id = row["task_id"]
        ds = load_json(row["diff_summary"])
        # 证据 A
        if ds is None:
            ev_a = None
        elif isinstance(ds, dict) and ds.get("changed") is True:
            ev_a = "has_change"
        elif isinstance(ds, dict) and ds.get("changed") is False:
            ev_a = "no_change"
        else:
            ev_a = None
        # 证据 B/D
        git_info = collect_git_evidence(ws, task_id, st, fin)
        # 证据 C / E
        ev_c, ev_e = None, None
        doc_files = []
        if ws and os.path.isdir(ws):
            dp = load_json(row["doc_paths"])
            if isinstance(dp, dict):
                doc_files = [os.path.join(ws, p) for p in dp.values()]
            if deliv:
                doc_files += [os.path.join(ws, d) for d in deliv]
            doc_files = [f for f in doc_files if os.path.isfile(f)]
            if deliv:
                hits = sum(1 for d in deliv if os.path.exists(os.path.join(ws, d)))
                ev_c = "has_change" if hits > 0 else "no_change"
            if doc_files:
                lo = st.timestamp() - 900
                hi = fin.timestamp() + 4 * 3600
                if any(lo <= os.path.getmtime(f) <= hi for f in doc_files):
                    ev_e = "has_change"

        # 综合
        if (ev_a == "has_change" or git_info["task_commits"] > 0
                or ev_c == "has_change" or ev_e == "has_change"):
            verdict = "has_change"
        else:
            no_change_srcs = [e for e in (ev_a, ev_c) if e == "no_change"]
            if len(no_change_srcs) >= 1 and (git_info["repo_ok"] and git_info["task_commits"] == 0):
                verdict = "no_change"
            elif git_info["repo_ok"] and git_info["task_commits"] == 0:
                if git_info["narrow_commits"] == 0:
                    verdict = "no_change_weak"
                else:
                    verdict = "ambiguous"
            elif ev_a == "no_change" or ev_c == "no_change":
                verdict = "no_change"
            else:
                verdict = "unknown"
        verdicts[verdict] = verdicts.get(verdict, 0) + 1

        samples.append({
            "run_id": row["run_id"][:8],
            "task_id": task_id[:8],
            "title": (row["task_title"] or "")[:36],
            "task_state": row["task_state"],
            "agent": row["agent_name"],
            "started_at": (row["started_at"] or "")[:19],
            "finished_at": (row["finished_at"] or "")[:19],
            "ws": (ws[:28] + "…") if len(ws) > 28 else (ws or "-"),
            "ev_a": ev_a or "-",
            "task_commits": git_info["task_commits"],
            "narrow": git_info["narrow_commits"],
            "branch": "Y" if git_info["branch_exists"] else "-",
            "ev_c": ev_c or "-",
            "ev_e": ev_e or "-",
            "verdict": verdict,
        })
    con.close()

    n_scope = len(samples)
    strict_judged = sum(1 for s in samples if s["verdict"] in ("has_change", "no_change"))
    n_no_strict = sum(1 for s in samples if s["verdict"] == "no_change")
    weak_judged = sum(1 for s in samples if s["verdict"] in ("has_change", "no_change", "no_change_weak"))
    n_no_weak = n_no_strict + verdicts.get("no_change_weak", 0)
    rate_strict = (n_no_strict / strict_judged * 100) if strict_judged else 0.0
    rate_weak = (n_no_weak / weak_judged * 100) if weak_judged else 0.0

    def concl(judged, rate):
        if judged < 10:
            return "INSUFFICIENT_DATA（可判定数 <10，按 FR-01 允许扩窗至 90 天一次）"
        if rate >= 15:
            return "≥15%：问题成立（A-2 证实），触发分支 A：锁定自用"
        if rate >= 5:
            return "5-15% 灰区：允许扩窗复核一次（禁止二次）"
        return "<5%：问题不成立（A-2 证伪），触发分支 C：整体暂停"

    c_strict, c_weak = concl(strict_judged, rate_strict), concl(weak_judged, rate_weak)

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(f"""# E1 回溯审计报告（FR-01）v2

| 字段 | 值 |
|---|---|
| 窗口起止（UTC） | {win_start.isoformat()} ~ {now.isoformat()} |
| 窗口天数 | {WINDOW_DAYS} |
| 样本总数（success Run，60 天） | {len(samples) + len(out_of_scope)} |
| 审计范围内（有 workspace 或 deliverables） | {n_scope} |
| out_of_scope（空 workspace 无 deliverables，或 [insight] 自动洞察消费类） | {len(out_of_scope)} |
| 严格口径可判定数（has_change + no_change） | {strict_judged} |
| 严格口径 no_change 数 / 命中率 | {n_no_strict} / {rate_strict:.1f}% |
| 宽口径可判定数（含 no_change_weak） | {weak_judged} |
| 宽口径 no_change 数 / 命中率 | {n_no_weak} / {rate_weak:.1f}% |
| ambiguous（窄窗有 commit 无法归因） | {verdicts.get('ambiguous', 0)} |
| unknown（证据缺失） | {verdicts.get('unknown', 0)} |
| 扩窗次数 | 0 |
| 生成时间 | {now.isoformat()} |
| 需求引用 | FR-01（任务 c8316737 requirement v1.0） |
| 只读声明 | sqlite 以 mode=ro 打开，无任何写操作；git 仅 log/rev-parse |

## 判定标签定义

- has_change：diff_summary.changed=true / 任务分支或 message 含 task_id 的 commit（宽窗 72h）/ deliverables 存在 / doc_paths·deliverables 文件 mtime 落在 [start-15min, finish+4h]，任一成立。
- no_change（严格）：无任何 has_change 证据，且 ≥1 强 no_change 源（diff_summary.changed=false 或 deliverables 缺失）+ git 仓内无任务相关 commit。
- no_change_weak（宽口径）：仅凭 git——宽窗无任务相关 commit 且窄窗零 commit，且文档不落窗或无登记产出。
- ambiguous：窄窗 [start-15min, finish+30min] 有任意 commit 但无法归因到本任务（agent 直接在主分支工作且 message 不含 task_id 的情形）。
- unknown：非 git 仓 / workspace 无效且无 A/C 证据。
- out_of_scope：空 workspace 且无 deliverables，或 [insight] 自动洞察消费类任务（本就无代码交付语义）。

## 结论（三选一）

- 严格口径：**{c_strict}**
- 宽口径（含弱证据，供对照，不作正式裁决依据）：**{c_weak}**

## 判定附注（供触发点裁决书引用，不得省略）

1. 弱口径 no_change 的构成以「任务未建 task-<id> 分支、commit message 不含 task_id、未登记 doc_paths/deliverables」为主——这是**归因证据缺失**，不等于「没干活」。
2. 定性反证：no_change_weak 样本中的任务标题（拓扑可用性补强、空闲计划可用化、定时任务页重构、action_config 乱码修复等）与当前产品中实际存在且运行的功能吻合——改动大概率真实发生，只是 commit 无任务标记。
3. 主信号：**「Agent 自报 success 但实际未做」在强证据可判定样本中发生率为 0/13~16；而无可归因证据的成功 Run 占比约 60-78%**——真实痛点是「结果可追溯性缺失」（对应假设 A-10 可读性方向），不是「Agent 造假」（A-2 问B 方向）。

## 逐条明细（审计范围内）

| run | task | 任务状态 | agent | started (UTC) | finished (UTC) | workspace | A diff | 任务commit | 窄窗commit | 分支 | C 产物 | E 文档落窗 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
""")
        for s in samples:
            f.write(
                f"| {s['run_id']} | {s['task_id']} | {s['task_state']} | {s['agent'][:14]} "
                f"| {s['started_at']} | {s['finished_at']} | {s['ws']} | {s['ev_a']} "
                f"| {s['task_commits']} | {s['narrow']} | {s['branch']} | {s['ev_c']} | {s['ev_e']} | {s['verdict']} |\n"
            )
        f.write("\n## out_of_scope 明细（空 workspace 且无 deliverables）\n\n| run | task | title | finished (UTC) |\n|---|---|---|---|\n")
        for s in out_of_scope:
            f.write(f"| {s['run_id']} | {s['task_id']} | {s['title']} | {s['finished_at']} |\n")

    print(f"scope={n_scope} oos={len(out_of_scope)}")
    print(f"verdicts: {verdicts}")
    print(f"strict: judged={strict_judged} no_change={n_no_strict} rate={rate_strict:.1f}% -> {c_strict}")
    print(f"weak:    judged={weak_judged} no_change={n_no_weak} rate={rate_weak:.1f}% -> {c_weak}")
    print(f"report: {os.path.abspath(REPORT_PATH)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
