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
_FR_ID = re.compile(r"(FR-\d+)")
_REVIEW_FAIL = re.compile(r"不通过|拒绝|驳回|reject|failed|失败|blocked", re.I)
_ITEM_PREFIX = re.compile(r"(?m)^\s*(?:\d+[.)、]|[-*])\s+")
_BULLET_PREFIX = re.compile(r"^[-*]\s+")


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
        d = _draft("signal", content[start:end], "content", content, start, end,
                   title=None, url=payload.get("url"),
                   evidence_tier="A" if payload.get("url") else "C",
                   scope_key="")
        return [d] if d else []
    desc = str(payload.get("description") or "")
    start, end = _trimmed_span(desc, 0, len(desc))
    d = _draft("signal", desc[start:end], "description", desc, start, end,
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
        if not _FR_LINE.match(line):
            continue
        stripped = line.strip()
        bullet = _BULLET_PREFIX.match(stripped)
        if bullet:
            stripped = stripped[bullet.end():].strip()
        if len(stripped) < 8:
            continue
        start = line_start + line.find(stripped)
        d = _draft("decision", stripped, "text", text,
                   start, start + len(stripped),
                   title=_FR_ID.search(stripped).group(1),
                   scope_key=project, evidence_tier="B")
        if d:
            out.append(d)
        if len(out) >= 50:
            break
    return out


def extract_doc_review(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    text = str(payload.get("review_result") or "")
    start, end = _first_lines_span(text, max_lines=2, max_chars=300)
    quote = text[start:end]
    if len(quote) < 8:
        return []
    kind = "problem" if _REVIEW_FAIL.search(quote) else "decision"
    d = _draft(kind, quote, "review_result", text, start, end,
               title="评审结论", scope_key=_project(payload),
               evidence_tier="C")
    return [d] if d else []


def _split_items(text: str) -> List[str]:
    parts = _ITEM_PREFIX.split(text)
    if len(parts) <= 1:
        parts = re.split(r"[\n]+", text)
    return [p.strip() for p in parts if p and p.strip()]


def extract_discussion_closed(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    text = str(payload.get("conclusions") or "")
    project = _project(payload)
    topic = str(payload.get("topic") or "讨论结论")
    out: List[Draft] = []
    cursor = 0
    for item in _split_items(text):
        quote = item.strip()
        if len(quote) < 8:
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
            break
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


def extract_run_result(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    exit_code = payload.get("exit_code")
    if exit_code is not None and int(exit_code) != 0:
        return []
    text = str(payload.get("result") or "")
    project = _project(payload)
    out: List[Draft] = []
    cursor = 0
    for sent in _split_run_sentences(text):
        if not has_test_evidence(sent):
            continue
        quote = _truncate(sent, 240)
        if len(quote) < 8:
            continue
        idx = text.find(quote, cursor)
        if idx < 0:
            idx = text.find(quote)
        if idx < 0:
            continue
        cursor = idx + len(quote)
        d = _draft("result", quote, "result", text,
                   idx, idx + len(quote), title=None,
                   scope_key=project, evidence_tier="C")
        if d:
            out.append(d)
        if len(out) >= 5:
            break
    return out


def extract_run_failure(payload: Dict[str, Any], source: Any = None) -> List[Draft]:
    exit_code = payload.get("exit_code")
    if exit_code is not None and int(exit_code) == 0:
        return []
    text = str(payload.get("result") or "")
    start, end = _first_lines_span(text, max_lines=3, max_chars=400)
    quote = text[start:end]
    if len(quote) < 8:
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
