# -*- coding: utf-8 -*-
"""P5 回归诊断：用修复后的提取器重跑旧 200 样本的原始 payload。

旧样本只用于诊断，不作为新校准样本。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

from mio_taskhub.materials.extract import (
    extract_for_source, has_citable_signal, is_generic_failure,
)
from mio_taskhub.materials.quality import evaluate_draft

TERM = re.compile(r"[。！？]|[.!?](?=\s|$)")
ITEM = re.compile(r"[①-⑩]|(?<!\d)\d+[)）、]")


def _old_non_atomic(q: str, source: str) -> bool:
    if source not in ("doc.review", "discussion.closed", "run.result"):
        return False
    return len(q) > 240 or len(TERM.findall(q)) > 2 or len(ITEM.findall(q)) >= 3


def run(sample_path: Path, labels_path: Path, db_path: Path) -> dict:
    samples = {r["sample_id"]: r for r in (
        json.loads(x) for x in sample_path.read_text(encoding="utf-8").splitlines() if x.strip())}
    labels = [json.loads(x) for x in labels_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    details = []
    for lab in labels:
        sample = samples[lab["sample_id"]]
        row = conn.execute(
            "select m.project, w.raw_id, w.source_key, w.raw_revision, "
            "w.sanitizer_version, r.payload "
            "from material m join material_witness w on w.material_id=m.id and w.is_primary=1 "
            "join material_raw r on r.id=w.raw_id where m.id=?",
            (lab["material_id"],),
        ).fetchone()
        if row is None:
            continue
        payload = json.loads(row["payload"])
        source_key = row["source_key"]
        drafts = extract_for_source(source_key, payload)
        materializable = []
        for d in drafts:
            if source_key in ("hn", "github-trending") and not has_citable_signal(
                    d.quote, source_key):
                continue
            if source_key in ("run.failure", "history.root_cause") and is_generic_failure(d.quote):
                continue
            gate = evaluate_draft(
                d, payload, source_key, d.scope_key or "",
                row["raw_revision"], row["sanitizer_version"],
            )
            if gate.ok:
                materializable.append(d)
        details.append({
            "sample_id": lab["sample_id"],
            "material_id": lab["material_id"],
            "cell": lab["cell"],
            "old_kind": lab["kind"],
            "label_kind": lab["label_kind"],
            "old_quote": lab["quote"],
            "old_usable": lab["label_usable"],
            "new_drafts": len(drafts),
            "new_materializable": len(materializable),
            "new_kinds": sorted({d.kind for d in materializable}),
            "new_quotes": [d.quote for d in materializable],
            "old_quote_emitted": any(d.quote == lab["quote"] for d in drafts),
            "old_non_atomic": _old_non_atomic(lab["quote"], source_key),
        })
    conn.close()

    review = [d for d in details if d["cell"] == "problem|internal"]
    generic = [d for d in details if d["cell"] == "problem|task"
               and is_generic_failure(d["old_quote"])]
    non_atomic = [d for d in details if d["old_non_atomic"]]
    signal_no = [d for d in details if d["cell"] == "signal|external"
                 and d["old_usable"] == "no"]
    kind_err = [d for d in details if d["old_kind"] != d["label_kind"]]

    def _materializable_kind_matches(rows):
        return sum(
            1 for d in rows
            if d["label_kind"] in d["new_kinds"] and d["new_materializable"] > 0
        )

    max_new_len = max(
        (len(q) for d in non_atomic for q in d["new_quotes"]), default=0)
    return {
        "n": len(details),
        "review_problem_internal": {
            "n": len(review),
            "new_decision": sum(1 for d in review if "decision" in d["new_kinds"]),
            "still_problem": sum(1 for d in review if "problem" in d["new_kinds"]),
            "materializable": sum(1 for d in review if d["new_materializable"] > 0),
        },
        "generic_failures": {
            "n": len(generic),
            "new_drafts": sum(d["new_drafts"] for d in generic),
            "new_materializable": sum(d["new_materializable"] for d in generic),
        },
        "non_atomic": {
            "n": len(non_atomic),
            "still_emitted": sum(1 for d in non_atomic if d["old_quote_emitted"]),
            "materializable": sum(1 for d in non_atomic if d["new_materializable"] > 0),
            "max_new_quote_len": max_new_len,
        },
        "signal_no_claim": {
            "n": len(signal_no),
            "new_materializable": sum(d["new_materializable"] for d in signal_no),
        },
        "kind_errors": {
            "n": len(kind_err),
            "corrected": _materializable_kind_matches(kind_err),
        },
        "details": details,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", required=True)
    p.add_argument("--labels", required=True)
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    out = run(Path(args.sample), Path(args.labels), Path(args.db))
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "details"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
