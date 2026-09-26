"""任务文档 doc_paths（kind -> path）与 /doc 白名单泛化回归测试。

背景：原先只有 spec_path / plan_path 两个专有列，/doc 只接受 spec|plan，
但扫描层（DOC_PATTERNS）与前端（DocPanel.KIND_META）早已支持 8 类。
本组测试锁定泛化后的行为，并确保旧字段仍可用。
"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.doc_paths import DOC_KINDS

client = TestClient(app)


def _mk(workspace, **extra):
    payload = {'title': 'doc paths case', 'workspace': str(workspace), 'stage': 'ready'}
    payload.update(extra)
    r = client.post('/api/v1/tasks', json=payload)
    assert r.status_code == 200, r.text
    return r.json()['id']


def _ws(tmp_path, *names):
    ws = tmp_path / 'ws'
    ws.mkdir(exist_ok=True)
    for n in names:
        (ws / n).write_text(f'# {n}', encoding='utf-8')
    return ws


# ── /doc 白名单泛化 ─────────────────────────────────────────────────────────

def test_doc_serves_all_non_legacy_kinds(tmp_path):
    """requirement/test/architecture/api/readme/changelog 均可经 /doc 读取。"""
    names = ['req.md', 'test-plan.md', 'arch.md', 'api-doc.md', 'readme.md', 'changelog.md']
    ws = _ws(tmp_path, *names)
    kinds = ['requirement', 'test', 'architecture', 'api', 'readme', 'changelog']
    paths = dict(zip(kinds, names))

    tid = _mk(ws, doc_paths=paths)
    for kind, name in paths.items():
        r = client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': kind})
        assert r.status_code == 200, f'{kind}: {r.text}'
        assert r.json()['content'].strip() == f'# {name}'
        assert r.json()['missing'] is False


def test_doc_unknown_kind_returns_400_listing_valid_kinds(tmp_path):
    tid = _mk(_ws(tmp_path))
    r = client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': 'bogus'})
    assert r.status_code == 400
    detail = r.json()['detail']
    assert 'kind must be one of' in detail
    for kind in DOC_KINDS:
        assert kind in detail


def test_doc_missing_document_returns_404(tmp_path):
    """kind 合法但任务未设该文档 → 404。"""
    tid = _mk(_ws(tmp_path))
    r = client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': 'spec'})
    assert r.status_code == 404
    assert 'spec' in r.json()['detail']


def test_doc_relative_path_without_workspace_reports_missing_not_error(tmp_path):
    """workspace 为空且路径为相对 → 200 但 missing=True（不是 HTTP 错误）。"""
    r = client.post('/api/v1/tasks', json={
        'title': 'no ws', 'stage': 'ready', 'doc_paths': {'spec': 'rel/spec.md'},
    })
    tid = r.json()['id']
    r = client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': 'spec'})
    assert r.status_code == 200
    body = r.json()
    assert body['missing'] is True
    assert '无法确定基准目录' in body['content']


# ── /documents 纳入 doc_paths ───────────────────────────────────────────────

def test_documents_lists_doc_paths_entries_as_field(tmp_path):
    ws = _ws(tmp_path, 'req.md', 'readme.md')
    tid = _mk(ws, doc_paths={'requirement': 'req.md', 'readme': 'readme.md'})

    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    kinds = {d['kind'] for d in docs}
    assert {'requirement', 'readme'} <= kinds
    assert all(d['source'] == 'field' for d in docs)


def test_documents_carries_lifecycle_status(tmp_path):
    """清单条目带上生命周期状态：有生命周期的带 {state}，无生命周期的为 None。

    此前 /documents 走 _rel_entry，不带 doc_statuses —— 前端拿到清单也无法渲染
    状态徽标（8 类文档生命周期在 Web UI 上不可见）。
    """
    ws = _ws(tmp_path, 'readme.md')
    tid = _mk(ws, doc_paths={'readme': 'readme.md'})

    r = client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': 'spec'},
                   json={'content': '# spec\n'})
    assert r.status_code == 200, r.text
    assert r.json()['status']['state'] == 'draft'

    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    by_kind = {d['kind']: d for d in docs}
    assert by_kind['spec']['status']['state'] == 'draft'
    # readme 无生命周期 → status 为 None（而非缺失该键）
    assert 'status' in by_kind['readme']
    assert by_kind['readme']['status'] is None


def test_legacy_spec_path_still_served_and_listed(tmp_path):
    """只用旧 spec_path 的任务：仍可 /doc 读取，且在 /documents 中为 field。"""
    ws = _ws(tmp_path, 'my-spec.md')
    tid = _mk(ws, spec_path='my-spec.md')

    r = client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': 'spec'})
    assert r.status_code == 200
    assert r.json()['content'].strip() == '# my-spec.md'

    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    hit = next(d for d in docs if d['kind'] == 'spec')
    assert hit['source'] == 'field' and hit['rel_path'] == 'my-spec.md'


# ── 写入：create / patch 与旧字段同步 ───────────────────────────────────────

def test_create_merges_doc_paths_and_legacy_fields(tmp_path):
    ws = _ws(tmp_path, 'a.md', 'b.md')
    tid = _mk(ws, doc_paths={'requirement': 'a.md'}, spec_path='b.md')

    detail = client.get(f'/api/v1/tasks/{tid}').json()
    assert detail['doc_paths'] == {'requirement': 'a.md', 'spec': 'b.md'}
    assert detail['spec_path'] == 'b.md'


def test_patch_doc_paths_clears_kind_with_empty_string(tmp_path):
    ws = _ws(tmp_path, 'req.md', 'readme.md')
    tid = _mk(ws, doc_paths={'requirement': 'req.md', 'readme': 'readme.md'})

    detail = client.patch(f'/api/v1/tasks/{tid}', json={
        'doc_paths': {'requirement': ''},
    }).json()
    assert detail['doc_paths'] == {'readme': 'readme.md'}


def test_patch_legacy_spec_path_syncs_into_doc_paths(tmp_path):
    ws = _ws(tmp_path, 's.md')
    tid = _mk(ws)

    detail = client.patch(f'/api/v1/tasks/{tid}', json={'spec_path': 's.md'}).json()
    assert detail['doc_paths'].get('spec') == 's.md'
    assert detail['spec_path'] == 's.md'

    # 反向：清空旧字段也应从 doc_paths 移除
    detail = client.patch(f'/api/v1/tasks/{tid}', json={'spec_path': ''}).json()
    assert 'spec' not in detail['doc_paths']
    assert detail['spec_path'] == ''


def test_doc_paths_ignores_unknown_kinds(tmp_path):
    ws = _ws(tmp_path)
    tid = _mk(ws, doc_paths={'spec': 's.md', 'not_a_kind': 'x.md'})
    detail = client.get(f'/api/v1/tasks/{tid}').json()
    assert detail['doc_paths'] == {'spec': 's.md'}


# ── 阶段门槛接受 doc_paths 并与旧字段同步 ───────────────────────────────────

def _discussion(tid):
    r = client.post(f'/api/v1/tasks/{tid}/discussions',
                    json={'topic': 'kickoff', 'conclusions': 'ok'})
    assert r.status_code == 200, r.text


def test_stage_design_accepts_doc_paths_and_syncs_legacy(tmp_path):
    """design 阶段可用 doc_paths 提供 spec，并回写 spec_path 旧列。"""
    ws = _ws(tmp_path, 's.md')
    tid = _mk(ws, stage='brainstorming')
    _discussion(tid)

    r = client.post(f'/api/v1/tasks/{tid}/stage',
                    json={'target_stage': 'design', 'doc_paths': {'spec': 's.md'}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['doc_paths'] == {'spec': 's.md'}
    assert body['spec_path'] == 's.md'


def test_stage_planning_syncs_doc_paths_from_legacy(tmp_path):
    ws = _ws(tmp_path, 'p.md')
    tid = _mk(ws, stage='brainstorming')
    _discussion(tid)
    client.post(f'/api/v1/tasks/{tid}/stage',
                json={'target_stage': 'design', 'spec_path': 's.md'})

    r = client.post(f'/api/v1/tasks/{tid}/stage',
                    json={'target_stage': 'planning', 'plan_path': 'p.md'})
    assert r.status_code == 200, r.text
    assert r.json()['doc_paths'] == {'spec': 's.md', 'plan': 'p.md'}


def test_stage_design_still_requires_spec_with_discussion(tmp_path):
    """保留原有门槛：有讨论但无 spec 仍 422。"""
    tid = _mk(_ws(tmp_path), stage='brainstorming')
    _discussion(tid)
    r = client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'design'})
    assert r.status_code == 422
    assert 'spec_path' in r.json()['detail']


# ── review 类型（审查报告） ──────────────────────────────────────────────────

def test_review_kind_readable(tmp_path):
    ws = _ws(tmp_path, 'code-review-x.md')
    tid = _mk(ws, title='code review case', doc_paths={'review': 'code-review-x.md'})

    r = client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': 'review'})
    assert r.status_code == 200, r.text
    assert r.json()['content'].strip() == '# code-review-x.md'


def test_review_kind_discovered_by_keyword(tmp_path):
    """文件名含 review / 审查 / 评审 的 .md 会被发现为 review 类型。"""
    ws = _ws(tmp_path, 'review-alpha.md')
    tid = _mk(ws, title='alpha review')

    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    hit = next((d for d in docs if d['rel_path'] == 'review-alpha.md'), None)
    assert hit is not None, f'未被发现：{docs}'
    assert hit['kind'] == 'review'


def test_done_gate_accepts_review_document(tmp_path):
    """done 门槛可由 doc_paths['review'] 满足（无需 review_result 文本）。"""
    ws = _ws(tmp_path, 'review-report.md')
    tid = _mk(ws, stage='review')

    r = client.post(f'/api/v1/tasks/{tid}/stage',
                    json={'target_stage': 'done', 'doc_paths': {'review': 'review-report.md'}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['doc_paths'].get('review') == 'review-report.md'
    assert 'review-report.md' in body['review_result']


def test_done_gate_still_accepts_review_text(tmp_path):
    """向后兼容：仍可用 review_result 文本满足门槛。"""
    tid = _mk(_ws(tmp_path), stage='review')

    r = client.post(f'/api/v1/tasks/{tid}/stage',
                    json={'target_stage': 'done', 'review_result': 'approved'})
    assert r.status_code == 200, r.text
    assert r.json()['review_result'] == 'approved'


def test_done_gate_requires_review_text_or_document(tmp_path):
    """文本与审查文档都没有 → 仍 422。"""
    tid = _mk(_ws(tmp_path), stage='review')

    r = client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'done'})
    assert r.status_code == 422
    assert 'review' in r.json()['detail']


# ── /raw 原始文件（文档内嵌图片） ───────────────────────────────────────────

_PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32 + b'fake-image-bytes'


def test_raw_serves_binary_with_content_type(tmp_path):
    """二进制安全：原样返回字节 + 正确的 content-type。"""
    ws = _ws(tmp_path)
    (ws / 'docs').mkdir()
    (ws / 'docs' / 'shot.png').write_bytes(_PNG)

    tid = _mk(ws, doc_paths={'spec': 'docs/spec.md'})
    r = client.get(f'/api/v1/tasks/{tid}/raw', params={'path': 'docs/shot.png'})
    assert r.status_code == 200, r.text
    assert r.content == _PNG
    assert r.headers['content-type'].startswith('image/png')


def test_raw_rejects_path_escape(tmp_path):
    tid = _mk(_ws(tmp_path))
    r = client.get(f'/api/v1/tasks/{tid}/raw', params={'path': '../../../etc/passwd'})
    assert r.status_code == 400
    assert 'escape workspace' in r.json()['detail']


def test_raw_missing_file_and_workspace(tmp_path):
    tid = _mk(_ws(tmp_path))
    r = client.get(f'/api/v1/tasks/{tid}/raw', params={'path': 'nope.png'})
    assert r.status_code == 404

    no_ws = client.post('/api/v1/tasks', json={'title': 'no ws', 'stage': 'ready'}).json()['id']
    r = client.get(f'/api/v1/tasks/{no_ws}/raw', params={'path': 'a.png'})
    assert r.status_code == 400
    assert 'workspace' in r.json()['detail']


def test_documents_entries_carry_dir(tmp_path):
    """每条文档带 dir（workspace 相对目录），供前端解析相对图片地址。"""
    ws = _ws(tmp_path, 'root-req.md')
    (ws / 'docs').mkdir()
    (ws / 'docs' / 'spec-x.md').write_text('# s', encoding='utf-8')

    tid = _mk(ws, title='dir case', doc_paths={'spec': 'docs/spec-x.md'})
    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    by_kind = {d['kind']: d for d in docs}
    assert by_kind['spec']['dir'] == 'docs'


def test_file_reports_binary_instead_of_garbling(tmp_path):
    """二进制文件经 /file 应显式标记，而不是返回乱码文本。"""
    ws = _ws(tmp_path)
    (ws / 'docs').mkdir()
    (ws / 'docs' / 'shot.png').write_bytes(_PNG)

    tid = _mk(ws, doc_paths={'spec': 'docs/spec.md'})
    body = client.get(f'/api/v1/tasks/{tid}/file', params={'path': 'docs/shot.png'}).json()
    assert body['binary'] is True
    assert body['content'] is None
    assert '/raw' in body['hint']


# ── 阶段产出物要求：表驱动 + 端点 ───────────────────────────────────────────

def test_stage_requirements_endpoint_shape():
    r = client.get('/api/v1/tasks/stages/requirements')
    assert r.status_code == 200, r.text
    body = r.json()
    stages = body['stages']
    assert set(stages) == {'brainstorming', 'design', 'planning', 'implementing', 'done'}
    assert stages['brainstorming']['document_kind'] == 'requirement'
    assert stages['brainstorming']['document_kinds'] == ['requirement']
    assert stages['brainstorming']['requires_discussion'] is False
    assert stages['design']['document_kind'] == 'spec'
    assert stages['design']['requires_discussion'] is True
    assert stages['planning']['document_kind'] == 'plan'
    assert stages['planning']['requires_discussion'] is False
    assert stages['implementing']['document_kind'] == 'changelog'
    assert stages['implementing']['document_kinds'] == ['changelog']
    assert stages['done']['document_kind'] == 'review'
    assert stages['done']['accepts_text'] is True
    assert stages['done']['text_field'] == 'review_result'
    assert body['doc_kinds'] == list(DOC_KINDS)


def test_stage_requirements_static_path_not_shadowed():
    """静态路径 /tasks/stages/requirements 不被 /tasks/{task_id} 抢占；后者仍 404。"""
    assert client.get('/api/v1/tasks/stages/requirements').status_code == 200
    assert client.get('/api/v1/tasks/no-such-task').status_code == 404


def test_gate_error_messages_preserved(tmp_path):
    """表驱动重构后错误文案保持不变（含旧字段名，兼容既有调用方）。"""
    ws = _ws(tmp_path, 's.md')
    tid = _mk(ws, stage='brainstorming')
    _discussion(tid)

    r = client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'design'})
    assert r.status_code == 422 and 'spec_path' in r.json()['detail']

    client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'design', 'spec_path': 's.md'})
    r = client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'planning'})
    assert r.status_code == 422 and 'plan_path' in r.json()['detail']

    client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'planning', 'plan_path': 's.md'})
    # ready / review 无门槛；implementing 需 changelog（2026-09-17 新增门槛）
    assert client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'ready'}).status_code == 200
    assert client.post(f'/api/v1/tasks/{tid}/stage',
                       json={'target_stage': 'implementing',
                             'doc_paths': {'changelog': 'docs/changelog.md'}}).status_code == 200
    assert client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'review'}).status_code == 200
    r = client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'done'})
    assert r.status_code == 422
    assert 'review_result' in r.json()['detail'] and 'review document' in r.json()['detail']


# ── 文档写入端点 PUT /tasks/{id}/doc ────────────────────────────────────────

def _put_doc(tid, kind, **body):
    return client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': kind}, json=body)


def test_write_doc_creates_file_and_registers(tmp_path):
    """写入即落盘 + 登记 doc_paths，且能与 /doc 读回一致。"""
    ws = _ws(tmp_path)
    tid = _mk(ws)

    r = _put_doc(tid, 'spec', content='# 设计\n正文')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['kind'] == 'spec' and body['path'] == f'docs/taskhub/{tid}/spec.md'
    assert body['created'] is True and body['mode'] == 'overwrite'
    assert body['size'] == len('# 设计\n正文'.encode('utf-8'))
    # spec 有生命周期（doc_lifecycle.py）：首次写入自动落初始状态 draft
    assert body['status'] == {'state': 'draft', 'note': 'auto',
                              'at': body['status']['at']}
    assert (ws / 'docs' / 'taskhub' / tid / 'spec.md').read_text(encoding='utf-8') == '# 设计\n正文'

    assert client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': 'spec'}).json()['content'] == '# 设计\n正文'
    detail = client.get(f'/api/v1/tasks/{tid}').json()
    assert detail['doc_paths'] == {'spec': f'docs/taskhub/{tid}/spec.md'}


def test_write_doc_default_path_per_kind(tmp_path):
    ws = _ws(tmp_path)
    tid = _mk(ws)
    assert (_put_doc(tid, 'requirement', content='# 需求').json()['path']
            == f'docs/taskhub/{tid}/requirement.md')
    assert (_put_doc(tid, 'review', content='# 审查').json()['path']
            == f'docs/taskhub/{tid}/review.md')
    assert (ws / 'docs' / 'taskhub' / tid / 'requirement.md').is_file()
    assert (ws / 'docs' / 'taskhub' / tid / 'review.md').is_file()


def test_write_doc_honours_explicit_and_existing_path(tmp_path):
    """显式 path 生效；再次写入时缺省沿用已登记路径。"""
    ws = _ws(tmp_path)
    tid = _mk(ws)
    assert _put_doc(tid, 'plan', content='p1', path='notes/plan-a.md').json()['path'] == 'notes/plan-a.md'
    # 不传 path → 沿用 notes/plan-a.md
    r = _put_doc(tid, 'plan', content='p2')
    assert r.json()['path'] == 'notes/plan-a.md'
    assert (ws / 'notes' / 'plan-a.md').read_text(encoding='utf-8') == 'p2'


def test_write_doc_overwrite_guard(tmp_path):
    ws = _ws(tmp_path)
    tid = _mk(ws)
    assert _put_doc(tid, 'spec', content='v1').json()['created'] is True

    r = _put_doc(tid, 'spec', content='v2', overwrite=False)
    assert r.status_code == 409
    assert (ws / 'docs' / 'taskhub' / tid / 'spec.md').read_text(encoding='utf-8') == 'v1'

    r = _put_doc(tid, 'spec', content='v3')
    assert r.status_code == 200 and r.json()['created'] is False
    assert (ws / 'docs' / 'taskhub' / tid / 'spec.md').read_text(encoding='utf-8') == 'v3'


def test_write_doc_validation_errors(tmp_path):
    ws = _ws(tmp_path)
    tid = _mk(ws)

    assert _put_doc(tid, 'bogus', content='x').status_code == 400
    r = _put_doc(tid, 'spec', content='x', path='../escape.md')
    assert r.status_code == 400 and 'escape workspace' in r.json()['detail']
    assert _put_doc(tid, 'spec').status_code == 422

    no_ws = client.post('/api/v1/tasks', json={'title': 'no ws'}).json()['id']
    assert _put_doc(no_ws, 'spec', content='x').status_code == 400


def test_write_doc_spec_syncs_legacy_field(tmp_path):
    ws = _ws(tmp_path)
    tid = _mk(ws)
    _put_doc(tid, 'spec', content='# s')
    detail = client.get(f'/api/v1/tasks/{tid}').json()
    assert detail['doc_paths']['spec'] == f'docs/taskhub/{tid}/spec.md'
    assert detail['spec_path'] == f'docs/taskhub/{tid}/spec.md'


def test_write_then_advance_design_without_workspace_doc(tmp_path):
    """写入的文档自动落位 draft 生命周期；未 approved 不能进 design，需先审或 force。"""
    ws = _ws(tmp_path)
    tid = _mk(ws, stage='brainstorming')
    _discussion(tid)

    r = _put_doc(tid, 'spec', content='# spec')
    assert r.status_code == 200
    assert (r.json().get('status') or {}).get('state') == 'draft'

    # draft 契约不能进 design（生命周期门控，2026-09-18 新增）
    r = client.post(f'/api/v1/tasks/{tid}/stage', json={'target_stage': 'design'})
    assert r.status_code == 422
    assert any(g['kind'] == 'spec' for g in r.json()['detail']['gate'])

    # force 可绕过（留痕）；或先 set_doc_status approved 再进
    r = client.post(f'/api/v1/tasks/{tid}/stage',
                    json={'target_stage': 'design', 'force': True})
    assert r.status_code == 200, r.text
    assert r.json()['spec_path'] == f'docs/taskhub/{tid}/spec.md'


# ── 写入：append 模式 ──────────────────────────────────────────────────────

def test_write_doc_append_creates_then_appends_with_newline(tmp_path):
    """append：不存在则新建；已存在则换行分隔追加。"""
    ws = _ws(tmp_path)
    tid = _mk(ws)

    r = _put_doc(tid, 'changelog', content='- 第一条', mode='append')
    assert r.status_code == 200, r.text
    assert r.json()['created'] is True and r.json()['mode'] == 'append'

    r = _put_doc(tid, 'changelog', content='- 第二条', mode='append')
    assert r.json()['created'] is False
    # 原文没有结尾换行 → 自动补一个，避免粘成一行
    assert (ws / 'docs' / 'taskhub' / tid / 'changelog.md').read_text(encoding='utf-8') == '- 第一条\n- 第二条'


def test_write_doc_append_no_extra_newline_when_already_terminated(tmp_path):
    ws = _ws(tmp_path)
    tid = _mk(ws)
    _put_doc(tid, 'changelog', content='# 标题\n', mode='overwrite')
    _put_doc(tid, 'changelog', content='- 条目', mode='append')
    assert (ws / 'docs' / 'taskhub' / tid / 'changelog.md').read_text(encoding='utf-8') == '# 标题\n- 条目'


def test_write_doc_append_rejects_binary_target(tmp_path):
    ws = _ws(tmp_path)
    (ws / 'docs').mkdir()
    (ws / 'docs' / 'bin.md').write_bytes(b'\x00\x01binary')
    tid = _mk(ws)

    r = _put_doc(tid, 'readme', content='x', path='docs/bin.md', mode='append')
    assert r.status_code == 422
    assert 'binary' in r.json()['detail']


def test_write_doc_invalid_mode(tmp_path):
    ws = _ws(tmp_path)
    tid = _mk(ws)
    r = _put_doc(tid, 'spec', content='x', mode='bogus')
    assert r.status_code == 422
    assert 'mode must be' in r.json()['detail']


def test_write_doc_append_checks_final_length(tmp_path):
    """长度上限作用于拼接后的最终正文。"""
    ws = _ws(tmp_path)
    tid = _mk(ws)
    _put_doc(tid, 'changelog', content='x' * 100, mode='append')

    r = _put_doc(tid, 'changelog', content='y' * (1_000_000 - 50), mode='append')
    assert r.status_code == 413
    assert '1000150' in r.json()['detail'] or 'too large' in r.json()['detail']


def test_write_doc_default_mode_is_overwrite(tmp_path):
    ws = _ws(tmp_path)
    tid = _mk(ws)
    _put_doc(tid, 'spec', content='v1')
    assert _put_doc(tid, 'spec', content='v2').json()['mode'] == 'overwrite'
    assert (ws / 'docs' / 'taskhub' / tid / 'spec.md').read_text(encoding='utf-8') == 'v2'


# ── task.deliverables / task.files 纳入文档清单 ──────────────────────────────

def test_documents_lists_deliverables_and_files_with_source_and_exists(tmp_path):
    """deliverables / files 作为 source=deliverable / file 进入清单，并带 exists 标志。"""
    ws = _ws(tmp_path)
    (ws / 'src.py').write_text('# src', encoding='utf-8')
    (ws / 'out').mkdir()
    (ws / 'out' / 'report.pdf').write_bytes(_PNG)          # 存在
    # deliverables 中放一个磁盘上不存在的路径，校验 exists=False
    tid = _mk(ws, deliverables=['out/report.pdf', 'missing/out.zip'], files=['src.py'])

    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    by_path = {d['rel_path']: d for d in docs}

    deliv = by_path['out/report.pdf']
    assert deliv['source'] == 'deliverable' and deliv['kind'] == 'deliverable'
    assert deliv['exists'] is True

    missing_deliv = by_path['missing/out.zip']
    assert missing_deliv['source'] == 'deliverable'
    assert missing_deliv['exists'] is False

    f = by_path['src.py']
    assert f['source'] == 'file' and f['kind'] == 'file'
    assert f['exists'] is True


def test_documents_deliverable_and_file_rank_after_field(tmp_path):
    """排序：field(0) < deliverable(1) < file(2)（discovered 为 3）。"""
    ws = _ws(tmp_path, 'asset.png')
    (ws / 'src.py').write_text('# s', encoding='utf-8')
    tid = _mk(ws, doc_paths={'spec': 'spec.md'},
              deliverables=['asset.png'], files=['src.py'],
              title='deliverable rank case')

    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    idx_spec = next(i for i, d in enumerate(docs) if d['rel_path'] == 'spec.md')
    idx_deliv = next(i for i, d in enumerate(docs) if d['rel_path'] == 'asset.png')
    idx_file = next(i for i, d in enumerate(docs) if d['rel_path'] == 'src.py')
    assert idx_spec < idx_deliv < idx_file


def test_documents_dedupes_path_shared_with_doc_paths(tmp_path):
    """同路径同时出现在 doc_paths 与 deliverables → 只列一次（doc_paths 优先）。"""
    ws = _ws(tmp_path, 'shared.md')
    tid = _mk(ws, doc_paths={'spec': 'shared.md'}, deliverables=['shared.md'])

    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    hits = [d for d in docs if d['rel_path'] == 'shared.md']
    assert len(hits) == 1
    assert hits[0]['source'] == 'field'           # doc_paths 优先，而非 deliverable
    assert hits[0]['kind'] == 'spec'


def test_documents_deliverables_files_exist_none_without_workspace():
    """无 workspace 且路径为相对 → exists 为 None（无法判定）。"""
    r = client.post('/api/v1/tasks', json={
        'title': 'no ws', 'stage': 'ready',
        'deliverables': ['out/report.pdf'], 'files': ['src.py'],
    })
    tid = r.json()['id']
    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    deliv = next(d for d in docs if d['rel_path'] == 'out/report.pdf')
    f = next(d for d in docs if d['rel_path'] == 'src.py')
    assert deliv['source'] == 'deliverable' and deliv['exists'] is None
    assert f['source'] == 'file' and f['exists'] is None


# ── 扩展文档类型（decision/risk/setup/runbook/glossary/userguide/research/
#     retro/milestone/data-model/security/troubleshooting，2026-09-17）────────

EXTENDED_KINDS = [
    'decision', 'risk', 'setup', 'runbook', 'glossary', 'userguide',
    'research', 'retro', 'milestone', 'data-model', 'security', 'troubleshooting',
    'incident',
]


def test_doc_kinds_now_22():
    """白名单从 9 类泛化到完整软件项目文档矩阵（21 类 + incident = 22 类）。"""
    assert len(DOC_KINDS) == 22
    for k in EXTENDED_KINDS:
        assert k in DOC_KINDS


def test_extended_kinds_writable_and_readable(tmp_path):
    """12 个新增 kind 均经 PUT /doc 落盘、登记 doc_paths，并能经 GET /doc 读回。"""
    ws = _ws(tmp_path)  # 不预建文件：让 PUT /doc 自行创建，避免 overwrite=False 触发 409
    tid = _mk(ws, doc_paths={k: f'{k}.md' for k in EXTENDED_KINDS})
    for k in EXTENDED_KINDS:
        r = _put_doc(tid, k, content=f'# {k} 内容')
        assert r.status_code == 200, f'{k}: {r.text}'
        g = client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': k})
        assert g.status_code == 200, f'{k}: {g.text}'
        assert g.json()['content'].strip() == f'# {k} 内容'
        assert g.json()['missing'] is False


def test_extended_kinds_append(tmp_path):
    """新增 kind 同样支持 append 累积模式。"""
    tid = _mk(_ws(tmp_path))
    for k in EXTENDED_KINDS:
        _put_doc(tid, k, content=f'# {k}\n', mode='overwrite')
        r = _put_doc(tid, k, content=f'- 行 {k}', mode='append')
        assert r.status_code == 200, f'{k}: {r.text}'
        g = client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': k})
        assert g.json()['content'].strip().endswith(f'- 行 {k}')


def test_discover_extended_kinds(tmp_path):
    """扫描层能按关键词识别 12 个新增 kind（文件名共享 token 'proj' 以过相关性过滤）。"""
    files = {
        'adr-proj.md': 'decision',
        'risk-proj.md': 'risk',
        'setup-proj.md': 'setup',
        'runbook-proj.md': 'runbook',
        'glossary-proj.md': 'glossary',
        'userguide-proj.md': 'userguide',
        'research-proj.md': 'research',
        'retro-proj.md': 'retro',
        'milestone-proj.md': 'milestone',
        'data-model-proj.md': 'data-model',
        'security-proj.md': 'security',
        'troubleshooting-proj.md': 'troubleshooting',
    }
    ws = _ws(tmp_path, *files.keys())
    tid = _mk(ws, title='proj')
    body = client.get(f'/api/v1/tasks/{tid}/documents').json()
    discovered = {d['rel_path']: d['kind']
                  for d in body['documents'] if d['source'] == 'discovered'}
    for fn, kind in files.items():
        assert discovered.get(fn) == kind, f'{fn}: expected {kind}, got {discovered.get(fn)}'


def test_extended_kinds_rejected_if_unknown_still_400(tmp_path):
    """未知 kind 仍 400，且错误信息列出全部 21 类。"""
    tid = _mk(_ws(tmp_path))
    r = client.get(f'/api/v1/tasks/{tid}/doc', params={'kind': 'bogus'})
    assert r.status_code == 400
    assert 'kind must be one of' in r.json()['detail']
    assert 'troubleshooting' in r.json()['detail']


def test_discover_skips_worktree_dirs(tmp_path):
    """扫描排除 *-worktree / *.worktree：工作区里的同名文档不再进清单。"""
    from mio_taskhub.api.task_documents import discover_task_docs
    ws = tmp_path / 'ws'
    (ws / 'docs').mkdir(parents=True)
    (ws / 'docs' / 'spec.md').write_text('# s', encoding='utf-8')
    wt = ws / 'srm-web-udsp-arch-worktree' / 'docs'
    wt.mkdir(parents=True)
    (wt / 'spec.md').write_text('# dup', encoding='utf-8')
    (wt / 'architecture-overview.md').write_text('# a', encoding='utf-8')
    found = {d['rel_path'] for d in discover_task_docs(str(ws))}
    assert 'docs/spec.md' in found
    assert not any('worktree' in p for p in found), found


def test_documents_have_category_and_counts(tmp_path):
    """清单条目带 category（doc/deliverable/reference）并返回 counts 汇总。"""
    ws = _ws(tmp_path)
    tid = _mk(ws, doc_paths={'spec': 'docs/spec.md'})
    (ws / 'docs').mkdir(exist_ok=True)
    (ws / 'docs' / 'spec.md').write_text('# s', encoding='utf-8')
    r = client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': 'requirement'},
                   json={'content': '# 需求'})
    assert r.status_code == 200, r.text

    body = client.get(f'/api/v1/tasks/{tid}/documents').json()
    cats = {d['kind']: d.get('category') for d in body['documents']}
    assert cats.get('spec') == 'doc'
    assert cats.get('requirement') == 'doc'
    assert set(body['counts']) == {'doc', 'deliverable', 'reference'}
    assert body['counts']['doc'] >= 2
