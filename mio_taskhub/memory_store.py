"""Memory Store: 本地 JSONL 知识图谱，替代 MCP 子进程方案。

数据模型（兼容 mneme）：
- entity: {"type":"entity","name":"...","entityType":"...","observations":["..."]}
- relation: {"type":"relation","from":"...","to":"...","relationType":"..."}

API：
- query(keyword, kind, project, limit) → 搜索实体
- record(kind, context, payload, project) → 写入实体
- policy_check(operation, context) → 策略检查（简单本地判断）
- observer_ingest(trace_id, event_type, payload, outcome) → 记录观察
- experience_reuse(source, target, experience_id, reuse, ...) → 记录经验复用
- health() → 健康状态
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

# 数据文件路径
_DATA_DIR = os.environ.get(
    "MIO_MEMORY_DIR",
    os.path.join(os.path.expanduser("~"), ".mio_taskhub"),
)
_DATA_FILE = os.path.join(_DATA_DIR, "memory.jsonl")

# kind → entityType 映射（兼容 mneme）
_KIND_MAP = {
    "decision": "rule",
    "context": "context",
    "problem": "problem",
    "note": "note",
    "experience": "experience",
    "trace": "trace",
    "observer": "observer",
}

_lock = threading.Lock()


# ---------- 数据读写 ----------

def _ensure_dir():
    os.makedirs(_DATA_DIR, exist_ok=True)


def _load_all() -> list[dict]:
    """读取全部行。"""
    if not os.path.exists(_DATA_FILE):
        return []
    items = []
    with open(_DATA_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def _load_entities() -> list[dict]:
    return [i for i in _load_all() if i.get("type") == "entity"]


def _load_relations() -> list[dict]:
    return [i for i in _load_all() if i.get("type") == "relation"]


def _append(items: list[dict]):
    """追加写入 JSONL。"""
    _ensure_dir()
    with open(_DATA_FILE, "a", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def _rewrite(items: list[dict]):
    """重写整个文件。"""
    _ensure_dir()
    with open(_DATA_FILE, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


# ---------- 查询 ----------

def search_entities(keywords: list[str], kind: str = None,
                    project: str = None, limit: int = 20) -> list[dict]:
    """按关键词搜索实体，支持 kind/project 过滤。"""
    entities = _load_entities()
    keywords_lower = [k.lower() for k in keywords] if keywords else []

    results = []
    for ent in entities:
        # kind 过滤
        if kind:
            ent_type = ent.get("entityType", "")
            expected = _KIND_MAP.get(kind, kind)
            if ent_type != expected:
                continue

        # project 过滤（observations 中含 project 信息）
        if project:
            obs_text = " ".join(ent.get("observations", [])).lower()
            if project.lower() not in obs_text:
                continue

        # 关键词评分
        if keywords_lower:
            name = ent.get("name", "")
            etype = ent.get("entityType", "")
            obs_text = " ".join(ent.get("observations", [])).lower()
            score = 0
            for kw in keywords_lower:
                if kw in name.lower():
                    score += 3
                if kw in etype.lower():
                    score += 2
                if kw in obs_text:
                    score += 1
            if score == 0:
                continue
            ent["_score"] = score

        results.append(ent)

    # 按评分排序
    results.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return results[:limit]


def add_entity(name: str, etype: str, observations: list[str],
               relations: list[dict] | None = None):
    """添加或更新实体，可选添加关系。"""
    with _lock:
        items = _load_all()
        entities = [i for i in items if i.get("type") == "entity"]
        existing = next((e for e in entities if e.get("name") == name), None)

        new_lines = []

        if existing:
            # 合并 observations
            old_obs = set(existing.get("observations", []))
            for o in observations:
                if o not in old_obs:
                    existing["observations"].append(o)
            # 重写整个文件
            for i, item in enumerate(items):
                if item.get("type") == "entity" and item.get("name") == name:
                    items[i] = existing
                    break
            _rewrite(items)
            return

        # 新建实体
        entity = {
            "type": "entity",
            "name": name,
            "entityType": etype,
            "observations": list(observations),
        }
        new_lines.append(entity)

        # 添加关系
        if relations:
            existing_rels = set()
            for item in items:
                if item.get("type") == "relation":
                    existing_rels.add((
                        item.get("from"), item.get("to"), item.get("relationType")
                    ))
            for rel in relations:
                key = (rel.get("from"), rel.get("to"), rel.get("relationType", "related to"))
                if key not in existing_rels:
                    new_lines.append({
                        "type": "relation",
                        "from": rel["from"],
                        "to": rel["to"],
                        "relationType": rel.get("relationType", "related to"),
                    })
                    existing_rels.add(key)

        _append(new_lines)


# ---------- 高层 API ----------

def query_memories(kind: str = None, project: str = None,
                   limit: int = 20, keyword: str = None) -> dict:
    """查询记忆。"""
    keywords = [keyword] if keyword else []
    if kind:
        keywords = keywords or [kind]
    entities = search_entities(keywords, kind=kind, project=project, limit=limit)
    return {
        "entities": entities,
        "total": len(entities),
    }


def record_memory(kind: str, context: str = "", payload: dict = None,
                  project: str = None) -> dict:
    """记录记忆。"""
    etype = _KIND_MAP.get(kind, "note")
    observations = []
    if context:
        observations.append(context)
    if payload:
        for k, v in payload.items():
            observations.append(f"{k}: {v}")
    if project:
        observations.append(f"project: {project}")

    name = f"mem-{kind}-{int(time.time() * 1000)}"
    add_entity(name, etype, observations)
    return {"ok": True, "name": name}


def policy_check(operation: str, context: dict = None) -> dict:
    """策略检查（简单本地判断）。"""
    # 简单实现：高风险操作返回警告
    high_risk = {"delete_task", "migrate_db", "drop_table", "delete_user"}
    if operation in high_risk:
        return {
            "allowed": False,
            "reason": f"High risk operation: {operation}",
            "suggestion": "Review manually before proceeding",
        }
    return {"allowed": True, "reason": "Low risk"}


def observer_ingest(trace_id: str, event_type: str, payload: dict = None,
                    outcome: str = "success") -> dict:
    """记录观察事件。"""
    observations = [
        f"trace_id: {trace_id}",
        f"event_type: {event_type}",
        f"outcome: {outcome}",
    ]
    if payload:
        for k, v in payload.items():
            observations.append(f"{k}: {v}")

    name = f"trace-{trace_id[:8]}"
    add_entity(name, "trace", observations)
    return {"ok": True}


def experience_reuse(source_agent: str, target_agent: str,
                     experience_id: str, reuse: bool,
                     behavior_changed: bool = False,
                     outcome_improved: bool = None) -> dict:
    """记录经验复用。"""
    observations = [
        f"source: {source_agent}",
        f"target: {target_agent}",
        f"experience_id: {experience_id}",
        f"reused: {reuse}",
        f"behavior_changed: {behavior_changed}",
    ]
    if outcome_improved is not None:
        observations.append(f"outcome_improved: {outcome_improved}")

    name = f"exp-{experience_id[:8]}"
    add_entity(name, "experience", observations)
    # 添加关系
    add_entity(source_agent, "agent", [f"Agent: {source_agent}"])
    add_entity(target_agent, "agent", [f"Agent: {target_agent}"])
    add_entity(experience_id, "experience", [f"Experience: {experience_id}"])
    return {"ok": True}


def health() -> dict:
    """健康状态。"""
    entities = _load_entities()
    relations = _load_relations()
    return {
        "available": True,
        "proc_alive": True,
        "store_type": "jsonl",
        "data_file": _DATA_FILE,
        "entity_count": len(entities),
        "relation_count": len(relations),
        "respawn_count": 0,
        "last_call_ms": None,
        "last_error": None,
    }


# ---------- 统计 ----------

_call_counts: dict[str, int] = {}
_call_errors: dict[str, str] = {}


def record_call(tool: str, outcome: str):
    _call_counts[tool] = _call_counts.get(tool, 0) + 1
    if outcome != "ok":
        _call_errors[tool] = outcome


def get_metrics() -> dict:
    return {
        "calls_5m": dict(_call_counts),
        "last_error": dict(_call_errors),
    }


def reset_metrics():
    _call_counts.clear()
    _call_errors.clear()
