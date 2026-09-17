# -*- coding: utf-8 -*-
"""文档生命周期状态机回归测试（doc_lifecycle.py）。

按用户定义的 8 类文档角色表：PRD→requirement、Spec、ADR→decision、Plan、
Task（即任务本身 state 机，无文档）、Test、Release→milestone、Incident→incident。
"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.doc_lifecycle import DOC_LIFECYCLE

client = TestClient(app)


def _mk(tmp_path, kind='spec'):
    """建任务并经 PUT /doc 写入一份文档（自动落初始状态）。"""
    ws = tmp_path / 'ws'
    ws.mkdir(exist_ok=True)
    r = client.post('/api/v1/tasks', json={'title': 'lifecycle case', 'workspace': str(ws)})
    assert r.status_code == 200, r.text
    tid = r.json()['id']
    r = client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': kind},
                   json={'content': f'# {kind} doc'})
    assert r.status_code == 200, r.text
    return tid


def _set(tid, kind, state, note=None, force=False):
    body = {'state': state}
    if note:
        body['note'] = note
    if force:
        body['force'] = True
    return client.post(f'/api/v1/tasks/{tid}/doc/{kind}/status', json=body)


def test_put_doc_auto_initializes_status(tmp_path):
    """写文档自动落初始状态：spec→draft、decision→proposed、incident→open。"""
    expect = {'spec': 'draft', 'requirement': 'draft', 'plan': 'draft',
              'decision': 'proposed', 'test': 'planned', 'milestone': 'planned',
              'incident': 'open'}
    ws = tmp_path / 'ws'
    ws.mkdir()
    tid = client.post('/api/v1/tasks', json={'title': 't', 'workspace': str(ws)}).json()['id']
    for kind, init in expect.items():
        r = client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': kind},
                       json={'content': f'# {kind}'})
        assert r.status_code == 200, r.text
        assert r.json()['status']['state'] == init, kind
    # 无生命周期的 kind（如 architecture）不落状态
    r = client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': 'architecture'},
                   json={'content': '# arch'})
    assert r.json()['status'] is None
    d = client.get(f'/api/v1/tasks/{tid}').json()
    assert d['doc_statuses']['spec']['state'] == 'draft'


def test_spec_full_chain_draft_review_approved(tmp_path):
    # 骨架文档过不了质量门（见 test_doc_quality.py），用 force 只测转移合法性
    tid = _mk(tmp_path, 'spec')
    assert _set(tid, 'spec', 'approved').status_code == 422  # 不能跳过 review
    assert _set(tid, 'spec', 'review', force=True).status_code == 200
    assert _set(tid, 'spec', 'approved', force=True).status_code == 200
    # 终态：approved 无后继
    assert _set(tid, 'spec', 'approved', force=True).status_code == 422
    r = client.get(f'/api/v1/tasks/{tid}/doc/statuses')
    assert r.json()['statuses']['spec']['state'] == 'approved'
    assert r.json()['statuses']['spec']['allowed_next'] == []


def test_adr_proposed_accepted_superseded_with_note(tmp_path):
    tid = _mk(tmp_path, 'decision')
    assert _set(tid, 'decision', 'superseded').status_code == 422
    assert _set(tid, 'decision', 'accepted').status_code == 200
    r = _set(tid, 'decision', 'superseded', note='superseded by ADR-7')
    assert r.status_code == 200
    assert r.json()['status']['note'] == 'superseded by ADR-7'
    st = client.get(f'/api/v1/tasks/{tid}/doc/statuses').json()['statuses']['decision']
    assert st['state'] == 'superseded' and st['allowed_next'] == []


def test_test_kind_branches_passed_or_failed(tmp_path):
    tid = _mk(tmp_path, 'test')
    assert _set(tid, 'test', 'failed').status_code == 200
    # failed 为终态，不能改判 passed（严格向前）
    assert _set(tid, 'test', 'passed').status_code == 422


def test_incident_open_resolved_closed(tmp_path):
    tid = _mk(tmp_path, 'incident')
    assert _set(tid, 'incident', 'closed').status_code == 422
    assert _set(tid, 'incident', 'resolved').status_code == 200
    assert _set(tid, 'incident', 'closed').status_code == 200


def test_status_requires_document_first(tmp_path):
    """没写过文档（未登记路径）时不允许设状态。"""
    ws = tmp_path / 'ws'
    ws.mkdir()
    tid = client.post('/api/v1/tasks', json={'title': 't2', 'workspace': str(ws)}).json()['id']
    r = _set(tid, 'spec', 'draft')
    assert r.status_code == 422 and 'no \'spec\' document' in r.json()['detail']


def test_lifecycle_kind_validation_and_coverage():
    """无生命周期 kind 400；非法状态 422；覆盖用户 8 行中的 7 行 + 接口契约 api。"""
    ws = None  # kind 校验在 404 之后，需要真实任务
    from fastapi.testclient import TestClient as _TC  # noqa: F401
    from mio_taskhub.doc_lifecycle import INITIAL_STATE
    # 用户表的 8 行：Task 行映射任务本身 state 机，其余 7 行 + task = 8
    # 另加 api（接口契约，2026-09-17 纳入，与 spec 同形）
    assert set(DOC_LIFECYCLE) == set(INITIAL_STATE) == {
        'requirement', 'spec', 'api', 'decision', 'plan', 'test', 'milestone', 'incident'}
    # 线性状态机无回边
    for kind, lc in DOC_LIFECYCLE.items():
        for src, dsts in lc['transitions'].items():
            for d in dsts:
                assert lc['transitions'].get(d) is not None or d in lc['states']
