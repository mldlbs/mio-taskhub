# -*- coding: utf-8 -*-
"""Mio 记忆只读观测投影（迁移自 mneme 只读观测层，语义逐案对齐；源仓库零改动 FR-10）。

只读数据源：MIO_HOME/memory.jsonl + MIO_HOME/experience_reuse.jsonl。
输出 {entities, relations, meta} 图谱 JSON，供 GET /api/v1/memory/observatory/data
（FR-1）与 Rail「记忆观测」视图使用。

边界（FR-10 / 观测层定稿）：不写入、不做生命周期、不做权威检索、不做评分裁决；
不 import 写路径、不触碰 hub DB、不调用 Mio MCP、零 Node 依赖。

对应用例见 tests/test_memory_observatory.py（FR-1~FR-6）。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from mio_taskhub import mio_runtime

# ---- 常量（与 mneme 投影语义一致，FR-3/FR-4）----

MIO_KIND_MAP = {
    "decision": "rule",
    "context": "context",
    "problem": "problem",
    "note": "note",
    "experience": "experience",
}  # 未知 kind → "note"

KIND_WEIGHTS = {"decision": 3, "context": 2, "problem": 2, "experience": 2, "note": 0}

DECISION_WORDS_RE = re.compile(r"决定|采用|必须|禁止|架构|原因|约定")

LOG_TAG = "task-outcome"

_PREFIX_RE = re.compile(r"^\[[^\]]*\]\s*")

_ZERO_EV = {"reuse": 0, "improved": 0}

_skipped = {"mio": 0, "reuse": 0}
_cache: dict = {"key": None, "data": None}
_lock = threading.Lock()


def data_paths() -> dict:
    """源文件路径：MIO_HOME 经 mio_runtime.home() 解析（env 覆盖，默认 ~/.mio-intelligence）。"""
    home = mio_runtime.home()
    return {
        "mioHome": home,
        "mioMemFile": home / "memory.jsonl",
        "mioReuseFile": home / "experience_reuse.jsonl",
    }


def read_jsonl(path: Path, bucket: str | None = None) -> list:
    """逐行 JSON 解析；空行跳过；坏行计入 skipped[bucket] 不中断（FR-5）。"""
    if not path.exists():
        return []
    out = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.split("\n"):
        t = line.strip()
        if not t:
            continue
        try:
            out.append(json.loads(t))
        except (json.JSONDecodeError, ValueError):
            if bucket:
                _skipped[bucket] += 1
    return out


def clean_mio_content(text) -> list:
    """按行去掉 \"[agent] \" 类前缀并清空行（FR-4，对应 mneme cleanMioContent）。"""
    lines = []
    for raw in str(text if text is not None else "").split("\n"):
        cleaned = _PREFIX_RE.sub("", raw).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def derive_mio_title(lines: list) -> str:
    """首句标题：优先首个 ≥4 字符行，>60 截断加省略号（FR-4，对应 deriveMioTitle）。"""
    first = ""
    for line in lines:
        if len(line) >= 4:
            first = line
            break
    if not first:
        first = lines[0] if lines else ""
    if len(first) > 60:
        return first[:60] + "…"
    return first


def load_evidence(paths: dict) -> tuple:
    """复用证据按 experienceId 聚合 {reuse, improved}；records=可解析总行数（FR-5）。"""
    mapping: dict = {}
    records = 0
    for r in read_jsonl(paths["mioReuseFile"], "reuse"):
        records += 1
        eid = r.get("experienceId") if isinstance(r, dict) else None
        if not eid:
            continue
        key = str(eid)
        entry = mapping.setdefault(key, {"reuse": 0, "improved": 0})
        if r.get("reuse"):
            entry["reuse"] += 1
        if r.get("outcomeImproved"):
            entry["improved"] += 1
    return mapping, records


def compute_reuse_score(mem: dict, tags: list, ev: dict) -> int:
    """复用热度公式（FR-3，与 mneme computeReuseScore 一致；呈现用、非权威评分）。

    5×复用 + 3×改善 + 非*-observer 源 +3 + 类型权重 + 决策词 +1
    + task-outcome 标签 −2，下限 0。
    """
    source = str(mem.get("source") or "")
    is_observer = source.endswith("-observer")
    score = 5 * int(ev.get("reuse") or 0) + 3 * int(ev.get("improved") or 0)
    if not is_observer:
        score += 3
    score += KIND_WEIGHTS.get(mem.get("kind"), 0)
    if any(t.lower() == LOG_TAG for t in tags):
        score -= 2
    if DECISION_WORDS_RE.search(str(mem.get("content") or "")):
        score += 1
    return max(0, score)


def parse_mio(paths: dict, show_logs: bool) -> dict:
    """MIO_HOME → 命名实体/关系/统计（FR-2/FR-4/FR-5，对应 mneme parseMio）。"""
    entities: list = []
    relations: list = []
    stats = {"memTotal": 0, "memWithEvidence": 0, "reuseRecords": 0}
    mem_file: Path = paths["mioMemFile"]
    if not mem_file.exists():
        return {"entities": entities, "relations": relations, "stats": stats}

    evidence, records = load_evidence(paths)
    stats["reuseRecords"] = records
    added: set = set()

    def add_entity(name, etype, obs, title, reuse_score):
        if name in added:
            return
        added.add(name)
        entities.append({
            "name": name,
            "type": etype,
            "obs": obs,
            "title": title or name,
            "reuse_score": reuse_score or 0,
        })

    for m in read_jsonl(mem_file, "mio"):
        if not isinstance(m, dict) or not m.get("id"):
            continue
        tags = [str(t) for t in m["tags"]] if isinstance(m.get("tags"), list) else []
        is_log = (m.get("kind") == "note") and any(t.lower() == LOG_TAG for t in tags)

        # 证据覆盖率统计覆盖全部记忆（含被观察视图隐藏的 log）
        stats["memTotal"] += 1
        ev = evidence.get(str(m["id"]), _ZERO_EV)
        if ev["reuse"] > 0:
            stats["memWithEvidence"] += 1
        if is_log and not show_logs:
            continue

        name = "mio-" + str(m["id"])
        etype = "log" if is_log else MIO_KIND_MAP.get(m.get("kind"), "note")
        cleaned = clean_mio_content(m.get("content"))
        title = derive_mio_title(cleaned) or ("Mio " + (str(m.get("kind") or "note")))
        source = str(m.get("source") or "unknown")
        project = m.get("project")
        tags_suffix = (" #" + " #".join(tags)) if tags else ""
        meta = (
            "来源 " + source
            + " · 项目 " + str(project or "-")
            + " · " + str(m.get("kind") or "note")
            + tags_suffix
        )
        obs = [meta] + cleaned
        add_entity(name, etype, obs, title, compute_reuse_score(m, tags, ev))

        src = "mio-src-" + source
        add_entity(src, "source", ["Mio 记忆来源：" + source], "来源 " + source, 0)
        relations.append({"from": name, "to": src, "rel": "来自"})
        if project:
            p = "mio-proj-" + str(project)
            add_entity(p, "project", ["Mio 项目：" + str(project)], "项目 " + str(project), 0)
            relations.append({"from": name, "to": p, "rel": "属于"})

    return {"entities": entities, "relations": relations, "stats": stats}


def build_data(show_logs: bool) -> dict:
    """命名实体→顺序编号，组装 meta（FR-1/FR-4，对应 mneme buildData）。"""
    paths = data_paths()
    parsed = parse_mio(paths, show_logs)

    entities: list = []
    name_map: dict = {}
    eid = 0
    for e in parsed["entities"]:
        if e["name"] in name_map:
            continue
        name_map[e["name"]] = eid
        entities.append({
            "id": eid,
            "name": e["name"],
            "type": e["type"],
            "obs": e["obs"],
            "title": e["title"],
            "reuse_score": e["reuse_score"],
        })
        eid += 1

    relations: list = []
    for r in parsed["relations"]:
        s = name_map.get(r["from"])
        t = name_map.get(r["to"])
        if s is not None and t is not None:
            relations.append({"source": s, "target": t, "rel": r["rel"]})

    now = datetime.now(timezone.utc)
    return {
        "entities": entities,
        "relations": relations,
        "meta": {
            "generatedAt": now.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "showLogs": bool(show_logs),
            "skipped": dict(_skipped),
            "counts": {"entities": len(entities), "relations": len(relations)},
            "evidence": dict(parsed["stats"]),
        },
    }


def _file_sig(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return "x"


def build_data_cached(show_logs: bool) -> dict:
    """mtime 缓存：key=两源文件签名+showLogs；Lock 内双检（FR-6，对应 buildDataCached）。"""
    paths = data_paths()
    key = "|".join([
        str(paths["mioMemFile"]),
        _file_sig(paths["mioMemFile"]),
        str(paths["mioReuseFile"]),
        _file_sig(paths["mioReuseFile"]),
        str(bool(show_logs)),
    ])
    with _lock:
        if _cache["key"] == key and _cache["data"] is not None:
            return _cache["data"]
        _skipped["mio"] = 0
        _skipped["reuse"] = 0
        data = build_data(bool(show_logs))
        _cache["key"] = key
        _cache["data"] = data
        return data


def _reset_cache() -> None:
    """测试注入点：清空缓存（对应 mneme _resetCache）。"""
    with _lock:
        _cache["key"] = None
        _cache["data"] = None


def _get_skipped() -> dict:
    """测试注入点：坏行计数快照（对应 mneme _getSkipped）。"""
    return dict(_skipped)


def env_force_hide_logs() -> bool:
    """MIO_OBSERVATORY_HIDE_LOGS 设任意非空值 → 强制观察视图（FR-2，端点层判定）。"""
    return bool(os.environ.get("MIO_OBSERVATORY_HIDE_LOGS"))
