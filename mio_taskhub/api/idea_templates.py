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


def _fetch_observer_insights(limit: int = 2, timeout: float = 15.0) -> list:
    """拉取 mio observer 近期洞察作为创意原料（观察→趋势→洞察链的产出）。

    fail-open：CLI 不可用/超时/解析失败一律返回 []，不阻断生成。
    """
    cli = mio_runtime.mio_cli()
    if not cli:
        return []
    try:
        proc = subprocess.run(cli + ["--json", "observer", "insights"],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        if proc.returncode != 0:
            return []
        data = json.loads(proc.stdout or "[]")
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    out = []
    for ins in data[:limit]:
        if not isinstance(ins, dict):
            continue
        topic = str(ins.get("topic") or ins.get("id") or "unknown")
        bits = [f"主题：{topic}"]
        for sec in (ins.get("sections") or [])[:4]:
            title = sec.get("title")
            content = str(sec.get("content") or "").strip()[:400]
            if title and content:
                bits.append(f"{title}: {content}")
        content = "\n".join(bits)
        if len(content) > 1200:
            content = content[:1200] + "…"
        out.append({"name": f"observer-insight:{topic}", "content": content})
    return out


def _generate_ideas(goal: str, context: str, timeout: float = 120.0) -> list:
    """经 mio CLI 调 creativity.generate（用户显式触发的 LLM 调用）。

    runtime 已发布版本没有 mio.idea.generate 工具（0.13.3 tools/list 实测），
    故映射到 creativity 语义；MCP tools/call 对长任务回空包（复现两次），
    走 CLI 直调。失败一律 HTTPException（502/503/504），不抛裸异常。
    额外注入 observer 近期洞察作为第 3+ 个 source（fail-open），
    让观察→洞察链的产出进入创意环节。
    """
    cli = mio_runtime.mio_cli()
    if not cli:
        raise HTTPException(503, "mio CLI 不可用：无法生成（runtime 未安装或 MIO_CLI 无效）")
    constraints = "约束：不引入外部依赖；保持本机单用户；复用现有 MCP 工具"
    sources = ["--source", f"template-goal: {goal}",
               "--source", f"template-context: {context}\n{constraints}"]
    for extra in _fetch_observer_insights():
        sources += ["--source", f"{extra['name']}: {extra['content']}"]
    args = cli + ["--json", "creativity", "generate", *sources]
    reason = ""
    for attempt in range(2):
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
        if ideas:
            return ideas
        # 空产出：上游 creativity 在 LLM 失败/空内容时静默返回空
        # （llm-client chatJson 永不抛错、引擎只判 result.data）——重试一次；
        # 带 reason 的空（如 all pairs already explored）是确定性结果，不重试。
        reason = str((data.get("reason") or "")).strip() if isinstance(data, dict) else ""
        if reason:
            break
    if reason:
        raise HTTPException(409, f"creativity 未生成：{reason}")
    raise HTTPException(502, "creativity 连续两次返回空（上游静默吞掉 LLM 错误/空内容），稍后重试")


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
    # 中文门禁：上游 creativity system 为英文（creativity-engine.js:228 无语言指令），
    # 语言要求只能随 sources 下发，goal/context 双带以提高遵从率。
    lang = "所有输出字段（title/idea/expectedBenefit/risk）必须用简体中文（专有名词与缩写除外）"
    goal = f"{goal}（{lang}）"
    context = f"{context}\n语言要求：{lang}"

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
