# -*- coding: utf-8 -*-
"""Read Evidence：把「agent 读过规定版本的文档」变成可校验事实，并作为 submit 前置门控。

设计（见 doc-consult-enforcement-plan.md）：
- `claim` 内联文档只是**预览**，不产生 evidence；必须显式调用
  `taskhub_read_document(..., run_id=...)` 才落 evidence。
- evidence **绑定 run_id**（本次执行），并记录文档**内容指纹**（sha256）；
  因此「读完之后文档又被人改了」也能识别（版本不匹配 → 需重读）。
- `submit_result` 成功路径前置校验：required_reads 全部有 evidence 且指纹一致，
  否则 422。这样 `git push --no-verify` 也绕不过服务端规则。

放行原则（不卡死开发）：
- 任务未登记这些 kind，或登记了但文件缺失 → 不纳入 required（不阻塞）；
- 环境变量 `MIO_READ_GATE=0/off` 可整体关闭门控；
- `MIO_READ_GATE_KINDS` 可改要求项，默认 `spec,api,requirement`。
"""
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from mio_taskhub.doc_paths import doc_path_of
from mio_taskhub.models import ReadEvidence
from mio_taskhub.utils import _now

DEFAULT_READ_KINDS = ("spec", "api", "requirement")
INLINE_CAP = 8000          # claim 内联正文上限（字符/kind）
_FR_RE = re.compile(r"FR-\d+", re.IGNORECASE)
_OFF = ("0", "off", "false", "no", "disabled")


def gate_enabled() -> bool:
    return (os.environ.get("MIO_READ_GATE") or "").strip().lower() not in _OFF


def gate_kinds() -> List[str]:
    raw = (os.environ.get("MIO_READ_GATE_KINDS") or "").strip()
    if not raw:
        return list(DEFAULT_READ_KINDS)
    return [k.strip() for k in raw.split(",") if k.strip()]


# ── 文档定位 / 指纹 / 抽取 ──────────────────────────────────────────────────

def resolve_doc_path(task: Any, kind: str) -> Optional[Path]:
    """按 kind 解析文档的绝对路径（相对路径以 task.workspace 为基准）。"""
    rel = doc_path_of(task, kind)
    if not rel:
        return None
    p = Path(rel)
    if not p.is_absolute():
        ws = (getattr(task, "workspace", "") or "").strip()
        if not ws:
            return None
        p = Path(ws) / p
    try:
        return p.resolve()
    except OSError:
        return None


def file_fingerprint(path: Optional[Path]) -> str:
    """文件内容指纹（sha256），文件不存在 / 不可读 → ""。"""
    if path is None or not path.is_file():
        return ""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return "sha256:" + h.hexdigest()


def read_doc_text(task: Any, kind: str, cap: Optional[int] = None):
    """返回 (text, truncated, path)。文件缺失 → ("", False, path)。"""
    path = resolve_doc_path(task, kind)
    if path is None or not path.is_file():
        return "", False, path
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "", False, path
    truncated = False
    if cap is not None and len(text) > cap:
        text, truncated = text[:cap], True
    return text, truncated, path


def extract_fr_ids(text: str) -> List[str]:
    """抽取 FR-n 编号（去重、按序号排序）。"""
    if not text:
        return []
    found = {m.group(0).upper() for m in _FR_RE.finditer(text)}

    def _num(x):
        try:
            return int(x.split("-")[1])
        except (IndexError, ValueError):
            return 0

    return sorted(found, key=_num)


def doc_status(task: Any, kind: str) -> Optional[dict]:
    return (getattr(task, "doc_statuses", None) or {}).get(kind)


# ── required / evidence ────────────────────────────────────────────────────

def required_read_kinds(task: Any) -> List[str]:
    """门控要求的 kind ∩ 任务已登记的 kind（按 gate_kinds 顺序）。"""
    return [k for k in gate_kinds() if doc_path_of(task, k)]


def latest_evidence(db: Session, run_id: str, kind: str) -> Optional[ReadEvidence]:
    return db.exec(
        select(ReadEvidence)
        .where(ReadEvidence.run_id == run_id, ReadEvidence.document_kind == kind)
        .order_by(ReadEvidence.read_at.desc())
    ).first()


def record_read(db: Session, task: Any, kind: str, run_id: str,
                agent_name: str = "") -> Dict[str, Any]:
    """记录一次阅读 evidence（按 (run_id, kind) upsert）。返回 evidence 摘要。"""
    path = resolve_doc_path(task, kind)
    fp = file_fingerprint(path)
    text = ""
    if path is not None and path.is_file():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
    frs = extract_fr_ids(text) if kind == "requirement" else []
    now = _now()
    ev = latest_evidence(db, run_id, kind)
    if ev is None:
        ev = ReadEvidence(task_id=task.id, run_id=run_id, agent_name=agent_name or "",
                          document_kind=kind, document_version=fp,
                          requirement_ids=frs, read_at=now)
    else:
        ev.task_id = task.id
        if agent_name:
            ev.agent_name = agent_name
        ev.document_version = fp
        ev.requirement_ids = frs
        ev.read_at = now
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return {"kind": kind, "run_id": run_id, "task_id": task.id,
            "document_version": fp, "requirement_ids": frs,
            "recorded_at": ev.read_at.isoformat()}


def check_read_gate(db: Session, task: Any, run_id: str) -> Dict[str, Any]:
    """校验本次 run 是否满足 required reads（存在 + 版本一致）。

    文件缺失的 kind 不纳入强制（无法阅读，放行）。
    """
    required = required_read_kinds(task)
    ok: List[str] = []
    missing: List[str] = []
    stale: List[str] = []
    for kind in required:
        fp = file_fingerprint(resolve_doc_path(task, kind))
        if not fp:                       # 文件缺失 → 不强制
            continue
        ev = latest_evidence(db, run_id, kind)
        if ev is None:
            missing.append(kind)
        elif (ev.document_version or "") != fp:
            stale.append(kind)
        else:
            ok.append(kind)
    return {
        "run_id": run_id,
        "task_id": getattr(task, "id", ""),
        "required_reads": required,
        "read_ok": ok,
        "missing": missing,
        "stale": stale,
        "enforced": bool(required) and gate_enabled(),
        "passed": not missing and not stale,
        "gate_kinds": gate_kinds(),
    }


# ── claim 内联上下文 ───────────────────────────────────────────────────────

def build_claim_context(task: Any) -> Dict[str, Any]:
    """claim 成功时内联返回文档上下文（预览 + 必读项 + FR）。

    **内联 ≠ 已读**：不产生 evidence；agent 仍须 read_document(run_id=...) 才能过 submit 门控。
    """
    required = required_read_kinds(task)
    docs: Dict[str, Any] = {}
    required_fr: List[str] = []
    for kind in required:
        path = resolve_doc_path(task, kind)
        text, truncated, _ = read_doc_text(task, kind, cap=INLINE_CAP)
        docs[kind] = {
            "path": doc_path_of(task, kind),
            "status": doc_status(task, kind),
            "version": file_fingerprint(path),
            "content": text,
            "truncated": truncated,
        }
        if kind == "requirement":
            full, _, _ = read_doc_text(task, kind, cap=None)
            required_fr = extract_fr_ids(full)
    return {
        "branch": "task-%s" % getattr(task, "id", ""),
        "required_reads": required,
        "required_fr": required_fr,
        "documents": docs,
        "read_evidence_required": gate_enabled() and bool(required),
        "note": ("documents 仅为内联预览，不构成已读；动手前必须调用 "
                 "taskhub_read_document(task_id, kind, run_id=<本 run>) 产生 "
                 "ReadEvidence，否则 taskhub_submit_result 会被拦截。"),
    }
