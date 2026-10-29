# -*- coding: utf-8 -*-
"""第一层：自动化数据质量门禁（不依赖人工，失败即阻止发布）。

检查规范级不变量：回源、原子性、去重、来源隔离、时效、审计完整性。
**不检查业务效用**：检索命中、下游任务收益不在本门禁范围内。

用法：
    python scripts/materials_gate.py --db <db> [--out report.json]
退出码：0 通过；1 阻塞项失败。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import create_engine  # noqa: E402

from mio_taskhub.materials.common import content_fingerprint  # noqa: E402
from mio_taskhub.materials.constants import (  # noqa: E402
    LLM_SOURCE_MARKERS, TTL_DAYS,
)
from mio_taskhub.materials.pipeline import draft_reject_reason  # noqa: E402
from mio_taskhub.materials.quality import Draft  # noqa: E402
from scripts.materials_audit import run_audit  # noqa: E402
from scripts.materials_validate import validate  # noqa: E402

STRUCTURAL = ("verbatim", "atomic", "decision_shape", "bare_count", "marker_leak")
EXTRACT_STATUS = {"extracted", "rejected", "skipped"}
DT_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")


def _dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value)
        dt = None
        for fmt in DT_FORMATS:
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _check(name: str, rows: List[Any], blocking: bool = True) -> dict:
    return {
        "name": name,
        "blocking": blocking,
        "ok": not rows,
        "count": len(rows),
        "sample": rows[:5],
    }


def _sql_count(conn: sqlite3.Connection, sql: str) -> List[Any]:
    return [tuple(r) for r in conn.execute(sql).fetchall()]


def gate(db: Path, examples: int = 5) -> dict:
    checks: List[dict] = []

    # 1 审计完整性（I1-I16，含孤儿、主 witness、occ/corr、版本、locator）
    try:
        audit = run_audit(create_engine(f"sqlite:///{db.as_posix()}"))
        for item in audit["checks"]:
            checks.append({
                "name": f"audit:{item['check']}",
                "blocking": True,
                "ok": bool(item["ok"]),
                "count": int(item["count"]),
                "sample": item["sample"][:examples],
            })
    except Exception as exc:  # 审计自身失败也必须阻止发布
        checks.append({"name": "audit:error", "blocking": True, "ok": False,
                       "count": 1, "sample": [str(exc)[:300]]})

    # 2 结构检查（回源、原子性、结论形状、裸计数、标记泄漏、重复）
    report = validate(db, examples=examples)
    by_check = report["checks"]["by_check"]
    for name in STRUCTURAL:
        checks.append({
            "name": f"structural:{name}",
            "blocking": True,
            "ok": by_check.get(name, 0) == 0,
            "count": by_check.get(name, 0),
            "sample": report["structural_misaccept"]["examples"].get(name, [])[:examples],
        })
    checks.append({
        "name": "structural:verbatim_unverifiable",
        "blocking": True,
        "ok": report["checks"]["verbatim_unverifiable"] == 0,
        "count": report["checks"]["verbatim_unverifiable"],
        "sample": [],
    })
    checks.append({
        "name": "duplicates:active_fingerprint",
        "blocking": True,
        "ok": report["checks"]["duplicate_groups"] == 0,
        "count": report["checks"]["duplicate_groups"],
        "sample": list(report["duplicates"].items())[:examples],
    })

    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # 3 去重（键级）
        checks.append(_check(
            "duplicates:material_key",
            _sql_count(conn,
                       "select kind, scope_key, content_fingerprint, count(*) n "
                       "from material group by 1,2,3 having n>1")))
        checks.append(_check(
            "duplicates:raw_revision",
            _sql_count(conn,
                       "select source_key, external_id, raw_revision, count(*) n "
                       "from material_raw group by 1,2,3 having n>1")))
        checks.append(_check(
            "duplicates:witness",
            _sql_count(conn,
                       "select material_id, raw_id, count(*) n "
                       "from material_witness group by 1,2 having n>1")))
        checks.append(_check(
            "duplicates:extract",
            _sql_count(conn,
                       "select material_id, raw_id, extract_version, count(*) n, "
                       "count(distinct draft_fingerprint) fps "
                       "from material_extract group by 1,2,3 having fps>1")))

        # 4 内容指纹与 active 硬闸（I4 / I7）
        fingerprint_bad = []
        for row in conn.execute(
                "select id, quote, content_fingerprint from material"):
            if content_fingerprint(str(row["quote"])) != row["content_fingerprint"]:
                fingerprint_bad.append({"id": row["id"]})
        checks.append(_check("audit:I4_fingerprint", fingerprint_bad))

        active_gate_bad = []
        for row in conn.execute(
                "select m.id, m.kind, m.quote, m.scope_key, w.source_key, "
                "w.raw_revision, w.sanitizer_version, w.locator, r.payload "
                "from material m "
                "join material_witness w on w.material_id=m.id and w.is_primary=1 "
                "join material_raw r on r.id=w.raw_id "
                "where m.status='active'"):
            try:
                payload = json.loads(row["payload"])
                loc = json.loads(row["locator"] or "{}")
                draft = Draft(
                    kind=row["kind"], quote=row["quote"],
                    field=str(loc.get("field") or "content"),
                    char_start=loc.get("char_start"),
                    char_end=loc.get("char_end"),
                    scope_key=row["scope_key"] or "",
                )
                reason = draft_reject_reason(
                    draft, payload, row["source_key"], row["scope_key"] or "",
                    int(row["raw_revision"]), int(row["sanitizer_version"]))
            except Exception as exc:
                reason = f"payload_error:{exc}"
            if reason:
                active_gate_bad.append({"id": row["id"], "reason": reason})
        checks.append(_check("audit:I7_active_gates", active_gate_bad))

        # 5 链接与范围（I8 / I9 / I10 / I11）
        checks.append(_check(
            "audit:I8_linked_not_archived",
            _sql_count(conn,
                       "select distinct m.id from material m "
                       "join material_link l on l.material_id=m.id "
                       "where m.status not in ('active','stale')")))
        checks.append(_check(
            "audit:I9_scope",
            _sql_count(conn,
                       "select id from material where (kind='signal' and scope_key!='') "
                       "or (kind!='signal' and scope_key is null)")))
        llm_sources = [
            row[0] for row in conn.execute("select key from material_source")
            if any(marker in str(row[0]).lower() for marker in LLM_SOURCE_MARKERS)
        ]
        checks.append(_check("audit:I10_llm_source", llm_sources))
        checks.append(_check(
            "audit:I11_link_target_status",
            _sql_count(conn,
                       "select l.id from material_link l "
                       "join material m on m.id=l.material_id "
                       "where m.status not in ('active','stale')")))

        # 6 来源隔离（witness 快照非空；corroboration 已由 audit I6 校验）
        checks.append(_check(
            "isolation:witness_group_empty",
            _sql_count(conn,
                       "select id from material_witness where independence_group is null "
                       "or independence_group='' or independence_version is null "
                       "or independence_version < 1")))
        checks.append(_check(
            "isolation:source_group_empty",
            _sql_count(conn,
                       "select key from material_source where independence_group is null "
                       "or independence_group=''")))
        checks.append(_check(
            "isolation:group_change_audited",
            _sql_count(conn,
                       "select w.id from material_witness w "
                       "join material_source s on s.key=w.source_key "
                       "where w.independence_group != s.independence_group "
                       "and not exists (select 1 from material_source_audit a "
                       "where a.source_key=s.key and a.field='independence_group')")))

        # 7 提取历史（I15 / I16 / I18）
        checks.append(_check(
            "audit:I15_extract_append_only",
            _sql_count(conn,
                       "select material_id, raw_id, extract_version, "
                       "count(*) n, count(distinct draft_fingerprint) fps "
                       "from material_extract group by 1,2,3 having fps>1")))
        bad_versions = []
        for row in conn.execute(
                "select m.id, m.first_extract_version, m.last_extract_version, "
                "(select count(distinct extract_version) from material_extract e "
                " where e.material_id=m.id) n from material m"):
            versions = {r[0] for r in conn.execute(
                "select distinct extract_version from material_extract "
                "where material_id=?", (row["id"],))}
            if (row["first_extract_version"] not in versions
                    or row["last_extract_version"] not in versions
                    or int(row["first_extract_version"]) > int(row["last_extract_version"])):
                bad_versions.append({"id": row["id"], "known": sorted(versions)})
        checks.append(_check("audit:I16_extract_versions", bad_versions))
        checks.append(_check(
            "audit:I18_raw_missing",
            _sql_count(conn,
                       "select w.id from material_witness w "
                       "where not exists (select 1 from material_raw r where r.id=w.raw_id)")))


        # 5 时效：expires_at 与 last_seen + TTL 一致；observed 不晚于 last_seen
        bad_expiry: List[dict] = []
        expired_active = 0
        now = datetime.now(timezone.utc)
        for row in conn.execute(
                "select id, kind, observed_at, last_seen_at, expires_at, status "
                "from material"):
            last_seen = _dt(row["last_seen_at"])
            observed = _dt(row["observed_at"])
            expires = _dt(row["expires_at"])
            days = TTL_DAYS.get(row["kind"])
            expected = last_seen + timedelta(days=days) if (last_seen and days) else None
            if expected is None:
                bad = expires is not None
            else:
                bad = expires is None or (expires - expected).total_seconds() != 0
            if bad:
                bad_expiry.append({"id": row["id"], "kind": row["kind"],
                                   "expires_at": row["expires_at"]})
            if observed and last_seen and observed > last_seen + timedelta(seconds=1):
                bad_expiry.append({"id": row["id"], "reason": "observed>last_seen"})
            if row["status"] == "active" and expires and expires < now:
                expired_active += 1
        checks.append(_check("freshness:expires_consistency", bad_expiry))
        checks.append({
            "name": "freshness:expired_active",
            "blocking": False,
            "ok": True,
            "count": expired_active,
            "sample": [],
        })

        # 6 提取状态与审计可回读
        checks.append(_check(
            "audit:raw_status_invalid",
            _sql_count(conn,
                       "select id, extract_status from material_raw "
                       "where extract_status not in ('extracted','rejected','skipped')")))
        checks.append(_check(
            "audit:rejected_without_reason",
            _sql_count(conn,
                       "select id from material_raw where extract_status='rejected' "
                       "and (reject_reason is null or reject_reason='')")))
        checks.append(_check(
            "audit:extracted_without_witness",
            _sql_count(conn,
                       "select r.id from material_raw r where r.extract_status='extracted' "
                       "and not exists (select 1 from material_witness w where w.raw_id=r.id)")))
        checks.append(_check(
            "audit:skipped_with_witness",
            _sql_count(conn,
                       "select r.id from material_raw r where r.extract_status='skipped' "
                       "and exists (select 1 from material_witness w where w.raw_id=r.id)")))
        checks.append(_check(
            "audit:raw_without_source",
            _sql_count(conn,
                       "select r.id from material_raw r where not exists "
                       "(select 1 from material_source s where s.key=r.source_key)")))
        checks.append(_check(
            "audit:source_without_status",
            _sql_count(conn,
                       "select key from material_source where last_status is null "
                       "or last_status=''")))
    finally:
        conn.close()

    blocking = [c for c in checks if c["blocking"]]
    failed = [c for c in blocking if not c["ok"]]
    return {
        "ok": not failed,
        "db": str(db).replace("\\", "/"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checked": len(checks),
        "blocking_failures": [c["name"] for c in failed],
        "failed_count": sum(c["count"] for c in failed),
        "warnings": [c for c in checks if not c["blocking"] and not c["ok"]],
        "metrics": {
            "materials": report["population"]["materials"],
            "active": report["population"]["active"],
            "quarantine": report["population"]["quarantine"],
            "expired_active": expired_active,
        },
        "checks": checks,
        "note": "仅数据质量与系统正确性；不包含检索命中或下游效用。",
    }


def run_gate(db: Path, examples: int = 5) -> dict:
    """门禁入口：缺失库/表/列时返回 gate:error，而不是抛异常。"""
    try:
        return gate(db, examples=examples)
    except Exception as exc:
        return {
            "ok": False,
            "db": str(db).replace("\\", "/"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "blocking_failures": ["gate:error"],
            "failed_count": 1,
            "error": str(exc)[:500],
            "warnings": [],
            "metrics": {},
            "checks": [],
            "note": "门禁自身失败也必须阻止发布。",
        }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--db", required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--examples", type=int, default=5)
    args = p.parse_args()
    db = Path(args.db)
    report = run_gate(db, examples=args.examples)
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "ok": report["ok"],
        "blocking_failures": report["blocking_failures"],
        "failed_count": report["failed_count"],
        "warnings": [w["name"] for w in report["warnings"]],
        "metrics": report["metrics"],
    }, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
