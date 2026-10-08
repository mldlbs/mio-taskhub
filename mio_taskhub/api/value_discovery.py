# -*- coding: utf-8 -*-
"""价值发现实验（R292）：人工触发、只读资产、允许 0 结果。

与已暂停的每日创意 job 的本质区别——价值函数代替组合解释：
- 素材（指定日期的 github-trending + hackernews 清洗后原文）→ LLM 价值评估
- 每个机会必须：引用素材编号证据、关联内部问题、回答为什么现在、给一周验证实验与成功判据
- 允许返回 0 个机会（反凑数）；LLM 自评 origin（external=外部素材触发 / internal-led=内部问题触发）
- 结果只追加实验存档（MIO_HOME/value-discovery/runs.jsonl），不改任务/想法/素材数据

LLM 调用走 mio_runtime.llm_config()（env MIO_LLM_* > config.json，P5/FR-34 设计即进程内调用）。
LLM 失败显式 502 透传，不伪装成空结果。
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mio_taskhub import mio_runtime

logger = logging.getLogger("mio_taskhub.value_discovery")

router = APIRouter(prefix="/api/v1/mio/value-discovery", tags=["mio-runtime"])

_MAX_OBS_READ = 8 * 1024 * 1024
_MAX_ITEMS_PER_SOURCE = 40
_TIMEOUT_S = 180.0

_SYSTEM_PROMPT = """你是一名严格的技术价值评估员，不是创意写手。你的任务是：从给定的真实素材和内部问题中，找出值得行动的新机会。

铁律：
1. 每个机会必须引用素材中的具体条目作为证据（标明编号）；无证据支撑的想法一律不许提。
2. 每个机会回答五个问题：发现了什么？为什么重要？与内部什么问题相关？为什么现在值得做？如何用一周内的小实验验证（含明确的成功判据）？
3. 给每个机会标注 origin："external"（主要由外部素材触发的、原本不知道的机会）或 "internal-led"（主要由内部问题触发、外部素材提供辅助证据）。
4. 宁缺毋滥：如果素材里没有值得行动的东西，输出 {"opportunities": []} 并在 verdict 说明原因。不允许为了凑数量创造假说。
5. 输出 JSON：{"opportunities": [{"title", "origin", "evidence"(数组,引用素材编号+原文片段), "what", "why_important", "internal_link", "why_now", "one_week_experiment"(含成功判据)}], "verdict"(对素材整体价值与机会来源的一句话判断)}。所有文本用简体中文。"""


class ValueDiscoveryRequest(BaseModel):
    date: str = Field(..., description="素材日期（observations 文件名，如 2026-10-08）")
    internal_issues: List[str] = Field(..., min_length=1, description="内部近期真实问题清单")
    label: Optional[str] = Field(None, max_length=100, description="本批实验的人读标签")


def _clean_observation(s: str) -> str:
    """清洗抓取原文：去 HTML 标签 / URL 参数垃圾 / 多余空白。"""
    s = re.sub(r"<[^>]+>", " ", str(s))
    s = re.sub(r"login\?return_to=%2F[^\s⭐]*", "?", s)
    s = re.sub(r"[⭐⏎]", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()[:120]


def _load_material(base: Path, date: str) -> dict:
    """读指定日期 observations，取 github-trending + hackernews 清洗后原文。"""
    p = base / "observations" / f"{date}.json"
    if not p.is_file():
        raise HTTPException(404, f"该日期无观测数据: {date}（{p}）")
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")
        r = json.loads(raw[:_MAX_OBS_READ])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"观测数据损坏: {e}")
    items = r.get("observations") or []
    out = {}
    for source in ("github-trending", "hackernews"):
        rows = [_clean_observation(i.get("content") or i.get("title") or "")
                for i in items if isinstance(i, dict) and i.get("source") == source]
        rows = [x for x in rows if x]
        out[source] = rows[:_MAX_ITEMS_PER_SOURCE]
    if not any(out.values()):
        raise HTTPException(404, f"{date} 无 github-trending/hackernews 素材")
    return out


def _call_llm(system: str, user: str) -> dict:
    """进程内直调 LLM（显式触发、显式报错）。"""
    c = mio_runtime.llm_config()
    if not (c["apiUrl"] and c["apiKey"]):
        raise HTTPException(503, "LLM 未配置（config.json llm 段缺失 apiUrl/apiKey）")
    body = json.dumps({
        "model": c["model"], "temperature": 0.3, "max_tokens": 2500,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}]}).encode()
    req = urllib.request.Request(
        c["apiUrl"], data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + c["apiKey"]})
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=_TIMEOUT_S)
        payload = json.load(resp)
    except urllib.error.HTTPError as e:
        raise HTTPException(502, f"LLM 调用失败: HTTP {e.code} {e.read().decode()[:200]}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"LLM 调用失败: {e}")
    duration = round(time.time() - t0, 1)
    content = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not content.strip():
        raise HTTPException(502, f"LLM 返回空 content（模型={c['model']}，{duration}s）——显式失败，不伪装")
    text = content.strip()
    for prefix in ("```json", "```"):  # fence 容忍
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text.endswith("```"):
        text = text[:-3]
    try:
        return json.loads(text.strip()), duration
    except Exception:  # noqa: BLE001
        raise HTTPException(502, f"LLM 输出不可解析 JSON（{duration}s）: {content[:200]}")


def _archive_dir() -> Path:
    p = mio_runtime.home() / "value-discovery"
    p.mkdir(parents=True, exist_ok=True)
    return p


@router.post("")
def run_value_discovery(req: ValueDiscoveryRequest):
    """人工触发的价值发现实验（烧 LLM；结果只入实验存档，不进任务/想法库）。"""
    base = Path(mio_runtime.observer_base_dir())
    material = _load_material(base, req.date)
    gh = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(material["github-trending"]))
    hn = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(material["hackernews"]))
    issues = "\n".join(f"- {x.strip()}" for x in req.internal_issues if str(x).strip())
    user = (f"【{req.date} 真实素材 · GitHub Trending（{len(material['github-trending'])} 条）】\n{gh}\n\n"
            f"【{req.date} 真实素材 · Hacker News（{len(material['hackernews'])} 条）】\n{hn}\n\n"
            f"【内部近期真实问题】\n{issues}\n\n请找出值得行动的机会（0~3 个）。")

    result, duration = _call_llm(_SYSTEM_PROMPT, user)
    opportunities = result.get("opportunities") if isinstance(result, dict) else None
    if not isinstance(opportunities, list):
        raise HTTPException(502, f"LLM 输出缺 opportunities 字段: {str(result)[:200]}")

    record = {
        "id": f"vd_{int(time.time()*1000)}",
        "date": req.date,
        "label": req.label,
        "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": duration,
        "material_counts": {k: len(v) for k, v in material.items()},
        "internal_issues_count": len(req.internal_issues),
        "opportunities": opportunities,
        "verdict": result.get("verdict"),
    }
    archive = _archive_dir() / "runs.jsonl"
    with open(archive, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


@router.get("")
def list_value_discovery():
    """历史实验记录 + 指标统计（R292 的 6 指标，human acceptance 除外——那项只能人工判）。"""
    archive = _archive_dir() / "runs.jsonl"
    runs: List[dict] = []
    if archive.is_file():
        for line in archive.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                runs.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    runs.sort(key=lambda r: r.get("createdAt") or "")
    ops_all = [o for r in runs for o in (r.get("opportunities") or [])]
    n_ops = len(ops_all)
    stats = {
        "batches": len(runs),
        "opportunities_total": n_ops,
        "opportunity_rate": round(n_ops / len(runs), 2) if runs else None,
        "zero_batches": sum(1 for r in runs if not (r.get("opportunities") or [])),
        "evidence_coverage": (sum(1 for o in ops_all if (o.get("evidence") or []).__len__() > 0) / n_ops
                              if n_ops else None),
        "internal_relevance": (sum(1 for o in ops_all if (o.get("internal_link") or "").strip() != "") / n_ops
                               if n_ops else None),
        "actionability": (sum(1 for o in ops_all if (o.get("one_week_experiment") or "").strip() != "") / n_ops
                          if n_ops else None),
        "external_origin_rate": (sum(1 for o in ops_all if o.get("origin") == "external") / n_ops
                                 if n_ops else None),
    }
    return {"runs": runs[::-1], "stats": stats}
