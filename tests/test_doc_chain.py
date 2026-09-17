# -*- coding: utf-8 -*-
"""软件项目文档链（七件套）scaffold 回归测试。

POST /tasks/{id}/docs/scaffold：需求规格→架构设计→模块 Spec→状态模型→
接口契约→测试验收→部署运维，一次生成全部骨架；已有文件不覆盖。
"""
import pathlib

from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.doc_chain import DOC_CHAIN

client = TestClient(app)


def _mk(tmp_path, **extra):
    ws = tmp_path / 'ws'
    ws.mkdir(exist_ok=True)
    payload = {'title': 'doc chain case', 'workspace': str(ws)}
    payload.update(extra)
    r = client.post('/api/v1/tasks', json=payload)
    assert r.status_code == 200, r.text
    return r.json()['id'], ws


def test_scaffold_creates_full_chain(tmp_path):
    """默认一次生成 7 份，按链路顺序、内容含骨架与上下游链接、路径全部登记。"""
    tid, ws = _mk(tmp_path)
    r = client.post(f'/api/v1/tasks/{tid}/docs/scaffold', json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['chain'] == list(DOC_CHAIN)
    assert [c['kind'] for c in body['created']] == list(DOC_CHAIN)
    assert body['skipped'] == []

    for kind in DOC_CHAIN:
        p = ws / 'docs' / f'{kind}.md'
        assert p.is_file(), f'{kind} 文件未生成: {p}'
        text = p.read_text(encoding='utf-8')
        assert '软件项目文档链' in text and '# ' in text
        # 每份都登记进了 doc_paths
        assert body['doc_paths'][kind] == f'docs/{kind}.md'
    # 上下游追溯链接：requirement 无上游、runbook 无下游、中间件双向
    req = (ws / 'docs' / 'requirement.md').read_text(encoding='utf-8')
    assert '上游：—' in req and '下游：[架构设计](docs/architecture.md)' in req
    runbook = (ws / 'docs' / 'runbook.md').read_text(encoding='utf-8')
    assert '下游：—' in runbook and '上游：[测试验收](docs/test.md)' in runbook
    spec = (ws / 'docs' / 'spec.md').read_text(encoding='utf-8')
    assert '上游：[架构设计](docs/architecture.md)' in spec
    assert '下游：[状态模型](docs/data-model.md)' in spec


def test_scaffold_never_overwrites_existing(tmp_path):
    """第二次调用全部 skipped，文件内容不变；overwrite=true 才重置模板。"""
    tid, ws = _mk(tmp_path)
    r1 = client.post(f'/api/v1/tasks/{tid}/docs/scaffold', json={})
    assert r1.status_code == 200
    spec_file = ws / 'docs' / 'spec.md'
    user_content = '# 我自己的 Spec（模板不该覆盖我）'
    spec_file.write_text(user_content, encoding='utf-8')

    r2 = client.post(f'/api/v1/tasks/{tid}/docs/scaffold', json={})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2['created'] == []
    assert [s['kind'] for s in body2['skipped']] == list(DOC_CHAIN)
    assert spec_file.read_text(encoding='utf-8') == user_content

    r3 = client.post(f'/api/v1/tasks/{tid}/docs/scaffold',
                     json={'kinds': ['spec'], 'overwrite': True})
    assert r3.status_code == 200
    assert '软件项目文档链' in spec_file.read_text(encoding='utf-8')


def test_scaffold_subset_and_validation(tmp_path):
    """kinds 子集只生成对应文档；链外 kind 返回 422；无 workspace 返回 400。"""
    tid, ws = _mk(tmp_path)
    r = client.post(f'/api/v1/tasks/{tid}/docs/scaffold', json={'kinds': ['api', 'test']})
    assert r.status_code == 200
    body = r.json()
    assert [c['kind'] for c in body['created']] == ['api', 'test']
    assert (ws / 'docs' / 'api.md').is_file()
    assert not (ws / 'docs' / 'requirement.md').exists()

    bad = client.post(f'/api/v1/tasks/{tid}/docs/scaffold', json={'kinds': ['readme']})
    assert bad.status_code == 422 and 'readme' in bad.json()['detail']

    # 无 workspace 的任务
    r2 = client.post('/api/v1/tasks', json={'title': 'no-ws'})
    tid2 = r2.json()['id']
    r3 = client.post(f'/api/v1/tasks/{tid2}/docs/scaffold', json={})
    assert r3.status_code == 400
