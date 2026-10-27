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
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlmodel import Session, select

from mio_taskhub.doc_paths import doc_path_of
from mio_taskhub.models import ReadEvidence
from mio_taskhub.utils import _now

logger = logging.getLogger("read_evidence")

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
    """按 gate_tier 计算要求的 kind（仅已登记项；env 显式覆盖优先）。"""
    if (os.environ.get("MIO_READ_GATE_KINDS") or "").strip():
        return [k for k in gate_kinds() if doc_path_of(task, k)]
    # 兼容直接构造、未经过 tier 推断的存量/测试任务（gate_tier_reason 为空）。
    if not str(getattr(task, "gate_tier_reason", "") or ""):
        return [k for k in gate_kinds() if doc_path_of(task, k)]
    from mio_taskhub.completion_gate import TIER_KINDS
    tier = str(getattr(task, "gate_tier", "light") or "light").lower()
    kinds = TIER_KINDS.get(tier, TIER_KINDS["light"])
    return [k for k in kinds if doc_path_of(task, k)]


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
    """校验本次 run 是否满足 required reads（存证 + 版本一致）。

    已登记但文件缺失：`MIO_READ_GATE_MISSING` 默认 block 记为 missing；warn 放行。
    """
    required = required_read_kinds(task)
    missing_mode = (os.environ.get("MIO_READ_GATE_MISSING") or "block").strip().lower()
    ok: List[str] = []
    missing: List[str] = []
    stale: List[str] = []
    missing_files: List[str] = []
    for kind in required:
        fp = file_fingerprint(resolve_doc_path(task, kind))
        if not fp:
            missing_files.append(kind)
            if missing_mode not in _OFF and missing_mode != "warn":
                missing.append(kind)
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
        "missing_files": missing_files,
        "stale": stale,
        "enforced": bool(required) and gate_enabled(),
        "passed": not missing and not stale,
        "gate_kinds": gate_kinds(),
    }


# ── claim 内联上下文 ───────────────────────────────────────────────────────

def _lessons_for(task: Any) -> Dict[str, Any]:
    """取同项目已完成任务的参考信息（任务 28f8fceb FR-2）。

    **只提供信息，不阻断任何流程**：内部开独立短会话，异常一律降级为空结构。
    放在这里而不是 claim 端点，是因为 claim 的两个响应构造点
    （api/tasks.py 的「已有 Run 复用」与「正常认领」）都调用 build_claim_context。
    """
    from mio_taskhub.api.task_lessons import empty_lessons, recent_lessons
    try:
        workspace = str(getattr(task, "workspace", "") or "").strip()
        project = str(getattr(task, "project", "") or "").strip()
        if not workspace and not project:
            # 无scope 依据时不查询，直接给空结构（避免全库扫描）
            return empty_lessons()
        from mio_taskhub.db import Session as _S, engine as _E
        with _S(_E) as s:
            return recent_lessons(s, workspace=workspace, project=project)
    except Exception:
        logger.warning("claim lessons 取值失败，降级为空", exc_info=True)
        return empty_lessons()


def _json_obj(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if not value:
        return None
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _package_doc_preview(task: Any, package: Dict[str, Any]) -> Dict[str, Any]:
    """P2c：只内联 context_package 指定章节，不返回父任务文档全文。"""
    from mio_taskhub.decomposition import read_context_sections
    docs: Dict[str, Any] = {}
    for idx, entry in enumerate(package.get("documents") or []):
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("kind") or f"doc{idx + 1}")
        key = kind
        if key in docs:
            key = f"{kind}#{idx + 1}"
        selected, path, error = read_context_sections(task, entry)
        content = selected or ""
        docs[key] = {
            "path": str(entry.get("path") or ""),
            "sections": list(entry.get("sections") or []),
            "version": file_fingerprint(path),
            "content": content[:INLINE_CAP],
            "truncated": len(content) > INLINE_CAP,
            "error": error or None,
        }
    return docs


def build_claim_context(task: Any, package_only: Optional[bool] = None) -> Dict[str, Any]:
    """claim 成功时内联返回文档上下文（预览 + 必读项 + FR + 同项目参考）。

    **内联 ≠ 已读**：不产生 evidence；agent 仍须 read_document(run_id=...) 才能过 submit 门控。

    P2c：任务有 context_package 且阶段到达 context 时，claim 只内联包内指定章节，
    不再把父任务文档全文塞进响应。
    """
    from mio_taskhub.hierarchy import STAGE_CONTEXT, stage_at_least

    package = _json_obj(getattr(task, "context_package", None))
    if package_only is None:
        package_only = bool(package) and stage_at_least(STAGE_CONTEXT)

    required = required_read_kinds(task)
    docs: Dict[str, Any] = {}
    required_fr: List[str] = []
    for kind in required:
        if kind == "requirement":
            full, _, _ = read_doc_text(task, kind, cap=None)
            required_fr = extract_fr_ids(full)

    if package_only and package:
        docs = _package_doc_preview(task, package)
        from mio_taskhub.decomposition import estimate_tokens
        stored = package.get("estimate") if isinstance(package.get("estimate"), dict) else {}
        recalculated = estimate_tokens(
            package, workspace=str(getattr(task, "workspace", "") or ""))
        estimate_drift = bool(stored) and stored.get("tokens") != recalculated.get("tokens")
    else:
        estimate_drift = None
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

    result = {
        "branch": "task-%s" % getattr(task, "id", ""),
        "required_reads": required,
        "required_fr": required_fr,
        "documents": docs,
        "read_evidence_required": gate_enabled() and bool(required),
        # 同项目参考（软信息，不阻断）
        "lessons": _lessons_for(task),
        "note": ("documents 仅为内联预览，不构成已读；动手前必须调用 "
                 "taskhub_read_document(task_id, kind, run_id=<本 run>) 产生 "
                 "ReadEvidence，否则 taskhub_submit_result 会被拦截。"),
    }
    try:
        from mio_taskhub.completion_gate import build_readiness
        result.update({
            "gate_tier": str(getattr(task, "gate_tier", "light") or "light"),
            "gate_tier_reason": str(getattr(task, "gate_tier_reason", "") or ""),
            "acceptance_criteria": str(getattr(task, "acceptance_criteria", "") or ""),
            "deliverables": list(getattr(task, "deliverables", None) or []),
            "readiness": build_readiness(None, task),
        })
    except Exception:
        logger.warning("claim readiness 计算失败，降级为只读字段", exc_info=True)
    if package:
        result["context_package"] = package
        result["package_only"] = bool(package_only)
        if estimate_drift is not None:
            result["estimate_drift"] = estimate_drift
    return result
