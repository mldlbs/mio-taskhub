# -*- coding: utf-8 -*-
"""确定性提炼：raw payload → Draft[]（纯函数，无 IO / 无网络 / 无 LLM）。

Payload 约定（P3 适配器负责构造；P2 用黄金样本验证）：

| source | 字段 |
|---|---|
| hn | content, url?, project? |
| github-trending | content（或 repo + description）, url?, project? |
| doc.requirement | text, path?, project? |
| doc.review | review_result, task_id?, project? |
| discussion.closed | conclusions, topic?, project? |
| run.result | result, exit_code=0, run_id?, project? |
| run.failure | result, exit_code!=0, run_id?, project? |
| history.root_cause | root_cause, event_id?, project? |
| hub.console / observer.serve | block, file?, line?, signature?, occurrence_count?, project? |
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mio_taskhub.api.task_lessons import has_test_evidence, split_sentences
from mio_taskhub.materials.quality import Draft

_HN_SUFFIX = re.compile(r"\s*\(\d+\s+points?\s+by\s+[^)]*\)\s*$", re.I)
_FR_LINE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(FR-\d+)(?:\*\*)?\s*[：:]?\s*\S")
_FR_TABLE = re.compile(
    r"^\s*\|\s*(?:\*\*)?(FR-\d+)(?:\*\*)?\s*\|\s*(.+?)\s*\|"
)
_FR_ID = re.compile(r"(FR-\d+)")
_REVIEW_FAIL = re.compile(r"不通过|拒绝|驳回|reject|failed|失败|blocked", re.I)
_ITEM_PREFIX = re.compile(r"(?m)^\s*(?:\d+[.)、]|[-*])\s+")
_BULLET_PREFIX = re.compile(r"^[-*]\s+")
_LEAD_MARK = re.compile(
    r"^\s*(?:[①-⑩]|\d+[.)、]\s+|\d+[)）、]|[-*]\s+)\s*")
_TERM = re.compile(r"[。！？]|[.!?](?=\s|$)")
_ITEM = re.compile(r"[①-⑩]|(?<!\d)\d+[)）、]")
_DECISION_CLAIM = re.compile(
    r"是|为|必须|禁止|应|需|可|返回|新增|删除|保留|采用|通过|失败|错误|"
    r"支持|实现|走|落|改|加|减|校验|拦截|超时|发布|推出|宣布|加入|泄露|"
    r"起诉|裁员|收购|上线|修复|移除|打破|证实|发现|建议|要求|规定|决定|"
    r"确认|同意|拒绝|默认|优先|需求|规则|验收|完成|保证|确保|approved|passed|failed|"
    r"errors=|is|are|has|can", re.I)
_APPROVE = re.compile(
    r"验收通过|审查通过|批准通过|通过验收|Approve|Approved|LGTM|\bPASS\b", re.I)
_REJECT = re.compile(r"不通过|未通过|驳回|拒绝|Reject|Rejected|\bFAIL\b", re.I)
_REVIEW_RESULT = re.compile(
    r"\b\d+\s+(?:passed|failed|skipped|errors?)\b|"
    r"(?:passed|failed|skipped)\s*/\s*\d+|"
    r"(?:测试|用例|build|构建|pytest|npm|go test|全量)"
    r"[^\n]{0,12}(?:passed|failed|ok|成功|全绿|通过)", re.I)
_REVIEW_ROOT_CAUSE = re.compile(
    r"^(?:根因|原因|失败原因|问题原因|root\s*cause)|根因[:：]", re.I)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_OPINION_PREFIX = re.compile(
    r"^(Ask HN|What|Why|How|Is|Are|Can|Do|Does|Should|Tell HN|"
    r"Here Be Monsters|The case|Maybe|Opinion|Rethinking|Thoughts on|Redesigning|"
    r"Notes on|A practical guide|A tiny|A minimal|The hidden|Understanding)\b", re.I)
_STRONG_EVENT = re.compile(
    r"released|launch|announc|join|ban|leak|hack|breach|breaks|adds|fixes|"
    r"removes|remove|acquires|sues|resigns|passes|blocks|outage|vulnerab|"
    r"\bRCE\b|\bCVE\b|bankrupt|purges|escapes|keeps|found|lost|hitting|"
    r"debut|\bnew\b|earthquake|wildfire|storm|election|attack|crash|extort|"
    r"404|not found", re.I)
_VERB = re.compile(
    r"\b(is|are|was|were|has|have|can|cannot|does|doesn't|did|didn't|will|"
    r"just|keeps|escapes|purges|bankrupted|ported|makes|hitting|found|lost|"
    r"watching|bring|brings|bringing|introduc\w*|show\w*|build\w*|implement\w*|"
    r"support\w*|use\w*|mak\w*|giv\w*|get\w*|learn\w*|teach\w*|find\w*|"
    r"writ\w*|read\w*|play\w*|creat\w*|design\w*|test\w*|deploy\w*|"
    r"replac\w*|cut|cuts|add\w*|announc\w*|releas\w*|launch\w*|join\w*|"
    r"ban\w*|leak\w*|hack\w*|break\w*|fix\w*|remov\w*|acquir\w*|su\w*|"
    r"resign\w*|pass\w*|block\w*|limit\w*|rais\w*|reduc\w*|improv\w*)\b|"
    r"发布|推出|宣布|加入|禁止|泄露|起诉|裁员|收购|上线|修复|"
    r"移除|打破|证实|发现|建议", re.I)
_GENERIC_FAILURE = {
    "agent offline", "heartbeat timeout", "never started (no heartbeat)",
    "never started", "legacy 失败提交", "hermes failed/timed out",
    "build · big-pickle", "exit 3221226505",
}


def _clean(q: str) -> str:
    return _ANSI.sub("", q or "").strip()


def review_verdict(text: str) -> str:
    head = _clean(text)[:160]
    a, r = _APPROVE.search(head), _REJECT.search(head)
    if a and (not r or a.start() < r.start()):
        return "approved"
    if r:
        return "rejected"
    return "unknown"


def is_generic_failure(q: str) -> bool:
    norm = " ".join(_clean(q).split()).lower()
    if norm in _GENERIC_FAILURE:
        return True
    if re.fullmatch(r"exit \d+", norm):
        return True
    if re.match(r"^(node:internal|e:internal)\b", norm):
        return True
    return False


def has_citable_signal(q: str, source: str = "hn") -> bool:
    q = _clean(q)
    if not q:
        return False
    if q.startswith("Ask HN:"):
        return False
    if q.strip().endswith("?"):
        return False
    if source == "github-trending":
        # repo — description：描述本身是能力主张；长度由原子闸控制
        return " — " in q and len(q) >= 20
    if q.startswith("Show HN:") and len(q) >= 20:
        return True
    if len(q) < 30 and not _STRONG_EVENT.search(q):
        return False
    if _OPINION_PREFIX.search(q) and not _STRONG_EVENT.search(q):
        return False
    return bool(_STRONG_EVENT.search(q) or _VERB.search(q))


def _atomic_units(text: str, max_chars: int = 240) -> List[str]:
    """把长段切成可逐字命中的原子单元；单句仍超限则丢弃该句。"""
    text = text or ""
    units: List[str] = []
    for sent in split_sentences(text):
        sent = _LEAD_MARK.sub("", sent.strip()).strip()
        if not sent:
            continue
        if len(sent) <= max_chars and len(_TERM.findall(sent)) <= 2 \
                and len(_ITEM.findall(sent)) <= 2:
            units.append(sent)
            continue
        if len(sent) > max_chars:
            for part in re.split(r"[，,；;]", sent):
                part = _LEAD_MARK.sub("", part.strip()).strip()
                if 8 <= len(part) <= max_chars:
                    units.append(part)
    return units


def _project(payload: Dict[str, Any]) -> str:
    return str(payload.get("project") or "").strip()


def _trimmed_span(text: str, start: int, end: int) -> Tuple[int, int]:
    start = max(0, start)
    end = min(len(text), end)
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _first_lines_span(text: str, max_lines: int = 3,
                      max_chars: int = 400) -> Tuple[int, int]:
    if not text:
        return 0, 0
    start = len(text) - len(text.lstrip())
    pos = start
    lines = 0
    for line in text[start:].splitlines(keepends=True):
        if not line.strip() and lines == 0:
            pos += len(line)
            continue
        pos += len(line)
        if line.strip():
            lines += 1
        if lines >= max_lines:
            break
    end = min(pos, start + max_chars)
    return _trimmed_span(text, start, end)


def _truncate(text: str, max_chars: int) -> str:
    s = (text or "").strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip()


def _draft(kind: str, quote: str, field: str, text: str,
           start: Optional[int] = None, end: Optional[int] = None,
           **kwargs: Any) -> Optional[Draft]:
    quote = (quote or "").strip()
    if len(quote) < 8:
        return None
    if start is None or end is None:
        idx = text.find(quote)
        if idx < 0:
            return None
        start, end = idx, idx + len(quote)
    start, end = _trimmed_span(text, start, end)
    if text[start:end] != quote:
        return None
    return Draft(kind=kind, quote=quote, field=field,
                 char_start=start, char_end=end, **kwargs)


def _lines_with_offsets(text: str) -> Iterable[Tuple[int, str]]:
    pos = 0
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        yield pos, raw
        pos += len(line)


def extract_hn(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    content = str(payload.get("content") or "")
    title = _HN_SUFFIX.sub("", content).strip()
    if not title:
        return []
    start = content.find(title)
    if start < 0:
        return []
    d = _draft(
        "signal", title, "content", content, start, start + len(title),
        title=title, url=payload.get("url"),
        evidence_tier="A" if payload.get("url") else "C",
        scope_key="",
    )
    return [d] if d else []


def extract_github_trending(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    content = str(payload.get("content") or "")
    if content.strip():
        start, end = _trimmed_span(content, 0, len(content))
        quote = content[start:end]
        d = _draft("signal", quote, "content", content, start, end,
                   title=None, url=payload.get("url"),
                   evidence_tier="A" if payload.get("url") else "C",
                   scope_key="")
        return [d] if d else []
    desc = str(payload.get("description") or "")
    start, end = _trimmed_span(desc, 0, len(desc))
    quote = desc[start:end]
    d = _draft("signal", quote, "description", desc, start, end,
               title=str(payload.get("repo") or None),
               url=payload.get("url"),
               evidence_tier="A" if payload.get("url") else "C",
               scope_key="")
    return [d] if d else []


def extract_doc_requirement(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    text = str(payload.get("text") or "")
    project = _project(payload)
    out: List[Draft] = []
    for line_start, line in _lines_with_offsets(text):
        table = _FR_TABLE.match(line)
        if table:
            quote = table.group(2).strip()
            start = line_start + table.start(2)
            end = start + len(quote)
        elif _FR_LINE.match(line):
            stripped = line.strip()
            bullet = _BULLET_PREFIX.match(stripped)
            if bullet:
                stripped = stripped[bullet.end():].strip()
            quote = stripped
            start = line_start + line.find(stripped)
            end = start + len(stripped)
        else:
            continue
        if len(quote) < 8:
            continue
        d = _draft("decision", quote, "text", text,
                   start, end,
                   title=_FR_ID.search(quote).group(1) if _FR_ID.search(quote) else None,
                   scope_key=project, evidence_tier="B")
        if d:
            out.append(d)
        if len(out) >= 50:
            break
    return out


def _review_kind(quote: str, verdict: str) -> str:
    """doc.review 单条归属：驳回/根因→problem，测试/构建结果→result，其余→decision。"""
    if verdict == "rejected":
        return "problem"
    if _REVIEW_ROOT_CAUSE.search(quote):
        return "problem"
    if _REVIEW_RESULT.search(quote) and not _APPROVE.search(quote):
        return "result"
    return "decision"


def extract_doc_review(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    text = str(payload.get("review_result") or "")
    if not text.strip():
        return []
    verdict = review_verdict(text)
    parts = re.split(r"(?=[①-⑩])", text)
    if len(parts) <= 1:
        parts = _split_items(text)
    project = _project(payload)
    out: List[Draft] = []
    cursor = 0
    for part in parts:
        for unit in _atomic_units(part):
            unit = unit.strip()
            if len(unit) < 10 or not _DECISION_CLAIM.search(unit):
                continue
            idx = text.find(unit, cursor)
            if idx < 0:
                idx = text.find(unit)
            if idx < 0:
                continue
            cursor = idx + len(unit)
            d = _draft(_review_kind(unit, verdict), unit, "review_result", text,
                       idx, idx + len(unit),
                       title="评审结论", scope_key=project, evidence_tier="C")
            if d:
                out.append(d)
            if len(out) >= 20:
                return out
    return out


def _split_items(text: str) -> List[str]:
    if re.search(r"[①-⑩]", text):
        parts = re.split(r"(?=[①-⑩])", text)
    else:
        parts = _ITEM_PREFIX.split(text)
    if len(parts) <= 1:
        parts = re.split(r"[\n]+", text)
    out = []
    for part in parts:
        cleaned = _LEAD_MARK.sub("", part.strip()).strip()
        if cleaned:
            out.append(cleaned)
    return out


def extract_discussion_closed(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    text = str(payload.get("conclusions") or "")
    project = _project(payload)
    topic = str(payload.get("topic") or "讨论结论")
    out: List[Draft] = []
    cursor = 0
    for item in _split_items(text):
        for unit in _atomic_units(item):
            quote = unit.strip()
            if len(quote) < 10 or not _DECISION_CLAIM.search(quote):
                continue
            idx = text.find(quote, cursor)
            if idx < 0:
                idx = text.find(quote)
            if idx < 0:
                continue
            cursor = idx + len(quote)
            d = _draft("decision", quote, "conclusions", text,
                       idx, idx + len(quote), title=topic,
                       scope_key=project, evidence_tier="C")
            if d:
                out.append(d)
            if len(out) >= 20:
                return out
    return out


def _split_run_sentences(text: str) -> List[str]:
    out: List[str] = []
    for chunk in split_sentences(text):
        # 保护省略号/路径中的 ...，避免被英文句点切碎
        protected = chunk.replace("...", "\u2026")
        parts = re.split(r"(?<=[.!?])\s+", protected)
        out.extend(
            p.replace("\u2026", "...").strip()
            for p in parts if p.strip()
        )
    return out


def _run_result_kind(q: str) -> str:
    q = _clean(q)
    if q.startswith("注：") or "易踩坑" in q:
        return "lesson"
    if q.startswith(("git_sync.py 是", "现行", "task 表", "运行进程 PID", "测试跑在")):
        return "fact"
    if q.startswith("【历史") and "建议" in q:
        return "decision"
    if re.match(r"^(✗|Read .* failed|Grep .* failed|ERROR|Traceback|npm ERR)", q, re.I):
        return "problem"
    return "result"


def extract_run_result(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    exit_code = payload.get("exit_code")
    if exit_code is not None and int(exit_code) != 0:
        return []
    text = str(payload.get("result") or "")
    project = _project(payload)
    out: List[Draft] = []
    cursor = 0
    for sent in _split_run_sentences(text):
        for unit in _atomic_units(sent, 240):
            if not has_test_evidence(unit):
                continue
            quote = unit.strip()
            if len(quote) < 8:
                continue
            idx = text.find(quote, cursor)
            if idx < 0:
                idx = text.find(quote)
            if idx < 0:
                continue
            cursor = idx + len(quote)
            d = _draft(_run_result_kind(quote), quote, "result", text,
                       idx, idx + len(quote), title=None,
                       scope_key=project, evidence_tier="C")
            if d:
                out.append(d)
            if len(out) >= 5:
                return out
    return out


def extract_run_failure(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    exit_code = payload.get("exit_code")
    if exit_code is not None and int(exit_code) == 0:
        return []
    text = str(payload.get("result") or "")
    start, end = _first_lines_span(text, max_lines=3, max_chars=100000)
    quote = text[start:end]
    if len(quote) < 8 or len(quote) > 400:
        return []
    if is_generic_failure(quote):
        return []
    run_id = str(payload.get("run_id") or "").strip()
    title = f"run {run_id}" if run_id else "失败原因"
    d = _draft("problem", quote, "result", text, start, end,
               title=title, scope_key=_project(payload), evidence_tier="C")
    return [d] if d else []


def extract_history_root_cause(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    text = str(payload.get("root_cause") or "")
    quote = _truncate(text, 400)
    if len(quote) < 8:
        return []
    idx = text.find(quote)
    if idx < 0:
        return []
    d = _draft("problem", quote, "root_cause", text,
               idx, idx + len(quote), title="根因",
               scope_key=_project(payload), evidence_tier="C")
    return [d] if d else []


def extract_log_block(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    text = str(payload.get("block") or payload.get("content") or "")
    start, end = _first_lines_span(text, max_lines=4, max_chars=400)
    quote = text[start:end]
    if len(quote) < 8:
        return []
    occurrence = payload.get("occurrence_count") or 1
    try:
        occurrence = max(1, int(occurrence))
    except Exception:
        occurrence = 1
    file = payload.get("file")
    line = payload.get("line")
    source_ref = {"path": str(file), "line": line} if file else None
    d = _draft("problem", quote, "block" if payload.get("block") else "content",
               text, start, end, title="日志错误",
               scope_key=_project(payload),
               evidence_tier="B" if file else "C",
               occurrence_count=occurrence, source_ref=source_ref)
    return [d] if d else []


EXTRACTORS = {
    "hn": extract_hn,
    "github-trending": extract_github_trending,
    "doc.requirement": extract_doc_requirement,
    "doc.review": extract_doc_review,
    "discussion.closed": extract_discussion_closed,
    "run.result": extract_run_result,
    "run.failure": extract_run_failure,
    "history.root_cause": extract_history_root_cause,
    "hub.console": extract_log_block,
    "observer.serve": extract_log_block,
}


def extract_for_source(source_key: str, payload: Dict[str, Any],
                       source: Any = None) -> List[Draft]:
    fn = EXTRACTORS.get(source_key)
    if fn is None:
        raise KeyError(f"no extractor for source: {source_key}")
    return fn(payload, source)
