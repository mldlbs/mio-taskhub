# -*- coding: utf-8 -*-
"""P5（FR-34）：驾驶舱结构化字段「一键生成草稿」。

读 Mio 的 LLM 配置（`mio config llm` → ~/.mio-intelligence/config.json）
→ 组装 prompt → 调 OpenAI 兼容 /chat/completions → 规范化 8 字段草稿返回。

**纯生成端点：不写库、不产生 IdeaChange、无缓存。**
安全：apiKey 只用于请求头，不进日志 / 响应 / 异常文案。
"""
import json
import socket
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from mio_taskhub import mio_runtime
from mio_taskhub.db import get_session
from mio_taskhub.models import Idea


router = APIRouter(prefix="/ideas", tags=["ideas"])

# 草稿字段白名单（与前端「编辑」表单的 8 个字段一致）
DRAFT_FIELDS = ("goal", "success_metric", "constraints", "out_of_scope",
                "mvp_scope", "tags", "assumptions", "risks")
_STR_FIELDS = ("goal", "success_metric", "constraints", "out_of_scope", "mvp_scope")
_LEVELS = ("low", "medium", "high")
LLM_TIMEOUT = 30.0
_MAX_DESC = 2000

_SYSTEM_PROMPT = (
    "你是资深产品评审助手。基于用户给的想法信息，输出一份结构化字段草稿。"
    "只输出一个 JSON 对象，不要任何解释文字。JSON 键固定为："
    "goal, success_metric, constraints, out_of_scope, mvp_scope, tags, assumptions, risks。"
    "写作要求（全中文）：goal 用「给【谁】解决【什么问题】，因为【为什么现在】」句式；"
    "success_metric 用「【指标】从【现状】到【目标】，在【期限】内」句式；"
    "constraints 覆盖时间/预算/人手/合规；out_of_scope 明确本期不做什么；"
    "mvp_scope 一句话圈定最小可用交付边界；"
    "tags 为 2~4 个短标签（字符串数组）；"
    "assumptions 为 2~4 条对象数组，每条形如 {\"text\": \"...\"}；"
    "risks 为 2~4 条对象数组，每条形如 "
    "{\"text\": \"...\", \"level\": \"low|medium|high\", \"mitigation\": \"...\"}。"
    "信息不足时给出保守、合理、可修改的默认值，不要留空、不要编造具体数字以外的虚构事实。"
)


def _truncate(s, n: int = _MAX_DESC) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n] + "…（已截断）"


def _existing_fields(idea: Idea) -> dict:
    """已填字段作为「保持一致」的上下文（只取非空）。"""
    out = {}
    for f in _STR_FIELDS:
        v = getattr(idea, f, "") or ""
        if v:
            out[f] = v
    tags = idea.tags if isinstance(idea.tags, list) else []
    if tags:
        out["tags"] = tags
    return out


def _build_user_prompt(idea: Idea, fields: list) -> str:
    lines = [
        "想法标题：%s" % (idea.title or "(无)"),
        "想法描述：%s" % (_truncate(getattr(idea, "description", "") or "(无)")),
    ]
    existing = _existing_fields(idea)
    if existing:
        lines.append("已有信息（须保持一致、不要矛盾）：%s"
                     % json.dumps(existing, ensure_ascii=False))
    lines.append("请生成本次需要的字段：%s" % ", ".join(fields))
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[dict]:
    """三级兜底：直接解析 → ```json 代码块 → 首尾花括号截取。"""
    text = str(text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001 —— 继续兜底
        pass
    if "```" in text:
        for part in text.split("```"):
            p = part.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                try:
                    data = json.loads(p)
                    if isinstance(data, dict):
                        return data
                except Exception:  # noqa: BLE001
                    continue
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        try:
            data = json.loads(text[i:j + 1])
            return data if isinstance(data, dict) else None
        except Exception:  # noqa: BLE001
            return None
    return None


def _normalize_draft(raw: dict, fields: list) -> dict:
    """白名单过滤 + 类型规范化（未知键丢弃，缺字段补空值）。"""
    draft = {}
    for f in fields:
        if f in _STR_FIELDS:
            draft[f] = str(raw.get(f) or "").strip()
        elif f == "tags":
            seen, tags = set(), []
            for t in (raw.get("tags") or []):
                t = str(t or "").strip()
                if t and t not in seen:
                    seen.add(t)
                    tags.append(t)
            draft["tags"] = tags
        elif f == "assumptions":
            rows = []
            for a in (raw.get("assumptions") or []):
                if isinstance(a, dict):
                    text = str(a.get("text") or a.get("title") or "").strip()
                else:
                    text = str(a or "").strip()
                if not text:
                    continue
                rows.append({"hid": "as-" + uuid.uuid4().hex[:8],
                             "text": text, "status": "open"})
            draft["assumptions"] = rows
        elif f == "risks":
            rows = []
            for r in (raw.get("risks") or []):
                if isinstance(r, dict):
                    text = str(r.get("text") or "").strip()
                    level = str(r.get("level") or "medium").strip().lower()
                    mit = str(r.get("mitigation") or "").strip()
                else:
                    text, level, mit = str(r or "").strip(), "medium", ""
                if not text:
                    continue
                if level not in _LEVELS:
                    level = "medium"
                rows.append({"text": text, "level": level, "mitigation": mit})
            draft["risks"] = rows
    return draft


def _call_llm(cfg: dict, messages: list, timeout: float = LLM_TIMEOUT):
    """调用 OpenAI 兼容端点（/chat/completions）。

    返回 (status, content)：status ∈ ok|timeout|unavailable|http_<code>；
    仅 ok 时 content 为上游 message.content。
    """
    payload = {
        "model": cfg.get("model") or "default",
        "messages": messages,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        cfg["apiUrl"],
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % cfg.get("apiKey", ""),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        # 不回显上游 body（可能含内部信息）；只报状态码
        return "http_%s" % e.code, None
    except (socket.timeout, TimeoutError):
        return "timeout", None
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            return "timeout", None
        return "unavailable", None
    except Exception:  # noqa: BLE001
        return "unavailable", None
    try:
        data = json.loads(body)
        choices = data.get("choices") or [{}]
        content = ((choices[0].get("message") or {}).get("content")) or ""
        return "ok", content
    except Exception:  # noqa: BLE001
        return "unavailable", None


@router.post("/{idea_id}/draft-fields")
def draft_idea_fields(idea_id: str, body: Optional[dict] = None,
                      db: Session = Depends(get_session)):
    """FR-34：LLM 生成驾驶舱 8 字段草稿（纯读端点，零写库）。"""
    i = db.get(Idea, idea_id)
    if not i:
        raise HTTPException(404, "idea not found")

    body = body if isinstance(body, dict) else {}
    fields = body.get("fields") or list(DRAFT_FIELDS)
    if (not isinstance(fields, list) or not fields
            or any((not isinstance(f, str)) or f not in DRAFT_FIELDS for f in fields)):
        raise HTTPException(422, "unknown fields: %s" % (fields,))

    cfg = mio_runtime.llm_config()
    if not (cfg.get("apiUrl") and cfg.get("apiKey")):
        raise HTTPException(503, "llm not configured")

    started = time.perf_counter()
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(i, fields)},
    ]
    status, content = _call_llm(cfg, messages)
    if status == "timeout":
        raise HTTPException(504, "llm timeout")
    if status.startswith("http_"):
        raise HTTPException(503, "llm http %s" % status[5:])
    if status != "ok":
        raise HTTPException(503, "llm unavailable")

    raw = _extract_json(content)
    if raw is None:
        raise HTTPException(502, "llm returned invalid json")

    return {
        "draft": _normalize_draft(raw, fields),
        "source": "llm",
        "model": cfg.get("model") or "",
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
