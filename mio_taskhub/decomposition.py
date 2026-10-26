"""P2b 拆分校验与 apply（FR-9 / FR-11）。

边界：
- preview 只做校验并返回结果，不创建子任务、不改任务字段；
- apply 在校验通过后事务内创建子任务并回写父任务 `decomposition`；
- P2c 的上下文装配/预算、P2d 的集成门禁不在本模块。

错误统一为 `decompose.<code>`，由 API 层包装成
`{"detail": {"code", "message", "refs", "errors"}}`。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlmodel import select

from mio_taskhub.dependency import normalize_depends
from mio_taskhub.doc_paths import doc_path_of, existing_doc_kinds
from mio_taskhub.events import emit_event
from mio_taskhub.hierarchy import (
    ROLE_CHILD, ROLE_MAIN, STAGE_CONTEXT, normalize_role, stage_at_least,
)
from mio_taskhub.models import Task, TaskStage
from mio_taskhub.planner import detect_cycle

COMPLEXITIES = ("S", "M", "L")
DEFAULT_MAX_CHILDREN = 8
STAGE_DONE = "done"
CONTEXT_METHOD = "chars/2.5 + 1500 + 2000"
_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)")


class DecomposeError(Exception):
    def __init__(self, status: int, code: str, message: str,
                 refs: Optional[list] = None, errors: Optional[list] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.refs = refs or []
        self.errors = errors or []

    def detail(self) -> dict:
        out = {"code": self.code, "message": self.message, "refs": self.refs}
        if self.errors:
            out["errors"] = self.errors
        return out


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def context_config() -> dict:
    """P2c 上下文预算配置；每次调用读取 env，便于单测与热调。"""
    soft = _env_int("MIO_SUBTASK_CONTEXT_SOFT", 8000)
    hard = _env_int("MIO_SUBTASK_CONTEXT_HARD", 16000)
    window = _env_int("MIO_CONTEXT_WINDOW", 0)
    if window > 0:
        hard = min(hard, int(window * 0.5))
    return {
        "soft": soft,
        "hard": hard,
        "chars_per_token": _env_float("MIO_CONTEXT_CHARS_PER_TOKEN", 2.5),
        "tool_overhead": _env_int("MIO_CONTEXT_TOOL_OVERHEAD", 1500),
        "output_reserve": _env_int("MIO_CONTEXT_OUTPUT_RESERVE", 2000),
        "window": window,
    }


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _section_text(text: str, sections: list[str]) -> Optional[str]:
    """按 H2/H3 编号或标题前缀截取章节；无 sections 返回全文。"""
    wanted = [str(s).strip() for s in sections if str(s).strip()]
    if not wanted:
        return text
    marks = [(m.start(), m.group(2).strip()) for m in _HEADING_RE.finditer(text)]
    chunks: list[str] = []
    for want in wanted:
        matched = False
        for idx, (start, title) in enumerate(marks):
            num = _NUM_RE.match(title)
            if want == title or title.startswith(want) or (num and want == num.group(1)):
                end = marks[idx + 1][0] if idx + 1 < len(marks) else len(text)
                chunks.append(text[start:end])
                matched = True
                break
        if not matched:
            return None
    return "\n".join(chunks)


def _resolve_package_path(parent: Task, entry: dict) -> Optional[Path]:
    raw = str(entry.get("path") or "").strip()
    if not raw:
        kind = str(entry.get("kind") or "").strip()
        raw = doc_path_of(parent, kind) if kind else ""
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        workspace = str(getattr(parent, "workspace", "") or "").strip()
        if not workspace:
            return None
        p = Path(workspace) / p
    try:
        return p.resolve()
    except OSError:
        return None


def read_context_sections(parent: Task, entry: dict) -> tuple[Optional[str], Optional[Path], str]:
    """读取 context_package.documents[] 指定章节；返回 (text, path, error_code)。"""
    path = _resolve_package_path(parent, entry)
    text = _read_text(path) if path is not None and path.is_file() else None
    if text is None:
        return None, path, "context_estimate_missing"
    selected = _section_text(text, _text_list(entry.get("sections")))
    if selected is None:
        return None, path, "section_not_found"
    return selected, path, ""


def _package_documents(parent: Task, child: dict, result: Optional[ValidationResult] = None) -> list[dict]:
    """规范化 context_package.documents，并计算每个文档的字符数。"""
    ref = child.get("ref")
    out: list[dict] = []
    for raw in _as_list(child.get("documents")):
        if isinstance(raw, str):
            entry: dict = {"kind": raw}
        elif isinstance(raw, dict):
            entry = dict(raw)
        else:
            if result is not None:
                result.error("schema_invalid", "document must be an object", [ref])
            continue
        sections = _text_list(entry.get("sections"))
        selected, _path, error_code = read_context_sections(parent, entry)
        if selected is None:
            if result is not None:
                message = (f"document section not found: {sections}"
                           if error_code == "section_not_found"
                           else f"cannot read document: {entry.get('path') or entry.get('kind')}")
                result.error(error_code, message, [ref])
            out.append({**entry, "sections": sections, "chars": 0})
            continue
        out.append({**entry, "sections": sections, "chars": len(selected)})
    return out


def _file_chars(package: dict, workspace: str = "") -> int:
    total = 0
    for raw in _text_list(package.get("files")):
        p = Path(raw)
        if not p.is_absolute():
            if not workspace:
                continue
            p = Path(workspace) / p
        try:
            if p.is_file():
                total += len(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return total


def estimate_tokens(package: dict, parent_docs: Any = None,
                    config: Optional[dict] = None, workspace: str = "") -> dict:
    """估算上下文 tokens；文档用预计算 chars，文件按存在内容计。

    兼容 spec 的 (package, parent_docs, cfg) 调用形态：parent_docs 仅作占位，
    实际文档字符数由 context_package.documents[].chars 提供。
    """
    if config is None and isinstance(parent_docs, dict) and "chars_per_token" in parent_docs:
        config = parent_docs
    cfg = config or context_config()
    doc_chars = sum(int(d.get("chars") or 0) for d in _as_list(package.get("documents"))
                    if isinstance(d, dict))
    summary_chars = len(str((package.get("parent") or {}).get("summary") or ""))
    chars = doc_chars + summary_chars + _file_chars(package, workspace)
    base = math.ceil(chars / cfg["chars_per_token"])
    tokens = base + cfg["tool_overhead"] + cfg["output_reserve"]
    return {
        "tokens": tokens,
        "estimated": True,
        "method": CONTEXT_METHOD,
        "soft": cfg["soft"],
        "hard": cfg["hard"],
        "over_soft": tokens > cfg["soft"],
        "over_hard": tokens > cfg["hard"],
    }


def _stage_str(task: Task) -> str:
    stage = getattr(task, "stage", "")
    return stage.value if hasattr(stage, "value") else str(stage)


def _doc_state(task: Task, kind: str) -> str:
    statuses = getattr(task, "doc_statuses", None) or {}
    if not isinstance(statuses, dict):
        return ""
    entry = statuses.get(kind)
    return str(entry.get("state") or "") if isinstance(entry, dict) else ""


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _text_list(value: Any) -> list[str]:
    return [str(x).strip() for x in _as_list(value) if str(x).strip()]


def _has_items(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return bool(_text_list(value))


def _canonical(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "apply_token"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_apply_token(parent_id: str, payload: dict) -> str:
    digest = hashlib.sha256(f"{parent_id}:{_canonical(payload)}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def infer_complexity(files: list, doc_kinds: list, description: str,
                     modules: Optional[list] = None) -> dict:
    signals: list[str] = []
    score = 0
    if len(files) > 6:
        signals.append("files>6")
        score += 2
    if len(files) > 12:
        signals.append("files>12")
        score += 2
    if modules and len(modules) > 2:
        signals.append("modules>2")
        score += 1
    if any(k in doc_kinds for k in ("spec", "architecture")):
        signals.append("has_design_doc")
        score += 1
    text = (description or "").lower()
    if any(w in text for w in ("迁移", "migration", "auth", "权限", "permission")):
        signals.append("high_risk_path")
        score += 2
    suggested = "L" if score >= 4 else "M" if score >= 2 else "S"
    return {"suggested": suggested, "signals": signals}


@dataclass
class ValidationResult:
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    complexity: dict = field(default_factory=dict)
    plan_coverage: dict = field(default_factory=dict)
    contract_edges: list[dict] = field(default_factory=list)
    context_estimates: dict = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return bool(self.errors)

    def error(self, code: str, message: str, refs: Optional[list] = None):
        self.errors.append({"code": f"decompose.{code}", "message": message,
                            "refs": refs or []})

    def warning(self, code: str, message: str, refs: Optional[list] = None):
        self.warnings.append({"code": f"decompose.{code}", "message": message,
                              "refs": refs or []})

    def preview(self, apply_token: str) -> dict:
        return {
            "ok": not self.has_errors,
            "errors": self.errors,
            "warnings": self.warnings,
            "complexity": self.complexity,
            "plan_coverage": self.plan_coverage,
            "context_estimates": self.context_estimates,
            "contract_graph": {"edges": self.contract_edges},
            "apply_token": apply_token,
        }


def _safe_relative(path: str, workspace: str) -> bool:
    raw = str(path or "").strip()
    if not raw:
        return False
    p = Path(raw)
    if p.is_absolute() or (len(raw) > 1 and raw[1] == ":"):
        return False
    if ".." in p.parts:
        return False
    if workspace:
        root = Path(workspace).resolve()
        target = (root / p).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return False
    return True


def _norm_path(path: str) -> str:
    return os.path.normcase(str(Path(str(path).replace("\\", "/"))))


def _check_contracts(children: list[dict], refs: set[str],
                     result: ValidationResult, payload_edges: Any) -> None:
    outputs: dict[tuple[str, str], dict] = {}
    inputs_by_child: dict[str, set[tuple[str, str]]] = {}

    for child in children:
        ref = child.get("ref")
        contracts = child.get("contracts") if isinstance(child.get("contracts"), dict) else {}
        for out in _as_list(contracts.get("outputs")):
            if not isinstance(out, dict):
                result.error("schema_invalid", "contract output must be an object", [ref])
                continue
            artifact = str(out.get("artifact") or "").strip()
            source = str(out.get("from_ref") or ref or "").strip()
            if not artifact:
                result.error("schema_invalid", "contract output artifact is required", [ref])
                continue
            if source not in refs:
                result.error("unknown_child_ref",
                             f"output source ref not found: {source}", [ref, source])
                continue
            key = (source, artifact)
            if key in outputs:
                result.error("contract_duplicate",
                             f"duplicate output ({source}, {artifact})", [ref])
                continue
            outputs[key] = {
                "from_ref": source,
                "artifact": artifact,
                "contract": str(out.get("contract") or ""),
                "path": str(out.get("path") or ""),
                "consumers": _text_list(out.get("consumers")),
                "ref": ref,
            }

    for child in children:
        ref = child.get("ref")
        contracts = child.get("contracts") if isinstance(child.get("contracts"), dict) else {}
        seen: set[tuple[str, str]] = set()
        for inp in _as_list(contracts.get("inputs")):
            if not isinstance(inp, dict):
                result.error("schema_invalid", "contract input must be an object", [ref])
                continue
            artifact = str(inp.get("artifact") or "").strip()
            source = str(inp.get("from_ref") or "").strip()
            if not artifact or not source:
                result.error("schema_invalid",
                             "contract input requires from_ref and artifact", [ref])
                continue
            if source not in refs:
                result.error("unknown_child_ref",
                             f"input source ref not found: {source}", [ref, source])
                continue
            key = (source, artifact)
            seen.add(key)
            out = outputs.get(key)
            if out is None:
                result.error("contract_unknown_input",
                             f"input has no matching output ({source}, {artifact})", [ref])
                continue
            if out["consumers"] and ref not in out["consumers"]:
                result.error("contract_consumer_mismatch",
                             f"consumer {ref} is not declared by output ({source}, {artifact})",
                             [ref, source])
        inputs_by_child[ref] = seen

    for key, out in outputs.items():
        for consumer in out["consumers"]:
            if consumer not in refs:
                result.error("unknown_child_ref",
                             f"consumer ref not found: {consumer}", [out["ref"], consumer])
            elif key not in inputs_by_child.get(consumer, set()):
                result.error("contract_consumer_mismatch",
                             f"consumer {consumer} does not declare input ({key[0]}, {key[1]})",
                             [out["ref"], consumer])

    edges: list[dict] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for raw in _as_list(payload_edges):
        if not isinstance(raw, dict):
            result.error("schema_invalid", "top-level contract must be an object")
            continue
        source = str(raw.get("from_ref") or "").strip()
        target = str(raw.get("to_ref") or "").strip()
        artifact = str(raw.get("artifact") or "").strip()
        if not source or not target or not artifact:
            result.error("schema_invalid",
                         "top-level contract requires from_ref/to_ref/artifact",
                         [x for x in (source, target) if x])
            continue
        if source not in refs or target not in refs:
            result.error("unknown_child_ref",
                         f"contract edge ref not found: {source} -> {target}",
                         [x for x in (source, target) if x not in refs])
            continue
        edge_key = (source, target, artifact)
        if edge_key in seen_edges:
            result.error("contract_duplicate",
                         f"duplicate contract edge ({source}, {artifact})", [source])
            continue
        seen_edges.add(edge_key)
        if (source, artifact) not in outputs:
            result.error("contract_missing_output",
                         f"contract edge source has no output ({source}, {artifact})", [source])
            continue
        if (source, artifact) not in inputs_by_child.get(target, set()):
            result.error("contract_consumer_mismatch",
                         f"edge {source}->{target} lacks matching input", [source, target])
            continue
        edges.append({"from": source, "to": target, "artifact": artifact})

    if not edges:
        for child in children:
            ref = child.get("ref")
            contracts = child.get("contracts") if isinstance(child.get("contracts"), dict) else {}
            for inp in _as_list(contracts.get("inputs")):
                if not isinstance(inp, dict):
                    continue
                source = str(inp.get("from_ref") or "").strip()
                artifact = str(inp.get("artifact") or "").strip()
                if source and artifact and (source, artifact) in outputs:
                    edges.append({"from": source, "to": ref, "artifact": artifact})
    result.contract_edges = edges


def _check_boundaries(child: dict, workspace: str, result: ValidationResult) -> None:
    ref = child.get("ref")
    contracts = child.get("contracts") if isinstance(child.get("contracts"), dict) else {}
    boundaries = contracts.get("boundaries") if isinstance(contracts.get("boundaries"), dict) else {}
    allowed = _text_list(boundaries.get("allowed_files"))
    files = _text_list(child.get("files"))
    allowed_norm = {_norm_path(p) for p in allowed}
    for path in allowed:
        if not _safe_relative(path, workspace):
            result.error("boundary_violation",
                         f"allowed_files path escapes workspace: {path}", [ref])
    if allowed:
        for path in files:
            if _norm_path(path) not in allowed_norm:
                result.error("boundary_violation",
                             f"file outside allowed_files: {path}", [ref])
    for path in files:
        if not _safe_relative(path, workspace):
            result.error("boundary_violation",
                         f"file path escapes workspace: {path}", [ref])
    frozen = _text_list(boundaries.get("frozen_interfaces"))
    for interface in frozen:
        if any(interface == f or f.endswith(interface) for f in files):
            result.warning("boundary_violation",
                           f"touches frozen interface: {interface}", [ref])


def _check_complexity(parent: Task, payload: dict, children: list[dict],
                      result: ValidationResult) -> str:
    files: list[str] = []
    for child in children:
        files.extend(_text_list(child.get("files")))
    files = sorted(set(files))
    modules = sorted({Path(f).parts[0] for f in files if Path(f).parts})
    description = f"{getattr(parent, 'description', '') or ''} {payload.get('parent_summary') or ''}"
    info = infer_complexity(files, list(existing_doc_kinds(parent)), description, modules)
    raw_declared = payload.get("complexity") or getattr(parent, "complexity", None)
    if raw_declared is not None:
        raw_declared = str(raw_declared).strip().upper()
    declared = raw_declared if raw_declared in COMPLEXITIES else None
    if raw_declared is not None and declared is None:
        result.error("schema_invalid", "complexity must be S/M/L")
    result.complexity = {"declared": declared, "suggested": info["suggested"],
                         "signals": info["signals"]}
    if declared == "S" and info["suggested"] in ("M", "L"):
        result.warning("complexity_signal",
                       f"declared S but signals suggest {info['suggested']}")
    if declared == "M" and not doc_path_of(parent, "plan"):
        result.warning("plan_recommended", "declared M but no plan document")
    required = (os.environ.get("MIO_DECOMPOSE_REQUIRED") or "").strip().upper()
    if required == "L" and "high_risk_path" in info["signals"] \
            and declared in ("S", "M") \
            and not str(payload.get("complexity_override_reason") or "").strip():
        result.error("complexity_override_required",
                     "high-risk path declares below L without override_reason")
    return declared or ""


def _check_budget(parent: Task, children: list[dict], payload: dict,
                  declared: str, result: ValidationResult) -> None:
    """P2c：估算每个子任务上下文包；软阈值 warning，硬上限 error。"""
    if not stage_at_least(STAGE_CONTEXT):
        return
    for child in children:
        ref = str(child.get("ref") or "")
        package = _child_context(parent, child, payload, declared, result)
        est = package.get("estimate") or {}
        result.context_estimates[ref] = est
        if est.get("over_soft"):
            result.warning(
                "context_soft_budget",
                f"{ref} 估算 {est.get('tokens')} tokens，超过软预算 {est.get('soft')}",
                [ref],
            )
        if est.get("over_hard"):
            result.error(
                "context_hard_cap",
                f"{ref} 估算 {est.get('tokens')} tokens，超过硬上限 {est.get('hard')}",
                [ref],
            )


def _check_docs(parent: Task, declared: str, result: ValidationResult) -> None:
    plan_state = _doc_state(parent, "plan")
    if declared == "L":
        missing = [k for k in ("requirement", "spec", "plan") if not doc_path_of(parent, k)]
        if missing:
            result.error("doc_required",
                         f"complexity L requires documents: {', '.join(missing)}", missing)
            return
        if plan_state != "approved":
            result.error("plan_not_approved",
                         "complexity L requires approved plan", ["plan"])
            return
    if os.environ.get("MIO_DECOMPOSE_REQUIRE_PLAN_APPROVAL") == "1" \
            and plan_state != "approved":
        result.error("plan_not_approved",
                     "plan approval required by MIO_DECOMPOSE_REQUIRE_PLAN_APPROVAL=1",
                     ["plan"])


def validate_decomposition(payload: dict, parent: Task) -> ValidationResult:
    result = ValidationResult()
    if not isinstance(payload, dict):
        result.error("schema_invalid", "request body must be an object")
        return result
    payload_edges = _as_list(payload.get("contracts"))

    plan_items = _as_list(payload.get("plan_items"))
    children = _as_list(payload.get("children"))
    max_children = _env_int("MIO_DECOMPOSE_MAX_CHILDREN", DEFAULT_MAX_CHILDREN)
    if not children:
        result.error("schema_invalid", "children must not be empty")
    if len(children) > max_children:
        result.error("too_many_children",
                     f"children={len(children)} exceeds limit {max_children}")

    if normalize_role(parent) == ROLE_CHILD or getattr(parent, "parent_id", None):
        result.error("depth_exceeded", "child task cannot be decomposed again", [parent.id])

    plan_refs: list[str] = []
    for item in plan_items:
        if not isinstance(item, dict):
            result.error("schema_invalid", "plan item must be an object")
            continue
        ref = str(item.get("ref") or "").strip()
        if not ref:
            result.error("schema_invalid", "plan item ref is required")
            continue
        if ref in plan_refs:
            result.error("schema_invalid", f"duplicate plan item ref: {ref}", [ref])
            continue
        plan_refs.append(ref)

    refs: list[str] = []
    for child in children:
        if not isinstance(child, dict):
            result.error("schema_invalid", "child must be an object")
            continue
        ref = str(child.get("ref") or "").strip()
        if not ref:
            result.error("schema_invalid", "child ref is required")
            continue
        if ref in refs:
            result.error("schema_invalid", f"duplicate child ref: {ref}", [ref])
            continue
        refs.append(ref)
        if not str(child.get("title") or "").strip():
            result.error("schema_invalid", f"child {ref} title is required", [ref])
        complexity = child.get("complexity")
        if complexity is not None and complexity not in COMPLEXITIES:
            result.error("schema_invalid",
                         f"child {ref} complexity must be S/M/L", [ref])

    ref_set = set(refs)
    covered: set[str] = set()
    for child in children:
        if not isinstance(child, dict):
            continue
        ref = str(child.get("ref") or "").strip()
        for pref in _text_list(child.get("plan_item_refs")):
            if pref not in plan_refs:
                result.error("unknown_plan_ref",
                             f"child {ref} references unknown plan item {pref}", [ref, pref])
            else:
                covered.add(pref)
        for dep in _text_list(child.get("depends_on_refs")):
            if dep not in ref_set:
                result.error("unknown_child_ref",
                             f"child {ref} depends on unknown ref {dep}", [ref, dep])
        if not _has_items(child.get("acceptance_criteria")):
            result.error("missing_acceptance",
                         f"child {ref} has no acceptance criteria", [ref])
        if not _has_items(child.get("deliverables")):
            result.error("missing_deliverable",
                         f"child {ref} has no deliverables", [ref])
        if not _has_items(child.get("verification")):
            result.error("missing_verification",
                         f"child {ref} has no verification", [ref])

    missing = [ref for ref in plan_refs if ref not in covered]
    result.plan_coverage = {"total": len(plan_refs), "covered": len(covered),
                            "missing": missing}
    if missing:
        result.error("plan_item_uncovered",
                     f"plan items not covered: {', '.join(missing)}", missing)

    deps = {str(c.get("ref")): _text_list(c.get("depends_on_refs"))
            for c in children if isinstance(c, dict) and c.get("ref")}
    cycle = detect_cycle(deps)
    if cycle:
        result.error("cycle", f"dependency cycle: {' -> '.join(cycle)}", cycle)

    _check_contracts([c for c in children if isinstance(c, dict)], ref_set, result,
                     payload_edges)

    workspace = str(getattr(parent, "workspace", "") or "")
    for child in children:
        if isinstance(child, dict):
            _check_boundaries(child, workspace, result)

    declared = _check_complexity(parent, payload, [c for c in children if isinstance(c, dict)],
                                 result)
    _check_budget(parent, [c for c in children if isinstance(c, dict)], payload,
                  declared, result)
    _check_docs(parent, declared, result)
    return result


def _child_acceptance(value: Any) -> str:
    return "\n".join(_text_list(value)) if not isinstance(value, str) else value.strip()


def _child_context(parent: Task, child: dict, payload: dict,
                   declared: str, result: Optional[ValidationResult] = None) -> dict:
    docs = _package_documents(parent, child, result)
    package = {
        "version": 1,
        "parent": {"id": parent.id, "title": parent.title,
                   "summary": str(payload.get("parent_summary") or "")},
        "goal": str(child.get("description") or ""),
        "acceptance_criteria": _text_list(child.get("acceptance_criteria")),
        "deliverables": _text_list(child.get("deliverables")),
        "files": _text_list(child.get("files")),
        "verification": child.get("verification") or "",
        "documents": docs,
        "contracts": child.get("contracts") or {},
        "complexity": child.get("complexity") or declared or None,
    }
    contracts = package["contracts"]
    if isinstance(contracts, dict) and "revalidation" not in contracts:
        contracts = dict(contracts)
        contracts["revalidation"] = {
            "on_upstream_change": ["rerun_verification", "notify_consumers"]
        }
        package["contracts"] = contracts
    workspace = str(getattr(parent, "workspace", "") or "")
    package["estimate"] = estimate_tokens(package, workspace=workspace)
    return package


def apply_decomposition(db, parent: Task, payload: dict, result: ValidationResult,
                        apply_token: str) -> dict:
    if result.has_errors:
        raise DecomposeError(422, result.errors[0]["code"],
                             result.errors[0]["message"],
                             result.errors[0].get("refs") or [],
                             errors=result.errors)
    existing = db.exec(select(Task).where(Task.parent_id == parent.id)).first()
    if existing is not None or getattr(parent, "decomposition", None):
        raise DecomposeError(409, "decompose.already_decomposed",
                             "task has already been decomposed", [parent.id])
    if _stage_str(parent) == STAGE_DONE or parent.state in ("completed", "cancelled"):
        raise DecomposeError(409, "decompose.parent_terminal",
                             "parent task is terminal", [parent.id])

    children = [c for c in _as_list(payload.get("children")) if isinstance(c, dict)]
    declared = (result.complexity or {}).get("declared") or ""
    ref_map: dict[str, str] = {}
    created: list[tuple[dict, Task]] = []
    try:
        for child in children:
            ref = str(child.get("ref"))
            task = Task(
                id=str(uuid.uuid4())[:8],
                title=str(child.get("title") or ""),
                description=str(child.get("description") or ""),
                acceptance_criteria=_child_acceptance(child.get("acceptance_criteria")),
                deliverables=_text_list(child.get("deliverables")),
                files=_text_list(child.get("files")),
                project=parent.project,
                workspace=parent.workspace,
                target_agent_type=parent.target_agent_type,
                fallback_after=parent.fallback_after,
                priority=parent.priority,
                est_duration_min=int(child.get("est_duration_min") or parent.est_duration_min or 30),
                max_retries=parent.max_retries,
                labels=list(parent.labels or []),
                stage=TaskStage.READY,
                role=ROLE_CHILD,
                parent_id=parent.id,
                complexity=child.get("complexity") or declared or None,
                context_package=json.dumps(
                    _child_context(parent, child, payload, declared), ensure_ascii=False),
                doc_paths=dict(parent.doc_paths or {}),
                spec_path=parent.spec_path or "",
                plan_path=parent.plan_path or "",
                doc_gate_exempt=bool(parent.doc_gate_exempt),
            )
            db.add(task)
            db.flush()
            ref_map[ref] = task.id
            created.append((child, task))
        for child, task in created:
            task.depends_on = normalize_depends(
                [ref_map[r] for r in _text_list(child.get("depends_on_refs"))])
            db.add(task)
        parent.role = ROLE_MAIN
        parent.decomposition = json.dumps({
            "version": 1,
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "complexity_declared": declared or None,
            "complexity_signals": (result.complexity or {}).get("signals", []),
            "plan_items": _as_list(payload.get("plan_items")),
            "child_ids": list(ref_map.values()),
            "ref_map": ref_map,
            "contracts": result.contract_edges,
            "context_estimates": result.context_estimates,
            "apply_token": apply_token,
            "parent_summary": str(payload.get("parent_summary") or ""),
        }, ensure_ascii=False)
        db.add(parent)
        emit_event(db, type="task_decomposed", entity="task", entity_id=parent.id,
                   payload={"children": list(ref_map.values()), "ref_map": ref_map})
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {
        "parent_id": parent.id,
        "children": [{"id": task.id, "ref": ref, "title": task.title,
                      "stage": _stage_str(task)} for ref, task in
                     [(str(c.get("ref")), t) for c, t in created]],
        "ref_map": ref_map,
        "decomposition": json.loads(parent.decomposition),
    }
