# -*- coding: utf-8 -*-
"""回填证据差异：业务表前后哈希 + 素材表计数/分布/重复检查。"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

BUSINESS = [
    "task", "run", "event", "historyevent", "discussion",
    "tasktemplate", "scheduledjob",
]
MATERIAL = [
    "material_source", "material_raw", "material", "material_witness",
    "material_extract", "material_link", "material_source_audit",
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--before", required=True)
    p.add_argument("--after", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    before = json.loads(Path(args.before).read_text(encoding="utf-8"))["tables"]
    after = json.loads(Path(args.after).read_text(encoding="utf-8"))["tables"]
    business = {}
    for t in BUSINESS:
        b, a = before.get(t, {}), after.get(t, {})
        business[t] = {
            "before_rows": b.get("rows"),
            "after_rows": a.get("rows"),
            "before_sha256": b.get("sha256"),
            "after_sha256": a.get("sha256"),
            "same": b.get("sha256") == a.get("sha256"),
        }

    with sqlite3.connect(f"file:{Path(args.db).as_posix()}?mode=ro", uri=True) as c:
        counts = {t: int(c.execute(f"select count(*) from '{t}'").fetchone()[0])
                  for t in MATERIAL if c.execute(
                      "select count(*) from sqlite_master where type='table' and name=?",
                      (t,)).fetchone()[0]}
        duplicates = {
            "raw_revision": c.execute(
                "select count(*) from (select source_key, external_id, raw_revision "
                "from material_raw group by 1,2,3 having count(*)>1)").fetchone()[0],
            "material_key": c.execute(
                "select count(*) from (select kind, scope_key, content_fingerprint "
                "from material group by 1,2,3 having count(*)>1)").fetchone()[0],
            "witness": c.execute(
                "select count(*) from (select material_id, raw_id from material_witness "
                "group by 1,2 having count(*)>1)").fetchone()[0],
            "extract": c.execute(
                "select count(*) from (select material_id, raw_id, extract_version "
                "from material_extract group by 1,2,3 having count(*)>1)").fetchone()[0],
        }
        orphan_material = c.execute(
            "select count(*) from material m where not exists "
            "(select 1 from material_witness w where w.material_id=m.id)").fetchone()[0]
        orphan_witness = c.execute(
            "select count(*) from material_witness w where not exists "
            "(select 1 from material m where m.id=w.material_id)").fetchone()[0]
        by_status = dict(c.execute(
            "select status, count(*) from material group by 1").fetchall())
        by_kind = dict(c.execute(
            "select kind, count(*) from material group by 1").fetchall())
        by_origin = dict(c.execute(
            "select origin, count(*) from material group by 1").fetchall())
    out = {
        "business": business,
        "business_identical": all(v["same"] for v in business.values()),
        "material_counts": counts,
        "duplicates": duplicates,
        "orphan_material": orphan_material,
        "orphan_witness": orphan_witness,
        "by_status": by_status,
        "by_kind": by_kind,
        "by_origin": by_origin,
    }
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
