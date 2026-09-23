import pathlib
import os
import re
import mimetypes
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlmodel import Session

from mio_taskhub.db import get_session
from mio_taskhub.events import emit_event
from mio_taskhub.models import Task
from mio_taskhub.doc_paths import DOC_KINDS, doc_path_of, existing_doc_kinds, merge_doc_paths, sync_legacy_fields

router = APIRouter(prefix="/tasks", tags=["tasks"])


# ── Helpers ─────────────────────────────────────────────────────────────────

def _resolve_doc_path(task, rel, kind):
    p = pathlib.Path(rel)
    if not p.is_absolute():
        base = (task.workspace or '').strip()
        if not base:
            return None, (f'任务未设置 workspace，且 {kind} 文档路径为相对路径「{rel}」，无法确定基准目录。'
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


def _is_binary(p, probe: int = 4096) -> bool:
    """粗略判定二进制：读取头部探测 NUL 字节。"""
    try:
        with open(p, 'rb') as f:
            return b'\x00' in f.read(probe)
    except OSError:
        return False


def _rel_dir(task, rel):
    """文档所在目录相对 workspace 的路径（根目录为 ''）。不在 workspace 内返回 None。

    供前端把 markdown 里的相对图片地址解析成 workspace 相对路径。
    """
    ws = (task.workspace or '').strip()
    if not ws:
        return None
    try:
        base = pathlib.Path(ws).resolve()
        p = pathlib.Path(rel)
        if not p.is_absolute():
            p = base / p
        rel_parent = p.resolve().parent.relative_to(base)
    except (ValueError, OSError):
        return None
    text = str(rel_parent).replace(os.sep, '/')
    return '' if text in ('.', '') else text


def discover_task_docs(workspace: str):
    if not workspace or not os.path.isdir(workspace):
        return []
    SKIP = {'node_modules', 'dist', 'build', '.venv', '.git', '.workbuddy',
            '.memory-backup', '__pycache__', '.idea', '.vscode'}
    DOC_PATTERNS = {
        'spec':    ['spec'],
        'plan':    ['plan'],
        'review':  ['review', '审查', '评审'],
        'requirement': ['requirement', 'req', '需求'],
        'test':    ['test-plan', 'test_plan', 'testing'],
        # research 须先于 architecture：'research' 含 'arch'，否则 'research-*.md' 会被 arch- 误判为 architecture
        'research':      ['research', 'spike', '预研', '调研'],
        'architecture': ['architecture', 'arch-', 'design-doc'],
        'readme':  ['readme'],
        'changelog': ['changelog', 'changes', 'release-notes'],
        'api':     ['api-doc', 'api_spec', 'openapi'],
        # ── 扩展文档类型（与 DOC_KINDS 保持一致，2026-09-17）──
        'decision':      ['decision', 'adr', '决策'],
        'risk':          ['risk', '风险', '隐患'],
        'setup':         ['setup', 'onboarding', '环境', '上手', 'quickstart'],
        'runbook':       ['runbook', 'ops', '运维', '部署', 'deploy'],
        'glossary':      ['glossary', '术语', '词表'],
        'userguide':     ['user-guide', 'userguide', '用户手册', '手册'],
        'retro':         ['retro', 'retrospective', '复盘', '回顾'],
        'milestone':     ['milestone', '里程碑', '发布计划', 'release-plan'],
        'data-model':    ['data-model', 'datamodel', '数据模型', 'schema', '表结构'],
        'security':      ['security', '安全'],
        'incident':      ['incident', '事故'],
        'troubleshooting': ['troubleshooting', 'troubleshoot', '故障', '排查', 'faq'],
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
            out.append({'name': f, 'rel_path': rel, 'kind': kind, 'size': size,
                        'source': 'discovered', 'exists': True,
                        'dir': rel.rsplit('/', 1)[0] if '/' in rel else ''})
    out.sort(key=lambda x: (x['kind'], x['rel_path']))
    return out


# 清单排序优先级：doc_paths → deliverables → files → 自动发现
SOURCE_RANK = {'field': 0, 'deliverable': 1, 'file': 2, 'discovered': 3}


def _doc_fs_path(t, rel):
    """按 /doc、/file 的同一规则解析路径（不强制 workspace 约束）。返回 Path 或 None。

    相对路径按 workspace 解析；绝对路径原样使用；缺 workspace 且路径为相对时返回 None。
    """
    p = pathlib.Path(rel)
    if not p.is_absolute():
        ws = (t.workspace or '').strip()
        if not ws:
            return None
        p = pathlib.Path(ws) / p
    return p.resolve()


def _rel_entry(t, rel, kind, source, dir_=None):
    """构造一条文档清单条目（doc_paths / deliverables / files 共用）。

    `exists` 与读取端规则一致；无法判定（缺 workspace 且路径为相对）时为 None。
    `status` 为该 kind 的生命周期状态（doc_statuses[kind]，形如 {state, at, note}）；
    无生命周期的 kind（如 deliverable/file）或未落状态时为 None。
    """
    p = _doc_fs_path(t, rel)
    exists = p.is_file() if p is not None else None
    size = 0
    if exists:
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
    return {'name': os.path.basename(rel), 'rel_path': rel, 'kind': kind,
            'size': size, 'source': source, 'dir': dir_ or '', 'exists': exists,
            'status': (getattr(t, 'doc_statuses', None) or {}).get(kind)}


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
    for kind in DOC_KINDS:
        p = doc_path_of(t, kind)
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
def get_task_doc(task_id: str, kind: str = Query(...), run_id: str = Query(None),
                 agent: str = Query(None), db: Session = Depends(get_session)):
    """按 kind 读取任务文档正文。

    kind 支持全部 DOC_KINDS（spec/plan/requirement/test/architecture/api/readme/changelog）；
    spec/plan 走旧列或 doc_paths，其余走 doc_paths。

    传 `run_id`（来自 claim）时，本次读取会落一条 **Read Evidence**（绑定该 run，
    含内容指纹）；submit_result 成功路径会校验 required reads 是否齐备。详见
    mio_taskhub/read_evidence.py。
    """
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    if kind not in DOC_KINDS:
        raise HTTPException(400, f'kind must be one of: {", ".join(DOC_KINDS)}')
    rel = doc_path_of(t, kind)
    if not rel:
        raise HTTPException(404, f'task has no {kind} document')
    p, err = _resolve_doc_path(t, rel, kind)
    if err:
        return {'kind': kind, 'path': rel, 'content': err, 'truncated': False, 'missing': True,
                'status': (getattr(t, 'doc_statuses', None) or {}).get(kind)}
    text, truncated = _read_content(p)
    resp = {'kind': kind, 'path': rel,
            'content': (text if text is not None else f'文件不存在：{p}'),
            'truncated': truncated, 'missing': text is None,
            'status': (getattr(t, 'doc_statuses', None) or {}).get(kind)}
    if run_id and text is not None:
        from mio_taskhub.read_evidence import record_read
        resp['read_evidence'] = record_read(db, t, kind, run_id, agent or "")
    return resp


@router.get('/{task_id}/documents')
def list_task_documents(task_id: str, db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    ws = (t.workspace or '').strip()
    docs = []
    seen = set()

    for kind in existing_doc_kinds(t):
        rel = doc_path_of(t, kind)
        if not rel or rel in seen:
            continue
        seen.add(rel)
        docs.append(_rel_entry(t, rel, kind, 'field', _rel_dir(t, rel)))

    # 交付物与相关文件也纳入清单（此前游离在文档体系外）
    for source, paths in (('deliverable', t.deliverables), ('file', t.files)):
        for rel in (paths or []):
            rel = (rel or '').strip()
            if not rel or rel in seen:
                continue
            seen.add(rel)
            docs.append(_rel_entry(t, rel, source, source, _rel_dir(t, rel)))

    keys = _task_keys(t)
    for d in discover_task_docs(ws):
        if d['rel_path'] in seen:
            continue
        if not keys or not _is_related(d, keys):
            continue
        d['related'] = True
        # 注意：扫描发现的文档**不挂生命周期状态**。doc_statuses 是「该 kind 登记
        # 的那一份文档」的状态，而扫描结果的 kind 只是文件名的启发式归类，同一
        # 任务里常有十几份文件都被判成 requirement —— 给它们套同一状态会显示成
        # 一排假「草稿」徽标（2026-09-18 实测 14/14 误标）。
        docs.append(d)

    # 统一响应形状：发现的文档没有 status 键，补 None（而不是缺字段），
    # 让 /documents 的每条条目 schema 一致。
    for d in docs:
        d.setdefault('status', None)

    docs.sort(key=lambda x: (SOURCE_RANK.get(x['source'], 9), x['kind'], x.get('rel_path', '')))
    return {'workspace': ws, 'documents': docs}


def _workspace_base(t):
    """返回 workspace 的绝对基准目录。返回 (base, error)。"""
    ws = (t.workspace or '').strip()
    if not ws:
        return None, 'task has no workspace'
    return pathlib.Path(ws).resolve(), None


def _resolve_within_workspace(base, path):
    """把路径解析为 base 内的目标（**不要求文件已存在**）。返回 (target, rel, error)。

    rel 是相对 workspace 的 POSIX 路径，用于写回 doc_paths。
    """
    p = pathlib.Path(path)
    target = (p if p.is_absolute() else base / p).resolve()
    try:
        rel = target.relative_to(base)
    except ValueError:
        return None, None, 'invalid path: escape workspace'
    return target, str(rel).replace(os.sep, '/'), None


def _resolve_workspace_file(t, path):
    """把请求路径解析为 workspace 内**已存在**的文件。返回 (target, error)。"""
    base, err = _workspace_base(t)
    if err:
        return None, err
    target, _rel, err = _resolve_within_workspace(base, path)
    if err:
        return None, err
    if not target.is_file():
        return None, f'file not found: {target}'
    return target, None


def _file_error_status(err: str) -> int:
    return 404 if err.startswith('file not found') else 400


@router.get('/{task_id}/file')
def get_task_file(task_id: str, path: str = Query(...), db: Session = Depends(get_session)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    target, err = _resolve_workspace_file(t, path)
    if err:
        raise HTTPException(_file_error_status(err), err)
    try:
        size = target.stat().st_size
    except OSError:
        size = 0
    if _is_binary(target):
        return {'path': path, 'content': None, 'binary': True, 'size': size,
                'hint': '二进制文件，无法按文本读取；请改用 /tasks/{id}/raw?path=... 取原始内容'}
    text, truncated = _read_content(target)
    return {'path': path, 'content': text, 'truncated': truncated, 'size': size}


@router.get('/{task_id}/raw')
def get_task_raw_file(task_id: str, path: str = Query(...), db: Session = Depends(get_session)):
    """原样返回 workspace 内的文件（二进制安全）。

    供文档内嵌资源（图片等）引用；同样受 workspace 逃逸防护约束。
    """
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    target, err = _resolve_workspace_file(t, path)
    if err:
        raise HTTPException(_file_error_status(err), err)
    media, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=media or 'application/octet-stream')


# 单次写入的正文长度上限（字符）
MAX_WRITE_CHARS = 1_000_000


@router.put('/{task_id}/doc')
def write_task_doc(task_id: str, body: dict, kind: str = Query(...),
                   db: Session = Depends(get_session)):
    """写入（或追加）任务文档内容，并登记到 doc_paths。

    body 字段：

    - `content`（str，必填）文档正文
    - `path`（str，可选）相对 workspace 的路径；缺省沿用该 kind 已登记路径，
      再缺省则写到 `docs/<kind>.md`
    - `mode`（str，可选）`overwrite`（默认，整篇替换）或 `append`（追加到末尾）
    - `overwrite`（bool，可选）仅 `overwrite` 模式下生效：传 `false` 且文件已存在时返回 409

    `append` 语义：文件不存在则新建；已存在则读回后拼接（原文不以换行结尾会补一个换行）。
    只能写 workspace 内的路径（逃逸返回 400）；长度为**最终**正文长度，超上限 413。
    """
    if kind not in DOC_KINDS:
        raise HTTPException(400, f'kind must be one of: {", ".join(DOC_KINDS)}')
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    base, err = _workspace_base(t)
    if err:
        raise HTTPException(400, err)

    payload = body or {}
    mode = str(payload.get('mode') or 'overwrite').strip().lower()
    if mode not in ('overwrite', 'append'):
        raise HTTPException(422, "mode must be 'overwrite' or 'append'")

    content = payload.get('content')
    if content is None:
        raise HTTPException(422, 'content is required')
    if not isinstance(content, str):
        raise HTTPException(422, 'content must be a string')

    rel = (payload.get('path') or '').strip() or doc_path_of(t, kind) or f'docs/{kind}.md'
    target, rel_norm, err = _resolve_within_workspace(base, rel)
    if err:
        raise HTTPException(400, err)

    existed = target.is_file()
    if existed and mode == 'overwrite' and payload.get('overwrite') is False:
        raise HTTPException(409, f'file already exists: {rel_norm}')

    final = content
    if mode == 'append' and existed:
        if _is_binary(target):
            raise HTTPException(422, f'cannot append to a binary file: {rel_norm}')
        try:
            prev = target.read_text(encoding='utf-8', errors='replace')
        except OSError as e:
            raise HTTPException(500, f'read failed: {e}')
        # 原文不以换行结尾时补一个，避免与追加内容粘在一行
        sep = '' if (not prev or prev.endswith('\n')) else '\n'
        final = prev + sep + content

    if len(final) > MAX_WRITE_CHARS:
        raise HTTPException(413, f'content too large: {len(final)} > {MAX_WRITE_CHARS} chars')

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(final, encoding='utf-8')
    except OSError as e:
        raise HTTPException(500, f'write failed: {e}')

    t.doc_paths = merge_doc_paths(t.doc_paths, {kind: rel_norm})
    sync_legacy_fields(t, kind, doc_path_of(t, kind))
    _auto_init_doc_status(t, kind)
    db.add(t)
    size = len(final.encode('utf-8'))
    emit_event(db, type='task_doc_written', entity='task', entity_id=t.id,
               payload={'kind': kind, 'path': rel_norm, 'created': not existed,
                        'size': size, 'mode': mode})
    db.commit()
    return {'kind': kind, 'path': rel_norm, 'created': not existed,
            'mode': mode, 'size': size,
            'status': (getattr(t, 'doc_statuses', None) or {}).get(kind),
            'quality': _quality_for(t, kind)}


@router.post('/{task_id}/docs/scaffold')
def scaffold_doc_chain(task_id: str, body: dict = None, db: Session = Depends(get_session)):
    """按「软件项目文档链（七件套）」一次性生成文档骨架。

    需求规格→架构设计→模块 Spec→状态模型→接口契约→测试验收→部署运维，
    每份带章节骨架与上下游追溯链接（mio_taskhub/doc_chain.py）。

    body 字段（均可省）：
    - `kinds`（list[str]，可选）只生成链内子集，默认全部 7 份
    - `overwrite`（bool，可选，默认 false）：已存在的文件**永不覆盖**；
      仅登记路径。传 true 时才重置为模板（慎用）。

    返回 `created` / `skipped`（含 reason）与登记后的 `doc_paths`。
    需要任务已设置 workspace。
    """
    from mio_taskhub.doc_chain import DOC_CHAIN, chain_path_of, render_chain
    payload = body or {}
    kinds = payload.get('kinds') or list(DOC_CHAIN)
    if not isinstance(kinds, list) or not kinds:
        raise HTTPException(422, 'kinds must be a non-empty list')
    bad = [k for k in kinds if k not in DOC_CHAIN]
    if bad:
        raise HTTPException(422, f'kinds outside doc chain {list(DOC_CHAIN)}: {bad}')
    overwrite = bool(payload.get('overwrite'))

    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    base, err = _workspace_base(t)
    if err:
        raise HTTPException(400, err)

    created, skipped = [], []
    for kind in kinds:  # 按链路顺序生成，保证上下游链接路径稳定
        rel = chain_path_of(kind, dict(t.doc_paths or {}))
        target, rel_norm, rerr = _resolve_within_workspace(base, rel)
        if rerr:
            raise HTTPException(400, rerr)
        if target.is_file() and not overwrite:
            skipped.append({'kind': kind, 'path': rel_norm, 'reason': 'exists'})
            continue
        content = render_chain(kind, dict(t.doc_paths or {}))
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
        except OSError as e:
            raise HTTPException(500, f'write failed ({kind}): {e}')
        t.doc_paths = merge_doc_paths(t.doc_paths, {kind: rel_norm})
        sync_legacy_fields(t, kind, doc_path_of(t, kind))
        created.append({'kind': kind, 'path': rel_norm})

    if created or overwrite:
        db.add(t)
        for c in created:
            _auto_init_doc_status(t, c['kind'])
        emit_event(db, type='task_doc_scaffolded', entity='task', entity_id=t.id,
                   payload={'created': [c['kind'] for c in created],
                            'skipped': [s['kind'] for s in skipped]})
    db.commit()
    return {'chain': list(DOC_CHAIN), 'created': created, 'skipped': skipped,
            'doc_paths': dict(t.doc_paths or {})}


def _auto_init_doc_status(t: Task, kind: str):
    """文档首次落位时自动置初始状态（有生命周期的 kind）。"""
    from mio_taskhub.doc_lifecycle import initial_state_of
    init = initial_state_of(kind)
    if init is None:
        return
    statuses = dict(getattr(t, 'doc_statuses', None) or {})
    if kind not in statuses:
        statuses[kind] = {'state': init, 'at': datetime.now(timezone.utc).isoformat(), 'note': 'auto'}
        t.doc_statuses = statuses


def _quality_for(t: Task, kind: str):
    """某 kind 的质量报告（无质量规格的 kind 返回 None）。"""
    from mio_taskhub.doc_quality import QUALITY_SPEC, check_content
    if kind not in QUALITY_SPEC:
        return None
    base, err = _workspace_base(t)
    if err:
        return check_content(kind, None)
    rel = doc_path_of(t, kind)
    if not rel:
        return None
    target, _, rerr = _resolve_within_workspace(base, rel)
    if rerr:
        return check_content(kind, None)
    return check_content(kind,
                         target.read_text(encoding='utf-8', errors='replace') if target.is_file() else None,
                         base)

@router.post('/{task_id}/doc/{kind}/status')
def set_doc_status(task_id: str, kind: str, body: dict, db: Session = Depends(get_session)):
    """推进文档生命周期状态（doc_lifecycle.py 状态机，严格向前，不允许回退）。

    body：`state`（必填）+ `note`（可选，如 ADR superseded 的替代编号）。
    未落初始状态时自动先落初始状态再校验转移。
    """
    from mio_taskhub.doc_lifecycle import lifecycle_of, validate_transition
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    lc = lifecycle_of(kind)
    if not lc:
        raise HTTPException(400, f"kind '{kind}' has no document lifecycle")
    state = (body or {}).get('state')
    if not state:
        raise HTTPException(422, 'state is required')

    statuses = dict(getattr(t, 'doc_statuses', None) or {})
    has_doc = bool(doc_path_of(t, kind))
    cur = (statuses.get(kind) or {}).get('state')
    if cur is None and not has_doc:
        raise HTTPException(422, f"task has no '{kind}' document; write it first (PUT /doc or scaffold)")
    err = validate_transition(kind, cur, state)
    if err:
        raise HTTPException(422, err)

    # 质量门控：推进到 review/approved/done 时 errors 必须为 0（force 可绕过但留痕）
    from mio_taskhub.doc_quality import GATED_TARGETS
    quality, forced = None, bool((body or {}).get('force'))
    if state in GATED_TARGETS:
        quality = _quality_for(t, kind)
        if quality and quality['errors'] and not forced:
            raise HTTPException(422, detail={
                'message': f"quality gate: {kind} has {len(quality['errors'])} blocking issue(s); "
                           f"fix them or pass force=true",
                'quality': quality})

    from datetime import datetime as _dt, timezone as _tz
    statuses[kind] = {'state': state, 'at': _dt.now(_tz.utc).isoformat(),
                      'note': (body or {}).get('note') or ''}
    t.doc_statuses = statuses
    db.add(t)
    emit_event(db, type='task_doc_status', entity='task', entity_id=t.id,
               payload={'kind': kind, 'from': cur, 'to': state,
                        'note': (body or {}).get('note') or '',
                        'forced': forced or None,
                        'quality_score': quality and quality['score']})
    db.commit()
    return {'kind': kind, 'status': statuses[kind], 'doc_paths': dict(t.doc_paths or {}),
            'quality': quality}


@router.get('/{task_id}/doc/statuses')
def list_doc_statuses(task_id: str, db: Session = Depends(get_session)):
    """文档生命周期状态总览：各 kind 当前状态 + 合法后继。"""
    from mio_taskhub.doc_lifecycle import DOC_LIFECYCLE, allowed_next, lifecycle_of
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    statuses = dict(getattr(t, 'doc_statuses', None) or {})
    out = {}
    for kind in DOC_KINDS:
        lc = lifecycle_of(kind)
        if not lc:
            continue
        cur = (statuses.get(kind) or {}).get('state')
        out[kind] = {
            'state': cur,
            'at': (statuses.get(kind) or {}).get('at'),
            'note': (statuses.get(kind) or {}).get('note') or '',
            'states': list(lc['states']),
            'allowed_next': allowed_next(kind, cur) if cur else [lc['states'][0]],
            'has_doc': bool(doc_path_of(t, kind)),
        }
    return {'statuses': out, 'lifecycles': {k: v['states'] for k, v in DOC_LIFECYCLE.items()}}


@router.get('/{task_id}/doc/{kind}/revision')
def doc_revision_prompt(task_id: str, kind: str, db: Session = Depends(get_session)):
    """把质量结果反哺成修订指令——写的人（agent/人）拿去直接照做重写。

    无质量问题的文档返回 revision_prompt=null（无需修订）。
    """
    from mio_taskhub.doc_quality import QUALITY_SPEC, check_content, revision_prompt
    if kind not in QUALITY_SPEC:
        raise HTTPException(400, f"kind '{kind}' has no quality spec")
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    q = _quality_for(t, kind)
    if q is None:
        q = check_content(kind, None)
    return {'kind': kind, 'quality': q,
            'revision_prompt': revision_prompt(kind, None, q)}


@router.get('/{task_id}/doc/quality')
def doc_quality_report(task_id: str, db: Session = Depends(get_session)):
    """全链文档质量报告：各文档质量分 + FR↔测试追溯缺口。

    仅覆盖有质量规格的 kind（doc_quality.QUALITY_SPEC，即文档链 7 件套）。
    """
    from mio_taskhub.doc_quality import (QUALITY_SPEC, check_content,
                                         quality_of_file, traceability)
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, 'task not found')
    base, err = _workspace_base(t)
    if err:
        raise HTTPException(400, err)

    docs = {}
    for kind in QUALITY_SPEC:
        rel = doc_path_of(t, kind)
        if not rel:
            docs[kind] = {'registered': False}
            continue
        target, rel_norm, rerr = _resolve_within_workspace(base, rel)
        q = check_content(kind, None) if rerr else quality_of_file(kind, target, base)
        docs[kind] = {'registered': True, 'path': rel_norm, **q}

    # FR-n ↔ 测试矩阵追溯
    def _content(kind):
        rel = doc_path_of(t, kind)
        if not rel:
            return None
        target, _, rerr = _resolve_within_workspace(base, rel)
        if rerr or not target.is_file():
            return None
        return target.read_text(encoding='utf-8', errors='replace')

    req_c, test_c = _content('requirement'), _content('test')
    tra = (traceability(req_c, test_c)
           if (req_c or test_c) else {'fr_total': 0, 'fr_covered': 0, 'fr_uncovered': []})

    scored = [d['score'] for d in docs.values() if d.get('registered')]
    return {'docs': docs,
            'traceability': tra,
            'summary': {'scored_docs': len(scored),
                        'avg_score': round(sum(scored) / len(scored)) if scored else None,
                        'total_errors': sum(len(d.get('errors') or []) for d in docs.values()
                                            if d.get('registered'))}}
