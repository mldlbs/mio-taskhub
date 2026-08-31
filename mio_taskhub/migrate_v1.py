"""M1 一次性脏数据修复：state/stage 合法化 + 归一化。

调用方式：
    python -m mio_taskhub.migrate_v1            # 实际修复
    python -m mio_taskhub.migrate_v1 --dry-run   # 预览不写

规则（与 mio_taskhub.status.LEGAL_COMBOS 对齐）：
  1. state=running   AND stage≠implementing  → state=claimed, stage 保留
  2. state=retrying   AND stage≠implementing  → state=claimed, stage 保留
  3. state=completed  AND stage=implementing  → stage 推进到 review（视为 T4）
  4. state=completed  AND stage=review        → stage 推进到 done（视为 T5+T6）
  5. state=completed  AND stage∈{brain,design,planning,ready} → 标记 needs_review
  6. state=queued     AND stage=implementing  → 有活跃 run 则 claimed/impl；否则 queued/ready + block_reason
  7. state=queued     AND stage=done          → 标记 needs_review
  8. state=blocked_failed                     → state=queued + block_reason="依赖未满足"
  9. stage=CANCELLED（遗留 TaskStage.CANCELLED）→ state=cancelled, stage=BRAINSTORMING + block_reason
 10. 其他非法组合                              → 标记 needs_review

state 写入用小写（DB 原生）；stage 写入用大写（DB 原生）。
"""
from __future__ import annotations
import argparse
import csv
import os
import sys
from typing import Optional

from sqlalchemy import inspect, text


# ---------- helpers ----------
def _legal_combo_strings() -> set:
    """把 status.LEGAL_COMBOS 转为 (state_str, stage_str) 集合（小写）。"""
    from mio_taskhub.status import LEGAL_COMBOS, State, Stage
    return {(s.value, st.value) for s, st in LEGAL_COMBOS}


def _stage_upper(stage_value_lower: str) -> str:
    """小写 stage 值 → DB 原生大写。"""
    from mio_taskhub.models import TaskStage
    try:
        return TaskStage(stage_value_lower).name
    except (ValueError, KeyError):
        return stage_value_lower.upper()


def _has_active_run(conn, task_id: str) -> bool:
    """是否存在非 FINISHED 的 Run。"""
    row = conn.execute(
        text("SELECT 1 FROM run WHERE task_id = :tid AND state != 'FINISHED' LIMIT 1"),
        {"tid": task_id},
    ).first()
    return row is not None


# ---------- 核心修复函数 ----------
def fix_state_stage(conn, *, dry_run: bool = False) -> dict:
    """读取全量 task，应用修复规则，返回摘要。

    conn: SQLAlchemy 连接（带事务）
    dry_run: True 时只统计不写
    """
    legal = _legal_combo_strings()
    rows = conn.execute(text(
        "SELECT id, title, state, stage FROM task"
    )).fetchall()

    fixes: list = []          # (id, title, old, new, reason)
    needs_review: list = []   # (id, title, state, stage, reason)

    for tid, title, state, stage in rows:
        s = (state or "").lower()
        st_raw = stage or ""
        st = st_raw.lower()
        old = (s, st_raw)

        # 规则 9：stage=CANCELLED 归一为 state=cancelled
        if st == "cancelled":
            fixes.append((tid, title, old, ("cancelled", _stage_upper("brainstorming")),
                          "stage=CANCELLED 归一为 state=cancelled"))
            continue

        # 规则 8：blocked_failed
        if s == "blocked_failed":
            fixes.append((tid, title, old, ("queued", st_raw),
                          "blocked_failed → queued + block_reason"))
            continue

        # 规则 1-2：runtime 状态必须在 implementing
        if s == "running" and st != "implementing":
            fixes.append((tid, title, old, ("claimed", st_raw),
                          f"running+{st} 非法 → claimed"))
            continue
        if s == "retrying" and st != "implementing":
            fixes.append((tid, title, old, ("claimed", st_raw),
                          f"retrying+{st} 非法 → claimed"))
            continue

        # 规则 3-4：completed 阶段推进
        if s == "completed" and st == "implementing":
            fixes.append((tid, title, old, ("completed", _stage_upper("review")),
                          "completed+implementing → review（待 T5/T6）"))
            continue
        if s == "completed" and st == "review":
            fixes.append((tid, title, old, ("completed", _stage_upper("done")),
                          "completed+review → done（视为 T5+T6 已过）"))
            continue

        # 规则 5：completed 在前段（不该出现）
        if s == "completed" and st in {"brainstorming", "design", "planning", "ready"}:
            needs_review.append((tid, title, s, st_raw, "completed 出现在前段阶段"))
            continue

        # 规则 6：queued+implementing
        if s == "queued" and st == "implementing":
            if _has_active_run(conn, tid):
                fixes.append((tid, title, old, ("claimed", _stage_upper("implementing")),
                              "queued+implementing 且有活跃 run → claimed"))
            else:
                fixes.append((tid, title, old, ("queued", _stage_upper("ready")),
                              "queued+implementing 无活跃 run → ready（block_reason）"))
            continue

        # 规则 7：queued+done
        if s == "queued" and st == "done":
            needs_review.append((tid, title, s, st_raw, "queued+done 自相矛盾"))
            continue

        # 规则 10：其他非法组合
        if (s, st) not in legal:
            needs_review.append((tid, title, s, st_raw, f"非法组合 ({s},{st})"))
            continue

        # 已合法：no-op

    # 写回 fixes
    if not dry_run and fixes:
        for tid, _title, _old, (new_s, new_st), _reason in fixes:
            # 同步 block_reason
            if new_s == "queued" and (new_s, new_st.lower()) == ("queued", "ready"):
                # queued+ready from rule 6: block_reason
                br = "原 implementing 阶段状态非法，已回退到 ready 待重新调度"
                conn.execute(
                    text("UPDATE task SET state=:s, stage=:st, block_reason=:br WHERE id=:id"),
                    {"s": new_s, "st": new_st, "br": br, "id": tid},
                )
            elif new_s == "queued":
                # rule 8: blocked_failed → queued
                conn.execute(
                    text("UPDATE task SET state=:s, block_reason='依赖未满足' WHERE id=:id"),
                    {"s": new_s, "id": tid},
                )
            elif (new_s, new_st.lower()) == ("cancelled", "brainstorming"):
                # rule 9: stage=CANCELLED 归一
                conn.execute(
                    text("UPDATE task SET state=:s, stage=:st, block_reason='原 stage=CANCELLED 归一' WHERE id=:id"),
                    {"s": new_s, "st": new_st, "id": tid},
                )
            else:
                conn.execute(
                    text("UPDATE task SET state=:s, stage=:st WHERE id=:id"),
                    {"s": new_s, "st": new_st, "id": tid},
                )

    if not dry_run:
        conn.commit()

    return {
        "scanned": len(rows),
        "fixed": len(fixes),
        "needs_review": len(needs_review),
        "fixes": fixes,
        "needs_review_list": needs_review,
    }


def write_needs_review_report(needs_review: list, path: str) -> None:
    """把 needs_review 列表写成 CSV。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "title", "state", "stage", "reason"])
        for tid, title, s, st, reason in needs_review:
            w.writerow([tid, title, s, st, reason])


# ---------- CLI ----------
def main(argv: Optional[list] = None) -> int:
    from mio_taskhub.db import engine, DATA_DIR
    parser = argparse.ArgumentParser(description="M1 脏数据修复（state/stage 合法化）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写")
    parser.add_argument("--report", default=os.path.join(DATA_DIR, "migrate_v1_needs_review.csv"),
                        help="needs_review 报告 CSV 路径")
    args = parser.parse_args(argv)

    with engine.connect() as conn:
        summary = fix_state_stage(conn, dry_run=args.dry_run)

    print(f"扫描任务: {summary['scanned']}")
    print(f"已修复:   {summary['fixed']}")
    print(f"待审核:   {summary['needs_review']}")

    if summary["fixes"]:
        print("\n--- 修复明细 ---")
        for tid, title, (os_, ost), (ns, nst), reason in summary["fixes"]:
            print(f"  {tid} | {os_}/{ost} → {ns}/{nst} | {reason} | {title[:40]}")

    if summary["needs_review_list"]:
        write_needs_review_report(summary["needs_review_list"], args.report)
        print(f"\n待审核报告已写入: {args.report}")
        for tid, title, s, st, reason in summary["needs_review_list"]:
            print(f"  ! {tid} | {s}/{st} | {reason} | {title[:40]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
