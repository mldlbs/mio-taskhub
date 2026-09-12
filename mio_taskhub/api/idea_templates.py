import os
import subprocess
import json
from typing import List, Optional, Dict
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select
from mio_taskhub.db import get_session
from mio_taskhub.models import Idea
from mio_taskhub.ideas.idea_prompts import DEFAULT_TEMPLATES, get_template_by_id, get_templates_by_category, render_template_prompt

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

    NODE = r"C:\Users\admin\.workbuddy\binaries\node\versions\22.22.2\node.exe"
    MCP_SCRIPT = r"D:\node_global\node_modules\mio-agent-runtime\server\mio-intelligence-mcp\index.js"
    DATA_DIR = os.path.join(os.path.expanduser("~"), ".mio-intelligence")

    ENV = {
        **os.environ,
        "MIO_DATA_DIR": DATA_DIR,
        "MIO_CONTEXT": json.dumps({
            "agentId": "opencode",
            "project": "2026-08-22-12-13-49",
            "workspace": r"c:\Users\admin\WorkBuddy\2026-08-22-12-13-49",
            "sessionId": "opencode-session",
        }),
    }

    def call_mcp_tool(tool_name: str, arguments: dict) -> dict:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        proc = subprocess.run(
            [NODE, MCP_SCRIPT],
            input=json.dumps(request) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=ENV,
            timeout=15,
        )
        if proc.returncode != 0:
            raise HTTPException(500, f"MCP error: {proc.stderr[:500]}")
        lines = proc.stdout.strip().split("\n")
        resp = json.loads(lines[0])
        text = resp["result"]["content"][0]["text"]
        return json.loads(text, strict=False)

    context = render_template_prompt(template=DEFAULT_TEMPLATES[0], values=request.values)
    context_parts = []
    for field in get_template_by_id(request.template_id).fields:
        key = field["key"]
        if key in request.values and request.values[key]:
            label = field["label"]
            context_parts.append(f"{label}：{request.values[key]}")
    context = "\n\n".join(context_parts)

    goal = request.values.get("title", "生成想法")

    try:
        result = call_mcp_tool("mio.idea.generate", {
            "goal": goal,
            "context": context,
            "constraints": ["不引入外部依赖", "保持本机单用户", "复用现有 MCP 工具"],
            "numIdeas": request.num_ideas,
        })
    except Exception as e:
        raise HTTPException(500, f"MCP generation failed: {e}")

    ideas = result.get("ideas", [])

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
