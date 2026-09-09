import pathlib
import os
import re
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from mio_taskhub.db import get_session
from mio_taskhub.models import Task

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_doc_path(task, rel, kind):
    p = pathlib.Path(rel)
    if not p.is_absolute():
        base = (task.workspace or '').strip()
        if not base:
            return None, (f'任务未设置 workspace，且 {kind}_path 为相对路径「{rel}」，无法确定基准目录。'
                          f'请改用绝对路径，或在任务中填写 workspace 后重试。')
        p = pathlib.Path(base) / p
    return p.resolve(), None


def _read_content(p):
    if not p.exists():
        return None, False
    try:
        text = p.read_text(encoding='utf-8', errors='replace')
    except Exception as e:
        return f'读取失败：{p}\n{e}', False
    MAX = 200_000
    truncated = len(text) > MAX
    if truncated:
        text = text[:MAX]
    return text, truncated


def discover_task_docs(workspace: str):
    if not workspace or not os.path.isdir(workspace):
        return []
    SKIP = {'node_modules', 'dist', 'build', '.venv', '.git', '.workbuddy',
            '.memory-backup', '__pycache__', '.idea', '.vscode'}
    DOC_PATTERNS = {
        'spec':    ['spec'],
        'plan':    ['plan'],
        'requirement': ['requirement', 'req', '需求'],
        'test':    ['test-plan', 'test_plan', 'testing'],
        'architecture': ['architecture', 'arch', 'design-doc'],
        'readme':  ['readme'],
        'changelog': ['changelog', 'changes', 'release-notes'],
        'api':     ['api-doc', 'api_spec', 'openapi'],
    }
    out = []
    for root, dirs, files in os.walk(workspace):
        dirs[:] = [d for d in dirs if d not in SKIP]
        for f in files:
            low = f.lower()
            if not low.endswith('.md'):
                continue
            kind = None
            for k, keywords in DOC_PATTERNS.items():
                if any(kw in low for kw in keywords):
                    kind = k
                    break
            if kind is None:
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, workspace).replace(os.sep, '/')
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            out.append({'name': f, 'rel_path': rel, 'kind': kind, 'size': size, 'source': 'discovered'})
    out.sort(key=lambda x: (x['kind'], x['rel_path']))
    return out


def _field_doc_entry(rel, kind, ws):
    name = os.path.basename(rel)
    p = rel if os.path.isabs(rel) else (os.path.join(ws, rel) if ws else rel)
    try:
        size = os.path.getsize(p)
    except OSError:
        size = 0
    return {'name': name, 'rel_path': rel, 'kind': kind, 'size': size, 'source': 'field'}


def _slug(name):
    n = name.lower()
    if n.startswith('spec-') or n.startswith('plan-'):
        n = n[5:]
    if n.endswith('.md'):
        n = n[:-3]
    return n


def _tokens(s):
    return {tok for tok in re.split(r'[^a-z0-9]+', s.lower()) if len(tok) >= 2}


def _task_keys(t):
    keys = set()
    for p in (t.spec_path, t.plan_path):
        if not p:
            continue
        keys |= _tokens(_slug(os.path.basename(p)))
    if t.title:
        keys |= _tokens(t.title)
    return keys


def _is_related(d, keys):
    return bool(_tokens(_slug(d['name'])) & keys)


# ── Endpoints ───────────────────────────────────────────────────────────────

@router.get('/{task_id}/doc')
def get_task_doc(task_id: str, kind: str = Query(...), db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    if kind not in ('spec', 'plan'):
        raise HTTPException(400, 'kind must be spec or plan')
    rel = t.spec_path if kind == 'spec' else t.plan_path
    if not rel:
        raise HTTPException(404, f'task has no {kind}_path')
    p, err = _resolve_doc_path(t, rel, kind)
    if err:
        return {'kind': kind, 'path': rel, 'content': err, 'truncated': False, 'missing': True}
    text, truncated = _read_content(p)
    return {'kind': kind, 'path': rel,
            'content': (text if text is not None else f'文件不存在：{p}'),
            'truncated': truncated, 'missing': text is None}


@router.get('/{task_id}/documents')
def list_task_documents(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    ws = (t.workspace or '').strip()
    docs = []
    if t.spec_path:
        docs.append(_field_doc_entry(t.spec_path, 'spec', ws))
    if t.plan_path:
        docs.append(_field_doc_entry(t.plan_path, 'plan', ws))
    field_rel = {p for p in (t.spec_path, t.plan_path) if p}
    keys = _task_keys(t)
    for d in discover_task_docs(ws):
        if d['rel_path'] in field_rel:
            continue
        if not keys or not _is_related(d, keys):
            continue
        d['related'] = True
        docs.append(d)
    docs.sort(key=lambda x: (0 if x['source'] == 'field' else 1, x['kind'], x.get('rel_path', '')))
    return {'workspace': ws, 'documents': docs}


@router.get('/{task_id}/file')
def get_task_file(task_id: str, path: str = Query(...), db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    ws = (t.workspace or '').strip()
    if not ws:
        raise HTTPException(400, 'task has no workspace')
    base = pathlib.Path(ws).resolve()
    target = (base / path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(400, 'invalid path: escape workspace')
    if not target.is_file():
        raise HTTPException(404, f'file not found: {target}')
    text, truncated = _read_content(target)
    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    return {'path': path, 'content': text, 'truncated': truncated, 'size': size}
