import subprocess
import json
from typing import List, Optional, Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Idea
from mio_taskhub import mio_runtime
from mio_taskhub.ideas.idea_prompts import DEFAULT_TEMPLATES, get_template_by_id, get_templates_by_category

router = APIRouter(prefix="/ideas", tags=["ideas"])


class TemplateResponse(BaseModel):
    id: str
    name: str
    description: str
    category: str
    icon: str
    fields: List[dict]
    tags: List[str]


class TemplateGenerateRequest(BaseModel):
    template_id: str
    values: Dict[str, str]
    num_ideas: int = 3
    sync_to_hub: bool = False


def _generate_ideas(goal: str, context: str, timeout: float = 120.0) -> list:
    """经 mio CLI 调 creativity.generate（用户显式触发的 LLM 调用）。

    runtime 已发布版本没有 mio.idea.generate 工具（0.13.3 tools/list 实测），
    故映射到 creativity 语义；MCP tools/call 对长任务回空包（复现两次），
    走 CLI 直调。失败一律 HTTPException（502/503/504），不抛裸异常。
    """
    cli = mio_runtime.mio_cli()
    if not cli:
        raise HTTPException(503, "mio CLI 不可用：无法生成（runtime 未安装或 MIO_CLI 无效）")
    constraints = "约束：不引入外部依赖；保持本机单用户；复用现有 MCP 工具"
    args = cli + ["--json", "creativity", "generate",
                  "--source", f"template-goal: {goal}",
                  "--source", f"template-context: {context}\n{constraints}"]
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"creativity generate 超时（{int(timeout)}s）")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[:400]
        raise HTTPException(502, f"creativity generate failed: {tail}")
    try:
        data = json.loads(proc.stdout or "{}")
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "creativity generate 输出非 JSON")
    ideas = data.get("ideas") if isinstance(data, dict) else None
    if not isinstance(ideas, list):
        raise HTTPException(502, "creativity generate 输出缺 ideas 字段")
    return ideas


def _map_hypothesis(h: dict) -> dict:
    """creativity 假设 → 对外 idea 结构（title / description / provenance.strategy）。"""
    parts = [str(h.get("idea") or h.get("description") or "").strip()]
    if h.get("expectedBenefit"):
        parts.append(f"预期收益：{h['expectedBenefit']}")
    if h.get("risk"):
        parts.append(f"风险：{h['risk']}")
    nfi = "/".join(str(h[k]) for k in ("novelty", "feasibility", "impact") if k in h)
    if nfi:
        parts.append(f"N/F/I：{nfi}")
    out = {"title": str(h.get("title") or "").strip()[:200],
           "description": "\n\n".join(p for p in parts if p),
           "provenance": {"strategy": h.get("strategy") or ""}}
    if isinstance(h.get("novelty"), (int, float)):
        out["scores"] = {"novelty": h.get("novelty"),
                         "feasibility": h.get("feasibility"),
                         "impact": h.get("impact")}
    return out


@router.get("/templates")
def list_templates(category: Optional[str] = None):
    """List available idea templates."""
    templates = get_templates_by_category(category) if category else DEFAULT_TEMPLATES
    return {
        "count": len(templates),
        "templates": [
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "category": t.category,
                "icon": t.icon,
                "fields": t.fields,
                "tags": t.tags,
            }
            for t in templates
        ],
    }


@router.get("/templates/{template_id}")
def get_template(template_id: str):
    """Get a specific template by ID."""
    template = get_template_by_id(template_id)
    if not template:
        raise HTTPException(404, f"Template not found: {template_id}")

    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "category": template.category,
        "icon": template.icon,
        "fields": template.fields,
        "tags": template.tags,
    }


@router.post("/templates/generate")
def generate_from_template(request: TemplateGenerateRequest):
    """Generate ideas using a template with provided values."""
    template = get_template_by_id(request.template_id)
    if not template:
        raise HTTPException(404, f"Template not found: {request.template_id}")

    # 可移植解析：mio CLI（env MIO_CLI → which），不再写死 node/脚本绝对路径。
    # 语义映射：runtime 已发布版本无 mio.idea.generate 工具，改走
    # `mio --json creativity generate --source ...`（用户显式触发的 LLM 调用）。
    context_parts = []
    for field in template.fields:
        key = field["key"]
        if key in request.values and request.values[key]:
            context_parts.append(f"{field['label']}：{request.values[key]}")
    context = "\n\n".join(context_parts)
    goal = request.values.get("title", "生成想法")
    if request.num_ideas and request.num_ideas > 1:
        goal = f"{goal}（请给出约 {request.num_ideas} 个不同方向）"

    raw_ideas = _generate_ideas(goal, context)
    ideas = [_map_hypothesis(h)
             for h in raw_ideas[: max(1, request.num_ideas)]]

    created = 0
    synced_ids = []
    if request.sync_to_hub:
        db = next(get_session())
        try:
            existing = db.exec(select(Idea.title)).all()
            existing_titles = {t[0].strip().lower() for t in existing}

            for idea in ideas:
                title = idea.get("title", "").strip()
                if not title or title.lower() in existing_titles:
                    continue
                strategy = idea.get("provenance", {}).get("strategy", "")
                new_idea = Idea(
                    title=title[:200],
                    description=idea.get("description", ""),
                    status="new",
                    labels=["mio-intelligence", "auto-generated"] + ([f"strategy:{strategy}"] if strategy else []),
                )
                db.add(new_idea)
                db.commit()
                db.refresh(new_idea)
                existing_titles.add(title.lower())
                synced_ids.append(new_idea.id)
                created += 1
        finally:
            db.close()

    return {
        "generated": len(ideas),
        "synced": created,
        "synced_ids": synced_ids,
        "ideas": ideas,
    }
