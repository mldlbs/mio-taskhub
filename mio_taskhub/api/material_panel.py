# -*- coding: utf-8 -*-
"""素材面板（只读）：每日创意生成链路的素材可追溯视图。

回答三个验收指标的数据来源（2026-10-08 用户定义）：
1. 真实素材进入配对的比例 —— creativity combos 的 source 分类统计
2. 有效洞察比例 —— insights 空 section / LLM 失败标记
3. 产出可追溯 —— 洞察回显原始素材来源分布、配对回显完整 source 原文

数据源（全部只读，不写任何文件）：
- <observer_base>/observations/*.json —— 每日原始抓取（来源分布 + 样本标题）
- <observer_base>/insights/*.json —— 洞察产出（空 section / confidence 可见）
- <observer_base>/topics/*.json —— 每日选题记录（topic 与窗口匹配度核对入口）
- MIO_HOME/creativity/creativity-{hypotheses,combos}.jsonl —— 配对与假设记录

分类口径（边界 3：测试/手动/例行必须分开统计）：
- store 记录不含触发来源，无法区分「手动触发」与「例行生产」——两者都归
  production，createdAt 原样返回供人工核对；
- source 命中测试特征（brandnew-*/S1-S6/alpha-beta-gamma 组合/unique-x/
  中文内容占位）→ 归类 test，**绝不计入生产指标**。
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Query

from mio_taskhub import mio_runtime

logger = logging.getLogger("mio_taskhub.material_panel")

router = APIRouter(prefix="/api/v1/mio", tags=["mio-runtime"])

_MAX_READ = 8 * 1024 * 1024

# 测试残留的 source 特征（2026-10-08 核验报告 §1：13 条假素材的名字）
_TEST_PATTERNS = (
    re.compile(r"^brandnew-", re.I),
    re.compile(r"^unique-x", re.I),
    re.compile(r"^alpha\s*x?$", re.I), re.compile(r"^alpha\s*x\b", re.I),
    re.compile(r"^beta\s*y?$", re.I), re.compile(r"^beta\s*y\b", re.I),
    re.compile(r"^gamma\s*z?$", re.I), re.compile(r"^gamma\s*z\b", re.I),
    re.compile(r"^S\d$", re.I),
)
_TEST_SUBSTRINGS = ("中文内容", "alpha x", "beta y", "gamma z")


def _is_test_source(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return False
    low = n.lower()
    if any(s in low for s in _TEST_SUBSTRINGS):
        return True
    return any(p.search(n) for p in _TEST_PATTERNS)


def _source_type(name: str) -> str:
    """单条 source 的类型（供逐条标注与统计）。"""
    n = (name or "").strip()
    if not n:
        return "empty"
    if _is_test_source(n):
        return "test"
    if n.startswith("template-goal") or n.startswith("template-context"):
        return "template"
    if n.startswith("observer-insight:"):
        return "insight"
    if n.startswith("observer-observations") or n.startswith("taskhub"):
        return "legacy"
    if n.startswith("跨领域类比") or n.startswith("already-explored"):
        return "instruction"
    return "other"


def _classify_record(sources: List[str]) -> str:
    """一条 combo/hypothesis 的类别。"""
    types = [_source_type(s) for s in (sources or [])]
    if not types:
        return "empty"
    if "test" in types:
        return "test"
    if "insight" in types:
        return "with-insight"
    if all(t in ("template", "instruction", "legacy", "empty") for t in types):
        return "template-only"
    return "other"


def _read_jsonl_tail(p: Path, limit: int) -> List[dict]:
    if not p.is_file():
        return []
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            if size > _MAX_READ:
                f.seek(size - _MAX_READ)
                f.readline()
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda r: r.get("createdAt") or 0)
    return out[-max(1, min(limit, 500)):]


def _read_json(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return None


def _observations(days: int, base: Path) -> List[dict]:
    """每日原始抓取：来源分布 + 每来源前几条标题样本（可追溯原始输入）。"""
    d = base / "observations"
    if not d.is_dir():
        return []
    today = date.today()
    out = []
    for f in sorted(d.glob("*.json"))[-days:]:
        r = _read_json(f)
        if not r:
            continue
        items = r.get("observations") or []
        by_src: Counter = Counter()
        samples: dict = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            src = str(it.get("source") or "?")
            by_src[src] += 1
            if len(samples.get(src, [])) < 3:
                title = str(it.get("title") or it.get("content") or "")[:120]
                samples.setdefault(src, []).append(title)
        out.append({
            "date": str(r.get("date") or f.stem),
            "total": len(items),
            "sources": [{"source": s, "count": c, "samples": samples.get(s, [])}
                        for s, c in by_src.most_common()],
        })
    return out


def _insights(days: int, base: Path) -> List[dict]:
    """洞察产出：空 section / LLM 失败 / 选题匹配核对所需的当天来源分布。"""
    d = base / "insights"
    if not d.is_dir():
        return []
    obs_by_date = {o["date"]: {s["source"]: s["count"] for s in o["sources"]}
                   for o in _observations(days, base)}
    out = []
    for f in sorted(d.glob("*.json"))[-days * 5:]:
        r = _read_json(f)
        if not r:
            continue
        sections = r.get("sections") or []
        gen = str(r.get("generatedAt") or "")
        day = gen[:10]
        out.append({
            "id": r.get("id"),
            "topic": r.get("topic"),
            "generatedAt": gen,
            "sections_count": len(sections),
            "section_titles": [s.get("title") for s in sections if isinstance(s, dict)],
            "preview": next((str(s.get("content") or "")[:200]
                             for s in sections if isinstance(s, dict) and s.get("content")), ""),
            "confidence": r.get("metadata", {}).get("confidence"),
            "llmCalls": r.get("metadata", {}).get("llmCalls"),
            "durationMs": r.get("metadata", {}).get("durationMs"),
            "empty": len(sections) == 0,
            "llm_failed": len(sections) == 0,
            "same_day_sources": obs_by_date.get(day, {}),
        })
    return out


def _topics(days: int, base: Path) -> List[dict]:
    d = base / "topics"
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json"))[-days:]:
        r = _read_json(f)
        if not r:
            continue
        t = r.get("topic") or {}
        out.append({"file": f.stem, "selectedAt": r.get("selectedAt"),
                    "topic": t.get("topic") if isinstance(t, dict) else t,
                    "fallback": r.get("fallback")})
    return out


def _creativity(limit: int) -> dict:
    """配对/假设记录 + 分类统计（测试残留单独计数，绝不混入生产指标）。"""
    home = mio_runtime.home() / "creativity"
    combos = _read_jsonl_tail(home / "creativity-combos.jsonl", limit)
    hyps = _read_jsonl_tail(home / "creativity-hypotheses.jsonl", limit)

    def combo_row(r: dict) -> dict:
        srcs = [s if isinstance(s, str) else str(s.get("name") or "") for s in r.get("sources") or []]
        dt = r.get("createdAt")
        return {
            "createdAt": dt,
            "createdAtIso": datetime.fromtimestamp(dt / 1000).isoformat(timespec="seconds")
            if isinstance(dt, (int, float)) else None,
            "class": _classify_record(srcs),
            "sourceTypes": [_source_type(s) for s in srcs],
            "sources": srcs,
            "description": str(r.get("description") or "")[:160],
        }

    def hyp_row(r: dict) -> dict:
        labs = [str(x) for x in (r.get("sourceLabels") or [])]
        dt = r.get("createdAt")
        return {
            "createdAt": dt,
            "createdAtIso": datetime.fromtimestamp(dt / 1000).isoformat(timespec="seconds")
            if isinstance(dt, (int, float)) else None,
            "class": _classify_record(labs),
            "title": str(r.get("title") or "")[:120],
            "status": r.get("status"),
            "strategy": r.get("strategy"),
            "sourceLabels": labs,
        }

    combo_rows = [combo_row(r) for r in combos]
    hyp_rows = [hyp_row(r) for r in hyps]
    cc = Counter(r["class"] for r in combo_rows)
    hc = Counter(r["class"] for r in hyp_rows)
    non_test_combos = cc.get("with-insight", 0) + cc.get("template-only", 0) + cc.get("other", 0)
    real_ratio = round(cc.get("with-insight", 0) / non_test_combos, 4) if non_test_combos else None
    return {
        "metrics": {
            "real_material_pair_ratio": real_ratio,  # 真实素材进入配对的比例（剔除测试后）
            "combos_total": len(combo_rows),
            "combos_by_class": dict(cc),
            "hypotheses_total": len(hyp_rows),
            "hypotheses_by_class": dict(hc),
        },
        "combos": combo_rows[::-1],   # 新的在前
        "hypotheses": hyp_rows[::-1],
    }


@router.get("/material-panel")
def material_panel(days: int = Query(7, ge=1, le=30), limit: int = Query(200, ge=1, le=500)):
    """素材面板聚合快照（只读）。days 控制观察/洞察/选题窗口。"""
    base = Path(mio_runtime.observer_base_dir())
    obs = _observations(days, base)
    ins = _insights(days, base)
    valid = [i for i in ins if not i["llm_failed"]]
    return {
        "observer_base": str(base),
        "window_days": days,
        "observations": obs,
        "insights": ins,
        "insight_metrics": {
            "total": len(ins),
            "llm_failed": sum(1 for i in ins if i["llm_failed"]),
            "valid": len(valid),
            "valid_ratio": round(len(valid) / len(ins), 4) if ins else None,
        },
        "topics": _topics(days, base),
        "creativity": _creativity(limit),
        "classification_note": "store 不含触发来源：test=source 命中测试特征；"
                               "with-insight=配对含观察洞察；template-only=配对双方均为模板文本；"
                               "手动/例行触发无法从 store 区分，按 createdAt 人工核对。",
    }
