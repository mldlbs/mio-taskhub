# -*- coding: utf-8 -*-
"""构造式修复基准：生成独立快照，验证“基线失败 / 回归通过 / 参考修复通过”。

不运行“有无素材”实验；只验证基准是否可构造、可复现、可自动判定。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
CUTOFF = "2026-10-29 00:00:00"
TIMEOUT_S = 60

TEMPLATES = [
    {
        "id": "repair-001-sum-even",
        "title": "sum_even 漏掉上界",
        "bug": "sum_even(n) 应包含 n（当 n 为偶数），当前 range(n) 排除了 n。",
        "source": "def sum_even(n):\n    return sum(i for i in range(n) if i % 2 == 0)\n",
        "gold": "def sum_even(n):\n    return sum(i for i in range(n + 1) if i % 2 == 0)\n",
        "target": "tests/test_target.py",
        "target_tests": "from calc import sum_even\n\n\ndef test_zero():\n    assert sum_even(0) == 0\n\n\ndef test_small():\n    assert sum_even(4) == 6\n\n\ndef test_ten():\n    assert sum_even(10) == 30\n",
        "regression": "from calc import sum_even\n\n\ndef test_odd():\n    assert sum_even(3) == 2\n\n\ndef test_one():\n    assert sum_even(1) == 0\n",
    },
    {
        "id": "repair-002-clamp",
        "title": "clamp 边界写反",
        "bug": "clamp 用 min(lo, max(x, hi))，上下界颠倒。",
        "source": "def clamp(x, lo, hi):\n    return min(lo, max(x, hi))\n",
        "gold": "def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n",
        "target": "tests/test_target.py",
        "target_tests": "from calc import clamp\n\n\ndef test_inside():\n    assert clamp(5, 0, 10) == 5\n\n\ndef test_low():\n    assert clamp(-1, 0, 10) == 0\n\n\ndef test_high():\n    assert clamp(11, 0, 10) == 10\n",
        "regression": "from calc import clamp\n\n\ndef test_equal_bounds():\n    assert clamp(3, 3, 3) == 3\n",
    },
    {
        "id": "repair-003-parse-bool",
        "title": "parse_bool 只认 true",
        "bug": "parse_bool 未支持 1/yes/on，且未处理空白。",
        "source": "def parse_bool(s):\n    return s.lower() == 'true'\n",
        "gold": "def parse_bool(s):\n    return s.strip().lower() in {'true', '1', 'yes', 'on'}\n",
        "target": "tests/test_target.py",
        "target_tests": "from calc import parse_bool\n\n\ndef test_one():\n    assert parse_bool('1') is True\n\n\ndef test_yes():\n    assert parse_bool(' YES ') is True\n\n\ndef test_false():\n    assert parse_bool('0') is False\n",
        "regression": "from calc import parse_bool\n\n\ndef test_true():\n    assert parse_bool('true') is True\n\n\ndef test_off():\n    assert parse_bool('off') is False\n",
    },
    {
        "id": "repair-004-median",
        "title": "median 偶数长度取错",
        "bug": "median 对偶数长度只取中间一个元素，没有取平均。",
        "source": "def median(xs):\n    xs = sorted(xs)\n    return xs[len(xs) // 2]\n",
        "gold": "def median(xs):\n    xs = sorted(xs)\n    n = len(xs)\n    mid = n // 2\n    if n % 2:\n        return xs[mid]\n    return (xs[mid - 1] + xs[mid]) / 2\n",
        "target": "tests/test_target.py",
        "target_tests": "from calc import median\n\n\ndef test_even():\n    assert median([1, 2, 3, 4]) == 2.5\n\n\ndef test_even_unsorted():\n    assert median([4, 1, 3, 2]) == 2.5\n",
        "regression": "from calc import median\n\n\ndef test_odd():\n    assert median([3, 1, 2]) == 2\n\n\ndef test_single():\n    assert median([7]) == 7\n",
    },
    {
        "id": "repair-005-slugify",
        "title": "slugify 分隔符错误",
        "bug": "slugify 用下划线连接单词，期望短横线。",
        "source": "import re\n\n\ndef slugify(s):\n    return re.sub(r'[^a-z0-9]+', '_', s.lower()).strip('_')\n",
        "gold": "import re\n\n\ndef slugify(s):\n    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')\n",
        "target": "tests/test_target.py",
        "target_tests": "from calc import slugify\n\n\ndef test_spaces():\n    assert slugify('Hello World') == 'hello-world'\n\n\ndef test_punct():\n    assert slugify(' A, B! ') == 'a-b'\n",
        "regression": "from calc import slugify\n\n\ndef test_simple():\n    assert slugify('abc') == 'abc'\n\n\ndef test_empty():\n    assert slugify('!!!') == ''\n",
    },
]


def snapshot_hash(repo: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in repo.rglob("*") if p.is_file()):
        rel = path.relative_to(repo).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(path.read_bytes())
    return h.hexdigest()


def build(out_dir: Path) -> List[dict]:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    samples = []
    for tpl in TEMPLATES:
        sample = out_dir / tpl["id"]
        repo = sample / "repo"
        (repo / "tests").mkdir(parents=True)
        (repo / "calc.py").write_text(tpl["source"], encoding="utf-8")
        (repo / "tests" / "test_target.py").write_text(
            tpl["target_tests"], encoding="utf-8")
        (repo / "tests" / "test_regression.py").write_text(
            tpl["regression"], encoding="utf-8")
        (sample / "gold").mkdir()
        (sample / "gold" / "calc.py").write_text(tpl["gold"], encoding="utf-8")
        meta = {
            "id": tpl["id"],
            "title": tpl["title"],
            "bug": tpl["bug"],
            "target_test": tpl["target"],
            "regression_test": "tests/test_regression.py",
            "timeout_s": TIMEOUT_S,
            "created_at": CUTOFF,
            "python": sys.version.split()[0],
            "snapshot_sha256": snapshot_hash(repo),
        }
        (sample / "task.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        samples.append(meta)
    return samples


def _run(repo: Path, test_path: str) -> dict:
    env = os.environ.copy()
    env["PYTHONHASHSEED"] = "0"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", test_path, "-q",
             "-p", "no:cacheprovider"],
            cwd=repo, env=env, capture_output=True, text=True,
            timeout=TIMEOUT_S)
        duration = time.time() - started
        return {"returncode": proc.returncode,
                "output": (proc.stdout + proc.stderr)[-2000:],
                "duration_s": round(duration, 3), "timed_out": False}
    except subprocess.TimeoutExpired:
        return {"returncode": None, "output": "timeout", "duration_s": None,
                "timed_out": True}


def classify(result: dict) -> str:
    if result["timed_out"]:
        return "timeout"
    out = result["output"].lower()
    if "error collecting" in out or "collectingerror" in out:
        return "collection_error"
    if "importerror" in out or "modulenotfounderror" in out:
        return "import_error"
    if "assertionerror" in out or "assert " in out:
        return "assertion"
    if result["returncode"] == 0:
        return "passed"
    return "other"


def evaluate(sample_dir: Path) -> dict:
    meta = json.loads((sample_dir / "task.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        shutil.copytree(sample_dir / "repo", work)
        baseline_target = _run(work, meta["target_test"])
        baseline_reg = _run(work, meta["regression_test"])
        shutil.copyfile(sample_dir / "gold" / "calc.py", work / "calc.py")
        gold_target = _run(work, meta["target_test"])
        gold_reg = _run(work, meta["regression_test"])
    return {
        "id": meta["id"],
        "snapshot_sha256": meta["snapshot_sha256"],
        "baseline_target": {**baseline_target,
                            "status": classify(baseline_target)},
        "baseline_regression": {**baseline_reg,
                                "status": classify(baseline_reg)},
        "gold_target": {**gold_target, "status": classify(gold_target)},
        "gold_regression": {**gold_reg, "status": classify(gold_reg)},
        "constructible": (
            baseline_target["returncode"] != 0
            and baseline_reg["returncode"] == 0
            and gold_target["returncode"] == 0
            and gold_reg["returncode"] == 0
        ),
    }


def material_binding(db: Path, cutoff: str = CUTOFF) -> dict:
    import sqlite3
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = conn.execute(
        "select m.id from material m "
        "join material_witness w on w.material_id=m.id and w.is_primary=1 "
        "where w.source_key in ('hn','github-trending') "
        "and m.observed_at <= ?", (cutoff,)).fetchall()
    conn.close()
    ids = sorted(r[0] for r in rows)
    digest = hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()
    return {"cutoff": cutoff, "available": len(ids),
            "ids_sha256": digest}


def audit(out_dir: Path, db: Path) -> dict:
    samples = build(out_dir)
    results = [evaluate(out_dir / s["id"]) for s in samples]
    ids = [s["id"] for s in samples]
    hashes = [s["snapshot_sha256"] for s in samples]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "samples": len(samples),
        "unique_ids": len(set(ids)) == len(ids),
        "unique_snapshots": len(set(hashes)) == len(hashes),
        "all_constructible": all(r["constructible"] for r in results),
        "baseline_failure_classes": sorted(
            {r["baseline_target"]["status"] for r in results}),
        "results": results,
        "materials": material_binding(db),
        "note": "只验证基准可构造性；未运行有无素材的对照实验。",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "experiments" / "runs" / "repair-benchmark"))
    p.add_argument("--db", default=os.path.expanduser("~/.mio_taskhub/taskhub.db"))
    p.add_argument("--report", default=None)
    args = p.parse_args()
    report = audit(Path(args.out), Path(args.db))
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"},
                     ensure_ascii=False, indent=2))
    for r in report["results"]:
        print(r["id"], r["constructible"],
              r["baseline_target"]["status"], r["gold_target"]["status"])
    return 0 if report["all_constructible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
