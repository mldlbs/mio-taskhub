# -*- coding: utf-8 -*-
"""冻结 G0 基线 → g0_manifest.json（04 §5.3 / §6.1，一键复现）。

汇总（DoD 字段）：
  - commit：git rev-parse HEAD + 分支 + dirty 状态（porcelain）
  - pytest 统计：子进程实跑（隔离 env：MIO_OBSERVER_AUTOSTART=0
    + MIO_TEST_DB_PATH=临时文件，F2 根治）
  - 覆盖率：--cov=mio_taskhub --cov-report=term-missing 的 TOTAL 行
  - 档位开关默认态：MIO_READ_GATE / MIO_RATCHET_DISABLED
    （未设置 = 默认态，显式记录当前值）

用法：
    python scripts/baseline_freeze.py --out g0_manifest.json
    python scripts/baseline_freeze.py --out m.json --pytest-args "tests/test_x.py -q"
    python scripts/baseline_freeze.py --out m.json --skip-pytest   # 仅结构（无统计）

退出码：pytest rc!=0 或 failed>0 → 1；git 不可用 → 1。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def git_info(repo: Path) -> dict:
    def _run(*args):
        return subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True,
            text=True, encoding="utf-8", errors="replace")

    head = _run("rev-parse", "HEAD")
    if head.returncode != 0:
        return {"error": "git unavailable", "head": head.stderr.strip()}
    branch = _run("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    dirty = _run("status", "--porcelain").stdout.splitlines()
    return {
        "head": head.stdout.strip(),
        "branch": branch,
        "dirty_count": len(dirty),
        "dirty_files": dirty[:20],
        "dirty": bool(dirty),
    }


def env_defaults() -> dict:
    """记录两个门控开关的默认态（未设置即默认）。"""
    out = {}
    for key in ("MIO_READ_GATE", "MIO_RATCHET_DISABLED"):
        val = os.environ.get(key)
        out[key] = {
            "value": val,
            "state": ("unset(default)" if val is None
                      else "overridden"),
        }
    return out


_SUMMARY_RE = re.compile(
    r"(\d+)\s+(passed|skipped|deselected|failed|error|warnings)")


def parse_pytest_summary(line: str) -> dict:
    stats = {}
    for m in _SUMMARY_RE.finditer(line):
        stats[m.group(2)] = int(m.group(1))
    m = re.search(r"in\s+([\d.]+s)$", line.strip())
    if m:
        stats["duration_seconds"] = float(m.group(1).rstrip("s"))
    return stats


def parse_coverage_total(text: str):
    for line in text.splitlines():
        if line.strip().startswith("TOTAL"):
            m = re.search(r"(\d+(?:\.\d+)?)%", line)
            if m:
                return float(m.group(1))
    return None


def run_pytest(repo: Path, pytest_args: str | None = None) -> dict:
    env = dict(os.environ)
    env["MIO_OBSERVER_AUTOSTART"] = "0"
    env.pop("MIO_READ_GATE", None)
    env.pop("MIO_RATCHET_DISABLED", None)
    fd, dbpath = tempfile.mkstemp(suffix=".db", prefix="baseline_")
    os.close(fd)
    env["MIO_TEST_DB_PATH"] = dbpath
    try:
        argv = [sys.executable, "-m", "pytest",
                "--cov=mio_taskhub", "--cov-report=term-missing"]
        if pytest_args:
            argv.extend(pytest_args.split())
        r = subprocess.run(argv, cwd=str(repo), env=env,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        combined = (r.stdout or "") + "\n" + (r.stderr or "")
        # 摘要在 coverage 表格之后：全文搜索含 "passed" 且带耗时的行
        summary_line = None
        for line in combined.splitlines():
            if "passed" in line and re.search(r"in\s+[\d.]+s", line):
                summary_line = line
        stats = (parse_pytest_summary(summary_line)
                 if summary_line else {})
        return {
            "rc": r.returncode,
            "stats": stats,
            "coverage_total_pct": parse_coverage_total(combined),
            "summary_line": summary_line,
        }
    finally:
        try:
            os.unlink(dbpath)
        except OSError:
            pass


def build_manifest(repo: Path, pytest_args: str | None = None,
                   skip_pytest: bool = False) -> dict:
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tool": "scripts/baseline_freeze.py",
        "python": sys.version.split()[0],
        "git": git_info(repo),
        "env_defaults": env_defaults(),
        "pytest": None,
    }
    if not skip_pytest:
        manifest["pytest"] = run_pytest(repo, pytest_args)
    else:
        manifest["pytest"] = {
            "skipped": True,
            "reason": "--skip-pytest: structure-only manifest",
        }
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description="冻结 G0 基线 manifest")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--pytest-args", default=None,
                    help="追加 pytest 参数（如 'tests/test_x.py -q'）")
    ap.add_argument("--skip-pytest", action="store_true",
                    help="只出结构，不跑 pytest（无统计）")
    args = ap.parse_args(argv)

    manifest = build_manifest(REPO, args.pytest_args, args.skip_pytest)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    py = manifest["pytest"] or {}
    stats = py.get("stats", {})
    print(f"[baseline_freeze] git={manifest['git'].get('head', '?')[:8]} "
          f"dirty={manifest['git'].get('dirty_count')} "
          f"pytest={stats} cov={py.get('coverage_total_pct')}")
    print(f"[baseline_freeze] manifest -> {out}")
    failed = stats.get("failed", 0) + stats.get("error", 0)
    if py.get("skipped"):
        return 0
    if py.get("rc") != 0 or failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
