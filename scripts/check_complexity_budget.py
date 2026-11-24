"""复杂度豁免预算门禁（棘轮）：复杂度 noqa / eslint-disable 只许减少，不许新增。

背景（2026-11-23）：ruff / eslint 的阈值本身是硬门禁，但存量巨型函数靠
noqa 注释（C901,PLR0912 等）和 eslint-disable 注释（complexity 等）绕过。
若不约束豁免数量，AI 可以随手加一条注释把任何新增违规"合法化"，硬上限形同虚设。
本脚本把豁免数量本身作为棘轮指标：当前值 > 基线 → 退出码 1；减少后更新基线。

用法：
  python scripts/check_complexity_budget.py              # CI 门禁
  python scripts/check_complexity_budget.py --write-baseline  # 清理存量后本地更新基线
  python scripts/check_complexity_budget.py --verbose     # 列出每处豁免

基线文件：scripts/complexity_budget.json（与脚本同目录）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Windows runner 的 stdout 默认编码是 cp1252，本脚本的中文输出会让门禁自身
# 以 UnicodeEncodeError 退出（2026-11-23 main CI run 45099075735 实证）。
# GitHub Actions 日志按 UTF-8 采集，这里显式重配；不可重配的流保持原样。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
BASELINE = Path(__file__).resolve().parent / "complexity_budget.json"

PY_RULES = ("C901", "PLR0911", "PLR0912", "PLR0915")
JS_RULES = ("complexity", "max-depth", "max-lines-per-function", "max-statements")
PY_NOQA = re.compile(r"#\s*noqa:[^\r\n]*")
JS_DISABLE = re.compile(r"eslint-disable(?:-next-line)?[^\r\n]*")


def scan() -> tuple[Counter, dict[str, list[str]]]:
    """返回 (按规则计数, 按规则→'文件:行:匹配文本')。"""
    counts: Counter = Counter()
    positions: dict[str, list[str]] = {rule: [] for rule in (*PY_RULES, *JS_RULES)}

    for sub in ("mio_taskhub", "scripts", "tests"):
        for path in (ROOT / sub).rglob("*.py"):
            for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                m = PY_NOQA.search(line)
                if not m:
                    continue
                for rule in PY_RULES:
                    if re.search(r"\b" + re.escape(rule) + r"\b", m.group(0)):
                        counts[rule] += 1
                        positions[rule].append(f"{path.relative_to(ROOT).as_posix()}:{lineno}")

    for path in (ROOT / "web" / "src").rglob("*.js*"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = JS_DISABLE.search(line)
            if not m:
                continue
            for rule in JS_RULES:
                if re.search(r"\b" + re.escape(rule) + r"\b", m.group(0)):
                    counts[rule] += 1
                    positions[rule].append(f"{path.relative_to(ROOT).as_posix()}:{lineno}")

    return counts, positions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write-baseline", action="store_true", help="将当前豁免数写为新基线（只在主动清理存量后使用）")
    ap.add_argument("--verbose", action="store_true", help="列出每处豁免")
    args = ap.parse_args()

    counts, positions = scan()
    total = sum(counts.values())

    if args.write_baseline:
        BASELINE.write_text(
            json.dumps(dict(sorted(counts.items())), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"基线已写入 {BASELINE.relative_to(ROOT).as_posix()}：{dict(sorted(counts.items()))}（合计 {total}）")
        return 0

    if not BASELINE.exists():
        print(f"缺少基线文件 {BASELINE.relative_to(ROOT).as_posix()}；运行 --write-baseline 初始化")
        return 2

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    deltas = [(rule, counts[rule], baseline.get(rule, 0)) for rule in sorted(set(counts) | set(baseline))]
    regressions = [(r, cur, base) for r, cur, base in deltas if cur > base]
    improvements = [(r, cur, base) for r, cur, base in deltas if cur < base]

    if args.verbose:
        for rule, places in positions.items():
            for loc in places:
                print(f"  {rule}: {loc}")

    print(f"复杂度豁免预算：当前 {total}，基线 {sum(baseline.values())}")
    if regressions:
        for rule, cur, base in regressions:
            print(f"  ✗ {rule}: {cur} > 基线 {base}（新增 {cur - base} 处）")
            for loc in positions[rule][base:]:
                print(f"      {loc}")
        print("新增复杂度豁免被拒绝：请拆分函数降低复杂度，确需保留则先降低阈值并重设基线。")
        return 1

    if improvements:
        for rule, cur, base in improvements:
            print(f"  ✓ {rule}: {cur} < 基线 {base}（减少 {base - cur} 处）")
        print("豁免数已下降，请运行 python scripts/check_complexity_budget.py --write-baseline 固化棘轮。")

    print("复杂度豁免预算检查通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
