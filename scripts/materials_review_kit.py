# -*- coding: utf-8 -*-
"""P5.1 复核包构建与校验。

- `--build`：从样本+标签重建盲复核包、空白表与 manifest（确定性）。
- `--verify`：校验冻结包内各文件 hash 与 manifest 一致。

盲包只输出 `sample_id` / `source_key` / `quote`，不含类型、状态、质量分。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

PACK_FIELDS = ("sample_id", "source_key", "quote")
FREEZE_FILES = (
    "review-pack.jsonl",
    "RULES-20261028.md",
    "REVIEW-PROTOCOL-P51.md",
    "review-sheet.tsv",
    "adjudication-sheet.tsv",
    "README.md",
    "REPORT-TEMPLATE.md",
)
SHEET_HEADER = (
    "sample_id\tsource_key\tquote\tlabel_kind\tlabel_usable\tconfidence\t"
    "reason\treviewer_id\treviewed_at\tround\n"
)
ADJ_HEADER = (
    "sample_id\treviewer_a_kind\treviewer_a_usable\treviewer_b_kind\t"
    "reviewer_b_usable\tagreement\tadjudicated_kind\tadjudicated_usable\t"
    "adjudicator\treason\tat\n"
)


def _rows(path: Path) -> List[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def select_pack(labels: List[dict], limit: int = 30) -> List[dict]:
    """确定性选样：隔离样本优先，再按层各取可用/不可用，去重截断。"""

    def pick(cell: str, usable: str, k: int) -> List[dict]:
        return [r for r in labels
                if r["cell"] == cell and r["label_usable"] == usable
                and r["status"] == "active"][:k]

    picks: List[dict] = [r for r in labels if r["status"] == "quarantine"][:3]
    for cell in ("decision|internal", "result|task", "signal|external"):
        picks += pick(cell, "yes", 4)
        picks += pick(cell, "no", 4)
    picks += pick("problem|task", "yes", 2)
    picks += pick("problem|task", "no", 1)

    seen = set()
    pack = []
    for r in picks:
        if r["sample_id"] in seen:
            continue
        seen.add(r["sample_id"])
        pack.append({k: r[k] for k in PACK_FIELDS})
        if len(pack) >= limit:
            break
    return pack


def _tsv_quote(text: str) -> str:
    return text.replace("\t", " ").replace("\n", "\\n")


def build(sample: Path, labels: Path, out: Path, rules: Path,
          protocol: Path, readme: Path, template: Path) -> dict:
    pack = select_pack(_rows(labels))
    if len(pack) != 30:
        raise ValueError(f"expected 30 samples, got {len(pack)}")

    out.mkdir(parents=True, exist_ok=True)
    (out / "review-pack.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in pack), encoding="utf-8")
    for src in (rules, protocol, readme, template):
        (out / src.name).write_bytes(src.read_bytes())

    with (out / "review-sheet.tsv").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(SHEET_HEADER)
        for r in pack:
            fh.write(f'{r["sample_id"]}\t{r["source_key"]}\t'
                     f'{_tsv_quote(r["quote"])}\t\t\t\t\t\t\t\n')

    with (out / "adjudication-sheet.tsv").open("w", encoding="utf-8",
                                               newline="\n") as fh:
        fh.write(ADJ_HEADER)
        for r in pack:
            fh.write(f'{r["sample_id"]}\t\t\t\t\t\t\t\t\t\t\n')

    manifest = {
        "kit": "P5.1 independent review kit",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "protocol": "REVIEW-PROTOCOL-P51.md v1.0-frozen",
        "samples": len(pack),
        "sample_source": str(sample.name),
        "files": {},
        "note": ("Do not edit the pack/rules/protocol after the first reviewer "
                 "starts. Raw sheets are preserved; adjudication is separate."),
    }
    for name in FREEZE_FILES:
        p = out / name
        manifest["files"][name] = {
            "size": p.stat().st_size,
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def verify(kit: Path) -> List[str]:
    manifest = json.loads((kit / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for name, meta in manifest["files"].items():
        p = kit / name
        if not p.exists():
            problems.append(f"missing: {name}")
            continue
        size = p.stat().st_size
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        if size != meta["size"]:
            problems.append(f"size mismatch: {name} {size} != {meta['size']}")
        if digest != meta["sha256"]:
            problems.append(f"sha256 mismatch: {name}")
    return problems


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--build", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--kit", required=True)
    p.add_argument("--sample")
    p.add_argument("--labels")
    p.add_argument("--rules")
    p.add_argument("--protocol")
    p.add_argument("--readme")
    p.add_argument("--template")
    args = p.parse_args()
    kit = Path(args.kit)

    if args.build:
        missing = [n for n in ("sample", "labels", "rules", "protocol",
                               "readme", "template") if not getattr(args, n)]
        if missing:
            raise SystemExit(f"--build requires: {', '.join('--' + m for m in missing)}")
        m = build(Path(args.sample), Path(args.labels), kit, Path(args.rules),
                  Path(args.protocol), Path(args.readme), Path(args.template))
        print(f"built {kit} samples={m['samples']}")
    if args.verify:
        problems = verify(kit)
        for item in problems:
            print(f"FAIL {item}")
        if not problems:
            print(f"OK {kit} files={len(FREEZE_FILES)}")
            return 0
        return 1
    if not (args.build or args.verify):
        raise SystemExit("nothing to do: pass --build and/or --verify")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
