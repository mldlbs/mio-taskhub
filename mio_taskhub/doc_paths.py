"""Task document path registry: kind -> path.

Single source of truth for **which document kinds a task can carry** and how a
kind resolves to a stored path.

Background: documents live on disk; the DB only stores paths. Historically only
``Task.spec_path`` / ``Task.plan_path`` existed, so the API could serve just two
kinds — even though the workspace scanner (``api/task_documents.discover_task_docs``)
and the Web UI (``DocPanel.KIND_META``) already recognised a broader set.

``Task.doc_paths`` (JSON dict) generalises this to all kinds. The legacy
``spec_path`` / ``plan_path`` columns are still supported and **kept in sync** so
stage gates, the MCP tools (``taskhub_read_spec`` / ``taskhub_read_plan``) and the
Web UI keep working unchanged.
"""

from typing import Any, Dict, Iterable

# Keep in sync with:
#   - api/task_documents.discover_task_docs DOC_PATTERNS
#   - web/src/components/DocPanel.jsx KIND_META
DOC_KINDS = (
    "spec",
    "plan",
    "review",
    "requirement",
    "test",
    "architecture",
    "api",
    "readme",
    "changelog",
    # ── 扩展文档类型（完整软件项目文档矩阵，2026-09-17）──
    "decision",      # 决策 / ADR：为什么选 A 不选 B
    "risk",          # 风险登记
    "setup",         # 环境搭建 / 上手 / onboarding
    "runbook",       # 部署 / 运维 / 回滚
    "glossary",      # 术语表 / 领域词汇
    "userguide",     # 用户手册（区别于开发向 readme / 机器向 api）
    "research",      # 技术预研 / spike
    "retro",         # 复盘 / retrospective
    "milestone",     # 里程碑 / 发布计划 / Release 记录
    "data-model",    # 数据模型 / 表结构 / schema
    "security",      # 安全
    "troubleshooting",  # 故障排查 / FAQ
    "incident",      # 事故记录（生命周期 open → resolved → closed，见 doc_lifecycle.py）
)

# kind -> legacy column name on Task (kept mirrored for back-compat)
LEGACY_FIELD = {
    "spec": "spec_path",
    "plan": "plan_path",
}


def _clean(value: Any) -> str:
    """Coerce a path value to a stripped string ('' means 'not set')."""
    if value is None:
        return ""
    return str(value).strip()


def normalize_doc_paths(value: Any) -> Dict[str, str]:
    """Accept a dict (or None) and return a clean ``{kind: path}`` mapping.

    Unknown kinds and empty paths are dropped.
    """
    if not isinstance(value, dict):
        return {}
    out: Dict[str, str] = {}
    for kind, path in value.items():
        if kind not in DOC_KINDS:
            continue
        cleaned = _clean(path)
        if cleaned:
            out[kind] = cleaned
    return out


def merge_doc_paths(current: Any, incoming: Any) -> Dict[str, str]:
    """Merge ``incoming`` into ``current``.

    Explicitly passing an empty/None value for a known kind **removes** it —
    that is how a caller clears a document. Unknown kinds are ignored.
    """
    merged = normalize_doc_paths(current)
    if not isinstance(incoming, dict):
        return merged
    for kind, path in incoming.items():
        if kind not in DOC_KINDS:
            continue
        cleaned = _clean(path)
        if cleaned:
            merged[kind] = cleaned
        else:
            merged.pop(kind, None)
    return merged


def doc_path_of(task: Any, kind: str) -> str:
    """Resolve ``kind`` -> path for a task.

    Prefers ``task.doc_paths[kind]``; falls back to the legacy
    ``spec_path`` / ``plan_path`` columns so pre-existing rows still resolve.
    """
    if kind not in DOC_KINDS:
        return ""
    paths = getattr(task, "doc_paths", None)
    if isinstance(paths, dict):
        stored = _clean(paths.get(kind))
        if stored:
            return stored
    legacy = LEGACY_FIELD.get(kind)
    if legacy:
        return _clean(getattr(task, legacy, ""))
    return ""


def existing_doc_kinds(task: Any) -> Iterable[str]:
    """Yield the kinds that currently have a path, in canonical order."""
    for kind in DOC_KINDS:
        if doc_path_of(task, kind):
            yield kind


def sync_legacy_fields(task: Any, kind: str, path: str) -> None:
    """Mirror a spec/plan path into the legacy column; no-op for other kinds."""
    legacy = LEGACY_FIELD.get(kind)
    if legacy:
        setattr(task, legacy, _clean(path))


def sync_all_legacy(task: Any) -> None:
    """Re-derive ``spec_path`` / ``plan_path`` from ``doc_paths``."""
    for kind in LEGACY_FIELD:
        sync_legacy_fields(task, kind, doc_path_of(task, kind))
