# -*- coding: utf-8 -*-
"""素材库不变量审计（I1~I18 的核心子集）。用法：python scripts/materials_audit.py"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import func  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from mio_taskhub.db import engine, init_db  # noqa: E402
from mio_taskhub.materials.models import (  # noqa: E402
    Material, MaterialExtract, MaterialRaw, MaterialWitness,
)


def _check(name: str, rows: list) -> dict:
    return {"check": name, "ok": not rows, "count": len(rows), "sample": rows[:5]}


def run_audit() -> dict:
    init_db()
    checks = []
    with Session(engine) as s:
        orphan = s.exec(
            select(Material.id).where(
                ~Material.id.in_(select(MaterialWitness.material_id))
            )
        ).all()
        checks.append(_check("I1_material_without_witness", list(orphan)))

        prim = s.exec(
            select(MaterialWitness.material_id, func.count())
            .where(MaterialWitness.is_primary == True)  # noqa: E712
            .group_by(MaterialWitness.material_id)
            .having(func.count() != 1)
        ).all()
        checks.append(_check("I2_primary_witness_count", [tuple(r) for r in prim]))

        quote_bad = []
        for m in s.exec(select(Material)).all():
            w = s.exec(
                select(MaterialWitness).where(
                    MaterialWitness.material_id == m.id,
                    MaterialWitness.is_primary == True,  # noqa: E712
                )
            ).first()
            if w is None or m.quote != w.quote_exact:
                quote_bad.append(m.id)
        checks.append(_check("I3_quote_matches_primary", quote_bad))

        occ_bad = []
        corr_bad = []
        for m in s.exec(select(Material)).all():
            rows = s.exec(
                select(
                    MaterialWitness.source_key,
                    MaterialWitness.external_id,
                    func.max(MaterialWitness.occurrence_count),
                ).where(MaterialWitness.material_id == m.id)
                .group_by(MaterialWitness.source_key, MaterialWitness.external_id)
            ).all()
            occ = sum(int(r[2] or 0) for r in rows) or 1
            corr = s.execute(
                select(func.count(func.distinct(MaterialWitness.independence_group)))
                .where(MaterialWitness.material_id == m.id)
            ).scalar_one()
            if occ != m.occurrences:
                occ_bad.append({"material": m.id, "stored": m.occurrences, "actual": occ})
            if int(corr or 1) != m.corroboration:
                corr_bad.append({"material": m.id, "stored": m.corroboration, "actual": int(corr or 1)})
        checks.append(_check("I5_occurrences", occ_bad))
        checks.append(_check("I6_corroboration", corr_bad))

        version_bad = []
        locator_bad = []
        extract_bad = []
        for w in s.exec(select(MaterialWitness)).all():
            r = s.get(MaterialRaw, w.raw_id)
            if r is None:
                version_bad.append({"witness": w.id, "raw": w.raw_id})
                continue
            if (w.sanitizer_version != r.sanitizer_version
                    or w.sanitized_payload_hash != r.sanitized_payload_hash
                    or w.raw_revision != r.raw_revision):
                version_bad.append({"witness": w.id, "raw": r.id})
            try:
                loc = json.loads(w.locator)
            except Exception:
                loc = {}
            if loc.get("raw_revision") != w.raw_revision:
                locator_bad.append({"witness": w.id})
            ex = s.get(MaterialExtract, w.current_extract_id)
            if ex is None or ex.material_id != w.material_id or ex.raw_id != w.raw_id:
                extract_bad.append({"witness": w.id, "extract": w.current_extract_id})
        checks.append(_check("I12_witness_raw_version", version_bad))
        checks.append(_check("I13_locator_revision", locator_bad))
        checks.append(_check("I14_current_extract", extract_bad))

        version_range_bad = []
        for m in s.exec(select(Material)).all():
            versions = set(s.exec(
                select(MaterialExtract.extract_version)
                .where(MaterialExtract.material_id == m.id)
            ).all())
            for v in (m.first_extract_version, m.last_extract_version):
                if v not in versions:
                    version_range_bad.append({"material": m.id, "version": v, "known": sorted(versions)})
                    break
        checks.append(_check("I16_extract_versions", version_range_bad))

    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
    }


if __name__ == "__main__":
    print(json.dumps(run_audit(), ensure_ascii=False, indent=2))
