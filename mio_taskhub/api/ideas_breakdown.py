import re
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import (Idea, IdeaStatus, Task, TaskKind, TaskStage,
                                TaskState, IdeaChange, Discussion, DiscussionMessage)
from mio_taskhub.utils import _now
from mio_taskhub.dependency import normalize_depends, task_deps
from mio_taskhub.planner import detect_cycle
from mio_taskhub.events import emit_event
from mio_taskhub.api.ideas import _idea_json, transition_idea_status


router = APIRouter(prefix="/ideas", tags=["ideas"])


def _validate_refs(items):
    """校验 ref 唯一性。"""
    refs = [(it.get("ref") or "").strip() for it in items]
    non_empty = [r for r in refs if r]
    if len(set(non_empty)) != len(non_empty):
        raise HTTPException(422, "duplicate ref")


def _create_tasks_from_items(db, items, idea_id):
    """从 items 创建 Task 对象（未 flush）。"""
    created = []
    for it in items:
        title = (it.get("title") or "").strip()
        if not title:
            raise HTTPException(422, "task title is required")
        stage_val = it.get("stage", "brainstorming")
        try:
            stage = TaskStage(stage_val)
        except ValueError:
            raise HTTPException(400, f"invalid stage: {stage_val}")
        t = Task(
            title=title,
            description=it.get("description", ""),
            target_agent_type=it.get("target_agent_type"),
            priority=it.get("priority", 0),
            est_duration_min=it.get("est_duration_min", 30),
            max_retries=it.get("max_retries", 3),
            acceptance_criteria=it.get("acceptance_criteria", ""),
            depends_on=normalize_depends(it.get("depends_on")),
            idea_id=idea_id,
            stage=stage,
        )
        db.add(t)
        created.append(t)
    return created


def _resolve_dependencies(db, items, created):
    """解析 depends_on：ref → real id。"""
    ref2id = {it.get("ref"): t.id for it, t in zip(items, created) if it.get("ref")}
    for it, t in zip(items, created):
        resolved = []
        for dep in normalize_depends(it.get("depends_on")):
            real = ref2id.get(dep, dep)
            if real not in [x.id for x in created] and db.get(Task, real) is None:
                raise HTTPException(422, f"unknown dependency ref: {dep}")
            resolved.append(real)
        t.depends_on = resolved


def _check_cycles(created):
    """环检测。"""
    graph = {t.id: list(t.depends_on or []) for t in created}
    cyc = detect_cycle(graph)
    if cyc:
        raise HTTPException(422, f"cyclic dependency: {' → '.join(cyc)}")


def _emit_breakdown_events(db, idea_id, created):
    """发送 breakdown 事件。"""
    idea_event = emit_event(db, type="idea_broken_down", entity="idea", entity_id=idea_id,
                            payload={"action": "broken_down",
                                     "task_ids": [t.id for t in created]})
    task_events = [emit_event(db, type="task_created", entity="task", entity_id=t.id,
                              payload={"title": t.title, "stage": t.stage.value})
                   for t in created]
    return idea_event, task_events


@router.post("/{idea_id}/breakdown")
def breakdown_idea(idea_id: str, body: dict, db: Session = Depends(get_session)):
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")
    if i.status == IdeaStatus.BROKEN_DOWN:
        existing = db.exec(select(Task).where(Task.idea_id == idea_id)).all()
        if existing and not body.get("force"):
            raise HTTPException(409, f"already broken down ({len(existing)} tasks exist); pass force=true to add more")
    items = body.get("tasks", [])
    if not items:
        raise HTTPException(422, "tasks is required")
    _validate_refs(items)
    created = []
    try:
        created = _create_tasks_from_items(db, items, idea_id)
        db.flush()
        _resolve_dependencies(db, items, created)
        _check_cycles(created)
        if i.status != IdeaStatus.BROKEN_DOWN:
            transition_idea_status(i, IdeaStatus.BROKEN_DOWN, db, actor="user", source="breakdown")
        idea_event, task_events = _emit_breakdown_events(db, idea_id, created)
        db.add(i)
        db.commit()
        db.refresh(i)
    except HTTPException:
        db.rollback()
        raise
    return {
        "idea": _idea_json(db.get(Idea, idea_id)),
        "tasks": [{"id": t.id, "title": t.title, "ref": it.get("ref", ""),
                   "depends_on": task_deps(t)}
                  for it, t in zip(items, created)],
    }


@router.post("/{idea_id}/suggest-tasks")
def suggest_tasks(idea_id: str, body: dict = None, db: Session = Depends(get_session)):
    """从想法的描述、讨论结论、变更记录中自动提取任务草案。"""
    body = body or {}
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")

    max_tasks = min(body.get("max_tasks", 10), 20)
    context_hint = (body.get("context") or "").strip()

    desc, conclusions, changes = _collect_source_data(db, idea_id)
    blocks = _split_description(desc)
    conclusion_tasks = _extract_conclusion_tasks(conclusions)
    change_tasks = _extract_change_tasks(changes)
    suggestions = _generate_suggestions(blocks, conclusion_tasks, change_tasks, max_tasks)

    if not suggestions:
        return {
            "suggestions": [],
            "source_context": desc[:500] if desc else "",
            "message": "描述过于简短，无法自动拆解。建议先补充描述或开启讨论后再试。",
        }

    _link_dependencies(suggestions)
    source_context = _build_source_context(desc, conclusions, changes, blocks)

    return {
        "suggestions": suggestions,
        "source_context": source_context,
        "message": f"从 {len(blocks)} 个描述段落 + {len(conclusions)} 条讨论结论 + {len(changes)} 条变更记录中提取了 {len(suggestions)} 个任务草案",
    }


def _collect_source_data(db, idea_id):
    """收集描述、讨论结论、变更记录。"""
    i = db.get(Idea, idea_id)
    desc = (i.description or "").strip()
    discussions = db.exec(
        select(Discussion).where(Discussion.idea_id == idea_id)
    ).all()
    conclusions = [d.conclusions.strip() for d in discussions if d.conclusions]
    changes = db.exec(
        select(IdeaChange).where(IdeaChange.idea_id == idea_id).order_by(IdeaChange.created_at)
    ).all()
    return desc, conclusions, changes


def _split_description(text):
    """按空行或列表项拆分描述为独立段落。"""
    blocks = re.split(r'\n\s*\n|\n(?=-\s)', text)
    result = [b.strip() for b in blocks if len(b.strip()) > 8]
    return result or ([text] if text else [])


def _extract_conclusion_tasks(conclusions):
    """从讨论结论中提取补充任务。"""
    tasks = []
    for c in conclusions:
        for seg in re.split(r'[。；;]\s*', c):
            seg = seg.strip()
            if len(seg) > 8:
                tasks.append(seg)
    return tasks


def _extract_change_tasks(changes):
    """从变更记录中提取需求变化。"""
    tasks = []
    for ch in changes:
        if ch.diff:
            for field_name, change_desc in ch.diff.items():
                if isinstance(change_desc, str) and len(change_desc) > 5:
                    tasks.append(f"处理 {field_name} 变更：{change_desc}")
    return tasks


def _generate_suggestions(blocks, conclusion_tasks, change_tasks, max_tasks):
    """从所有来源合并生成草案。"""
    suggestions = []
    ref_counter = 0

    def _add(title, description, source, reasoning):
        nonlocal ref_counter
        if len(suggestions) >= max_tasks:
            return
        ref_counter += 1
        total_len = len(title) + len(description)
        est = 30 if total_len < 80 else (60 if total_len < 200 else 120)
        ac_lines = [
            line.strip().lstrip('- ')
            for line in description.split('\n')
            if any(kw in line for kw in ['需要', '必须', '验证', '确认', '检查', '验收', '应该'])
        ]
        suggestions.append({
            "ref": f"t{ref_counter}",
            "title": title,
            "description": description,
            "depends_on": [],
            "est_duration_min": est,
            "acceptance_criteria": "; ".join(ac_lines) if ac_lines else "",
            "reasoning": reasoning,
            "source": source,
        })

    for block in blocks:
        first_line = re.sub(r'^[#\-*>\s]+', '', block.split('\n')[0]).strip()
        if first_line and len(first_line) > 3:
            _add(first_line[:80], block, "description", "从描述段落提取")

    for ct in conclusion_tasks:
        title = re.sub(r'^[#\-*>\s]+', '', ct.split('\n')[0]).strip()[:80]
        _add(title, ct, "discussion", "从讨论结论提取")

    for ct in change_tasks:
        _add(ct[:80], ct, "change", "从变更记录提取")

    return suggestions


def _link_dependencies(suggestions):
    """线性依赖链：t1 → t2 → t3。"""
    for idx in range(1, len(suggestions)):
        suggestions[idx]["depends_on"] = [suggestions[idx - 1]["ref"]]


def _build_source_context(desc, conclusions, changes, blocks):
    """构建来源摘要。"""
    parts = []
    if desc:
        parts.append(f"描述: {desc[:200]}")
    if conclusions:
        parts.append(f"讨论结论({len(conclusions)}条): {'; '.join(c[:80] for c in conclusions[:3])}")
    if changes:
        parts.append(f"变更记录({len(changes)}条)")
    return "\n".join(parts)