import subprocess
import json
from typing import List, Optional, Dict

# Windows：隐藏子进程控制台窗口（观测台 insights / creativity generate 走 node 直调）
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
    recently_seen: list = []       # 最近已生成标题（避开重复角度；可从求体显式传入）
    auto_recently_seen: bool = True  # 为真时自动取 hub 最近 auto-generated 标题
    sources_limit: int = 5         # 观测台素材条数上限（扩大组合空间）
    cross_domain: bool = True      # 要求跨领域类比（重新打开已探索空间）


def _fetch_observer_insights(limit: int = 5, timeout: float = 15.0) -> list:
    """拉取 mio observer 近期洞察作为创意原料（观察→趋势→洞察链的产出）。

    fail-open：CLI 不可用/超时/解析失败一律返回 []，不阻断生成。
    """
    cli = mio_runtime.mio_cli()
    if not cli:
        return []
    try:
        proc = subprocess.run(cli + ["--json", "observer", "insights",
                                     "--base-dir", mio_runtime.observer_base_dir()],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              creationflags=_CREATE_NO_WINDOW)
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


def _generate_ideas(goal: str, context: str, timeout: float = 120.0,
                    recently_seen: list = None,
                    strategy: str = "",
                    sources_limit: int = 5,
                    cross_domain: bool = True) -> list:
    """经 mio CLI 调 creativity.generate（用户显式触发的 LLM 调用）。

    CLI 由 mio_runtime.mio_cli() 解析——cmd shim 自动转 node 直调
    （0.14.x 起 runtime 有 mio.idea.generate MCP 工具，但 MCP tools/call
    对长任务回空包（复现两次），仍走 CLI 直调）。失败一律
    HTTPException（502/503/504），不抛裸异常；LLM 逐 pair 失败显式透出。
    额外注入 observer 近期洞察作为第 3+ 个 source（fail-open），
    让观察→洞察链的产出进入创意环节。

    recently_seen：最近已生成过的标题，拼进 source 要求「避开这些角度」，
    缓解 all pairs already explored（素材重复导致的确定性空产出）。
    strategy：显式指定生成策略（explore/signal/stable），供 409 后换角度重试。
    """
    cli = mio_runtime.mio_cli()
    if not cli:
        raise HTTPException(503, "mio CLI 不可用：无法生成（runtime 未安装或 MIO_CLI 无效）")
    constraints = "约束：不引入外部依赖；保持本机单用户；复用现有 MCP 工具"
    sources = ["--source", f"template-goal: {goal}",
               "--source", f"template-context: {context}\n{constraints}"]
    seen = [str(s).strip() for s in (recently_seen or []) if str(s).strip()]
    if seen:
        sources += ["--source",
                    "already-explored（本批必须避开这些既有角度，换新切入点）: "
                    + " | ".join(seen[:20])]
    if cross_domain:
        sources += ["--source",
                    "跨领域类比要求：请把不同来源/不同领域的素材显式交叉配对"
                    "（例如把 A 领域的问题结构映射到 B 领域的解法），"
                    "产出至少一个跨域迁移的新假设，而不是同域内的微调。"]
    for extra in _fetch_observer_insights(limit=max(2, sources_limit)):
        sources += ["--source", f"{extra['name']}: {extra['content']}"]
    args = cli + ["--json", "creativity", "generate", *sources]
    if strategy:
        args += ["--strategy", strategy]
    reason = ""
    for attempt in range(2):
        try:
            proc = subprocess.run(args, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout,
                                  creationflags=_CREATE_NO_WINDOW)
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
        # 引擎的 errors（逐 pair 的 LLM 失败）必须显式透出：LLM 402/超时
        # 不能被伪装成「素材重复」或纯空产出（用户边界 2，2026-10-08）。
        errors = data.get("errors") if isinstance(data, dict) else None
        if isinstance(errors, list) and errors:
            brief = "; ".join(
                f"{e.get('pair')}: {e.get('error')}" if isinstance(e, dict) else str(e)
                for e in errors[:3])
            raise HTTPException(502, f"creativity LLM 调用失败（{len(errors)} 个配对）：{brief}")
        if reason:
            break
    if reason:
        raise HTTPException(409, f"creativity 未生成：{reason}")
    raise HTTPException(502, "creativity 连续两次返回空（上游静默吞掉 LLM 错误/空内容），稍后重试")


# 409/all pairs 后的降级策略序（换角度重试；explore 默认，故从 signal 起）
_DEGRADE_STRATEGIES = ("signal", "stable")


def _generate_with_degrade(goal: str, context: str, timeout: float = 120.0,
                           recently_seen: list = None,
                           sources_limit: int = 5,
                           cross_domain: bool = True) -> tuple:
    """先生成；遇 409（素材重复/无可新增组合）则换 strategy 重试，返回 (ideas, strategy_used)。

    仍失败则抛出最后一次的 409（message 里带已尝试策略），不再假绿。
    """
    try:
        return _generate_ideas(goal, context, timeout=timeout,
                               recently_seen=recently_seen,
                               sources_limit=sources_limit,
                               cross_domain=cross_domain), ""
    except HTTPException as e:
        if e.status_code != 409:
            raise
        tried = []
        for strat in _DEGRADE_STRATEGIES:
            tried.append(strat)
            try:
                return _generate_ideas(goal, context, timeout=timeout,
                                       recently_seen=recently_seen, strategy=strat,
                                       sources_limit=sources_limit,
                                       cross_domain=cross_domain), strat
            except HTTPException as e2:
                if e2.status_code != 409:
                    raise
        raise HTTPException(409, f"creativity 未生成（已尝试 explore/{'/'.join(tried)}）：素材重复，"
                                 f"建议等观测台产出新素材后重试")


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


def _recent_generated_titles(request, limit: int = 20) -> list:
    """最近已生成的想法标题（默认取 hub 里 auto-generated 的最近 N 条），
    用于让 LLM 避开既有角度、缓解 all pairs already explored。fail-open。"""
    seen = [str(s).strip() for s in (getattr(request, "recently_seen", None) or []) if str(s).strip()]
    if seen or not getattr(request, "auto_recently_seen", True):
        return seen[:limit]
    try:
        db = next(get_session())
        try:
            rows = db.exec(select(Idea).order_by(Idea.created_at.desc()).limit(60)).all()
            for i in rows:
                if "auto-generated" in (i.labels or []) and (i.title or "").strip():
                    seen.append(i.title.strip())
                if len(seen) >= limit:
                    break
        finally:
            db.close()
    except Exception:  # noqa: BLE001 —— 取不到就不带（fail-open）
        pass
    return seen[:limit]


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

    raw_ideas, used_strategy = _generate_with_degrade(
        goal, context,
        recently_seen=_recent_generated_titles(request),
        sources_limit=int(getattr(request, "sources_limit", 5) or 5),
        cross_domain=bool(getattr(request, "cross_domain", True)),
    )
    ideas = [_map_hypothesis(h)
             for h in raw_ideas[: max(1, request.num_ideas)]]
    if used_strategy:
        for it in ideas:
            it.setdefault("provenance", {})["strategy"] = used_strategy

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
                    # 自动生成的先进收集箱（初筛后晋升 NEW），与 INBOX 迁移设计一致
                    status="inbox",
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
