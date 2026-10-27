# -*- coding: utf-8 -*-
"""Phase 1 完成门禁：风险分级、领取就绪、完成证据与 done 校验。"""
from __future__ import annotations

import glob
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from mio_taskhub.doc_paths import doc_path_of
from mio_taskhub.models import Run, RunState, Task, TaskStage

HIGH_LABELS = {"destructive", "migration", "credential", "production", "schema"}
CORE_LABELS = {"core-design", "api-change"}
HIGH_PATHS = ("migrations/", "auth/", ".github/workflows/", "dockerfile", "docker-compose")
CORE_PATHS = ("mio_taskhub/models.py", "mio_taskhub/api/", "mio_taskhub/workflow/",
              "mio_taskhub/scripts/git-hooks/")
TIER_ORDER = {"light": 0, "standard": 1, "high": 2}
TIER_KINDS = {
    "light": ("requirement",),
    "standard": ("requirement", "spec", "api"),
    "high": ("requirement", "spec", "api", "plan", "runbook"),
}
MANDATORY = {"light": (), "standard": (), "high": ("requirement", "spec")}
CHECK_ORDER = (
    "result_not_empty", "deliverables_exist", "verification_passed",
    "no_unresolved_high_deviation", "not_zero_diff",
)
_OFF = ("0", "off", "false", "no", "disabled")


class GateError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 422,
                 detail: Any = None):
        super().__init__(message or code)
        self.code = code
        self.message = message or code
        self.status = status
        self.detail = detail

    def as_detail(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.detail is not None:
            out["detail"] = self.detail
        return out


def gate_mode() -> str:
    raw = (os.environ.get("MIO_COMPLETION_GATE") or "warn").strip().lower()
    if raw in _OFF:
        return "off"
    return "block" if raw == "block" else "warn"


def claim_gate_mode() -> str:
    raw = (os.environ.get("MIO_CLAIM_GATE") or "warn").strip().lower()
    if raw in _OFF:
        return "off"
    return "block" if raw == "block" else "warn"


def infer_tier(task: Task) -> tuple[str, str]:
    """推断 tier；当前已是 standard/high 时保持（只升不降）。"""
    current = (getattr(task, "gate_tier", "") or "light").lower()
    reason = getattr(task, "gate_tier_reason", "") or ""
    if current in ("standard", "high"):
        return current, reason
    files = [str(f).lower().replace("\\", "/") for f in (getattr(task, "files", None) or [])]
    labels = {str(l).lower() for l in (getattr(task, "labels", None) or [])}
    if (str(getattr(task, "risk_level", "") or "").lower() == "high"
            or labels & HIGH_LABELS
            or any(f.startswith(p) for f in files for p in HIGH_PATHS)):
        return "high", "high_risk_signal"
    if doc_path_of(task, "spec") or doc_path_of(task, "api"):
        return "standard", "spec_registered"
    if labels & CORE_LABELS or any(f.startswith(p) for f in files for p in CORE_PATHS):
        return "standard", "core_path"
    return "light", "default"


def upgrade_tier(task: Task) -> None:
    """创建/更新 files/labels 后重新推断；只升不降。"""
    tier, reason = infer_tier(task)
    if TIER_ORDER.get(tier, 0) > TIER_ORDER.get((task.gate_tier or "light").lower(), 0):
        task.gate_tier, task.gate_tier_reason = tier, reason


def completion_checks(tier: str) -> list[str]:
    return list(CHECK_ORDER)


def _safe_rel(path: str) -> Optional[Path]:
    raw = str(path or "").strip()
    if not raw:
        return None
    p = Path(raw.replace("\\", "/"))
    if p.is_absolute() or (len(raw) > 1 and raw[1] == ":") or ".." in p.parts:
        return None
    return p


def resolve_task_path(task: Task, path: str) -> Optional[Path]:
    rel = _safe_rel(path)
    if rel is None:
        return None
    ws = str(getattr(task, "workspace", "") or "").strip()
    if not ws:
        return None
    return (Path(ws) / rel).resolve()


def _has_content(p: Path) -> bool:
    try:
        if p.is_file():
            return p.stat().st_size > 0
        if p.is_dir():
            return any(f.is_file() and f.stat().st_size > 0 for f in p.rglob("*"))
    except OSError:
        return False
    return False


def check_deliverables(task: Task) -> list[dict]:
    """返回缺失/空交付物；workspace 不可用时 fail-open 返回空。"""
    items = list(getattr(task, "deliverables", None) or [])
    if not items or not str(getattr(task, "workspace", "") or "").strip():
        return []
    missing = []
    for raw in items:
        path = str(raw).strip()
        target = resolve_task_path(task, path)
        if target is None:
            missing.append({"path": path, "reason": "unsafe_path"})
            continue
        matches = glob.glob(str(target)) if any(c in path for c in "*?[") else [str(target)]
        found = [Path(m) for m in matches if _has_content(Path(m))]
        if not found:
            missing.append({"path": path, "reason": "missing_or_empty"})
    return missing


def _clean_str(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def normalize_evidence(raw: Any, task: Task) -> dict:
    data = raw if isinstance(raw, dict) else {}
    ver = data.get("verification") if isinstance(data.get("verification"), dict) else {}
    commands = []
    for item in (ver.get("commands") or [])[:50]:
        if not isinstance(item, dict):
            continue
        exit_code = item.get("exit_code")
        if not isinstance(exit_code, int):
            raise GateError("gate.verification_missing", "exit_code must be an integer")
        commands.append({
            "cmd": _clean_str(item.get("cmd")),
            "exit_code": exit_code,
            "duration_sec": item.get("duration_sec"),
            "output_tail": _clean_str(item.get("output_tail")),
        })
    tests = []
    for item in (ver.get("tests") or [])[:50]:
        if not isinstance(item, dict):
            continue
        passed = item.get("passed")
        if not isinstance(passed, bool):
            raise GateError("gate.verification_missing", "test passed must be boolean")
        tests.append({"name": _clean_str(item.get("name")),
                      "suite": _clean_str(item.get("suite")), "passed": passed})
    artifacts = []
    for item in (ver.get("artifacts") or [])[:50]:
        rel = _safe_rel(item)
        if rel is None:
            raise GateError("gate.verification_missing", "artifact path must be workspace-relative")
        artifacts.append(rel.as_posix())
    workspace = str(getattr(task, "workspace", "") or "").strip()
    out = {
        "verification": {"commands": commands, "tests": tests, "artifacts": artifacts},
        "deliverables_checked": [
            {"path": d.get("path"), "exists": False} for d in check_deliverables(task)
        ],
        "workspace": workspace or None,
        "unverified": not bool(workspace),
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    for key in ("integration", "contract_checks", "contract_waivers"):
        if key in data:
            out[key] = data[key]
    return out


def has_machine_verification(evidence: dict) -> bool:
    ver = evidence.get("verification") or {}
    return bool(ver.get("commands") or ver.get("tests"))


def has_any_evidence(evidence: dict) -> bool:
    ver = evidence.get("verification") or {}
    return bool(ver.get("commands") or ver.get("tests") or ver.get("artifacts"))


def verification_failed(evidence: dict) -> bool:
    ver = evidence.get("verification") or {}
    return any(c.get("exit_code") not in (0, None) for c in ver.get("commands") or []) or \
        any(t.get("passed") is False for t in ver.get("tests") or [])


def is_zero_diff(run: Run) -> bool:
    summary = run.diff_summary or {}
    if summary.get("changed") is True:
        return False
    after = summary.get("after") or {}
    if any(int(after.get(k) or 0) for k in ("files", "insertions", "deletions")):
        return False
    return summary.get("changed") is False


def _unresolved_deviations(task: Task) -> list[dict]:
    out = []
    for dev in getattr(task, "deviations", None) or []:
        if not isinstance(dev, dict):
            continue
        if dev.get("severity") in ("high", "critical"):
            if dev.get("status") not in ("accepted", "resolved", "decided"):
                out.append(dev)
            elif dev.get("status") == "accepted" and not dev.get("decision_ref"):
                out.append(dev)
    return out


def require_human_confirmation(task: Task) -> None:
    if (task.gate_tier or "light").lower() != "high":
        return
    if not getattr(task, "human_confirmed_at", None) or not getattr(task, "human_confirmed_by", ""):
        raise GateError("gate.human_confirmation_required",
                        "high tier requires human confirmation", 409)


def collect_submit_problems(db: Session, task: Task, run: Run, body: dict) -> tuple[list[dict], dict]:
    issues: list[dict] = []
    evidence = normalize_evidence((body or {}).get("evidence"), task)
    result = str((body or {}).get("result") or "").strip()
    if len(result) < 10:
        issues.append({"code": "gate.result_too_thin", "message": "result is too short"})
    missing = check_deliverables(task)
    if missing:
        issues.append({"code": "gate.deliverable_missing",
                       "message": "deliverables missing", "detail": missing})
    if (task.gate_tier or "light") in ("standard", "high") or str(task.acceptance_criteria or "").strip():
        if not has_machine_verification(evidence):
            issues.append({"code": "gate.verification_missing",
                           "message": "machine verification missing"})
        elif verification_failed(evidence):
            issues.append({"code": "gate.verification_failed",
                           "message": "verification failed"})
    elif not has_any_evidence(evidence):
        issues.append({"code": "gate.verification_missing",
                       "message": "evidence missing"})
    for dev in _unresolved_deviations(task):
        issues.append({"code": "gate.deviation_needs_decision",
                       "message": "high deviation needs decision", "detail": dev.get("id")})
    if (task.gate_tier or "light") in ("standard", "high") and is_zero_diff(run):
        if str(getattr(task, "task_kind", "") or "") not in ("idea_review", "change_tracking") \
                and not getattr(task, "diff_gate_exempt", False):
            if not evidence["verification"]["artifacts"]:
                issues.append({"code": "gate.no_changes", "message": "zero diff without artifacts"})
    if (task.gate_tier or "light").lower() == "high" and (
            not getattr(task, "human_confirmed_at", None)
            or not getattr(task, "human_confirmed_by", "")):
        issues.append({"code": "gate.human_confirmation_required",
                       "message": "high tier requires human confirmation", "status": 409})
    return issues, evidence


def _raise_first(issues: list[dict]) -> None:
    first = issues[0]
    raise GateError(first["code"], first.get("message", ""),
                    int(first.get("status") or 422), first.get("detail"))


def _json_obj(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _sha256_file(task: Task, rel_path: str) -> str:
    p = resolve_task_path(task, rel_path)
    if p is None or not p.is_file():
        return ""
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return "sha256:" + h.hexdigest()


def contract_outputs_for(task: Task) -> dict:
    """从 context_package.contracts.outputs 计算当前产物指纹（FR-13）。"""
    pkg = _json_obj(getattr(task, "context_package", None))
    contracts = pkg.get("contracts") if isinstance(pkg.get("contracts"), dict) else {}
    out = {}
    for item in contracts.get("outputs") or []:
        if not isinstance(item, dict):
            continue
        artifact = str(item.get("artifact") or "").strip()
        path = str(item.get("path") or "").strip()
        if artifact and path:
            digest = _sha256_file(task, path)
            if digest:
                out[artifact] = digest
    return out


def _integration_issues(db: Session, task: Task) -> list[dict]:
    """P2d：主子任务集成门禁（FR-13）。"""
    from mio_taskhub.hierarchy import STAGE_INTEGRATE, stage_at_least

    children = db.exec(select(Task).where(Task.parent_id == task.id)).all()
    if not children:
        return []
    issues: list[dict] = []
    incomplete = [c.id for c in children
                  if c.state.value != "completed"
                  or getattr(c.stage, "value", c.stage) != "done"]
    if incomplete:
        issues.append({"code": "decompose.children_incomplete",
                       "message": "children are not all completed", "status": 409,
                       "detail": incomplete})
        return issues
    if not stage_at_least(STAGE_INTEGRATE):
        return issues

    parent_run = db.get(Run, task.completion_run_id) if task.completion_run_id else None
    parent_ev = (parent_run.evidence or {}) if parent_run is not None else {}
    waivers = set(parent_ev.get("contract_waivers") or [])
    decomposition = _json_obj(getattr(task, "decomposition", None))
    ref_map = decomposition.get("ref_map") or {}
    by_id = {c.id: c for c in children}
    child_runs = {c.id: (db.get(Run, c.completion_run_id) if c.completion_run_id else None)
                  for c in children}
    summary = []
    for child in children:
        missing = check_deliverables(child)
        if missing:
            issues.append({"code": "decompose.child_deliverable_missing",
                           "message": "child deliverable missing", "detail": missing})
        run = child_runs.get(child.id)
        if run is None or run.evidence is None or run.exit_code not in (0, None):
            issues.append({"code": "decompose.child_evidence_invalid",
                           "message": "child has no valid completion evidence",
                           "detail": child.id})
        summary.append({"id": child.id,
                        "run_id": child.completion_run_id,
                        "deliverables_ok": not missing,
                        "contract_ok": True})

    for edge in decomposition.get("contracts") or []:
        if not isinstance(edge, dict):
            continue
        source_ref = str(edge.get("from") or edge.get("from_ref") or "")
        artifact = str(edge.get("artifact") or "")
        source = by_id.get(ref_map.get(source_ref))
        if source is None or not artifact:
            continue
        pkg = _json_obj(getattr(source, "context_package", None))
        contracts = pkg.get("contracts") if isinstance(pkg.get("contracts"), dict) else {}
        out = next((o for o in contracts.get("outputs") or []
                    if isinstance(o, dict) and str(o.get("artifact") or "") == artifact), None)
        if not out:
            continue
        path = str(out.get("path") or "")
        stored = (child_runs[source.id].evidence or {}).get("contract_outputs", {}) \
            if child_runs.get(source.id) and child_runs[source.id].evidence else {}
        current = _sha256_file(source, path)
        if stored.get(artifact) and current and stored[artifact] != current \
                and artifact not in waivers:
            issues.append({"code": "decompose.contract_drift",
                           "message": f"contract output changed: {artifact}",
                           "detail": {"artifact": artifact, "path": path}})

    if parent_run is not None:
        evidence = dict(parent_run.evidence or {})
        if not evidence.get("integration"):
            issues.append({"code": "decompose.integration_required",
                           "message": "parent evidence.integration=true is required"})
        checks = evidence.get("contract_checks") or {}
        for edge in decomposition.get("contracts") or []:
            if not isinstance(edge, dict):
                continue
            key = f"{edge.get('from') or edge.get('from_ref')}->{edge.get('to') or edge.get('to_ref')}:{edge.get('artifact')}"
            if key not in checks:
                issues.append({"code": "decompose.contract_unchecked",
                               "message": f"contract edge unchecked: {key}"})
                break
        evidence["children"] = summary
        parent_run.evidence = evidence
    return issues


def check_submit(db: Session, task: Task, run: Run, body: dict) -> list[dict]:
    mode = gate_mode()
    if mode == "off":
        return []
    issues, evidence = collect_submit_problems(db, task, run, body)
    if issues and mode == "block":
        _raise_first(issues)
    outputs = contract_outputs_for(task)
    if outputs:
        evidence["contract_outputs"] = outputs
    run.evidence = evidence
    if not issues:
        task.completion_run_id = run.id or ""
    return issues


def check_done(db: Session, task: Task) -> list[dict]:
    mode = gate_mode()
    if mode == "off":
        return []
    issues: list[dict] = []
    issues.extend(_integration_issues(db, task))
    run = None
    if not task.completion_run_id:
        if str(getattr(task, "stage", "") or "") in ("done", "TaskStage.DONE") or \
                getattr(getattr(task, "stage", None), "value", None) == "done":
            return issues
        issues.append({"code": "gate.completion_missing",
                       "message": "no completion run"})
    else:
        run = db.get(Run, task.completion_run_id)
        state_val = getattr(run.state, "value", run.state) if run is not None else None
        if run is None or state_val not in ("finished", "completed") or run.exit_code not in (0, None):
            issues.append({"code": "gate.completion_missing",
                           "message": "completion run is missing or not successful"})
        elif run.evidence is None:
            issues.append({"code": "gate.verification_missing",
                           "message": "completion run has no evidence"})
    missing = check_deliverables(task)
    if missing:
        issues.append({"code": "gate.deliverable_missing",
                       "message": "deliverables missing", "detail": missing})
    for dev in _unresolved_deviations(task):
        issues.append({"code": "gate.deviation_needs_decision",
                       "message": "high deviation needs decision", "detail": dev.get("id")})
    if (task.gate_tier or "light").lower() == "high" and (
            not getattr(task, "human_confirmed_at", None)
            or not getattr(task, "human_confirmed_by", "")):
        issues.append({"code": "gate.human_confirmation_required",
                       "message": "high tier requires human confirmation", "status": 409})
    if issues and mode == "block":
        _raise_first(issues)
    return issues


def build_readiness(db: Session, task: Task) -> dict:
    tier = (task.gate_tier or "light").lower()
    if tier not in TIER_KINDS:
        tier = "light"
    kinds = [k for k in TIER_KINDS[tier] if doc_path_of(task, k)]
    missing = []
    for kind in kinds:
        p = resolve_task_path(task, doc_path_of(task, kind))
        if p is None or not p.is_file():
            missing.append({"kind": kind, "reason": "registered_but_file_missing",
                            "path": doc_path_of(task, kind)})
    for kind in MANDATORY.get(tier, ()):
        if not doc_path_of(task, kind):
            missing.append({"kind": kind, "reason": "required_but_not_registered", "path": ""})
    if tier == "standard" and (task.gate_tier_reason or "") == "core_path" and not doc_path_of(task, "spec"):
        missing.append({"kind": "spec", "reason": "required_but_not_registered", "path": ""})
    return {
        "ready": not missing,
        "required_reads": kinds,
        "missing": missing,
        "completion_checks": completion_checks(tier),
    }


def readiness_for_claim(db: Session, task: Task) -> dict:
    return build_readiness(db, task)
