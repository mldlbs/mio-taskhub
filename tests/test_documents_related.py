"""GET /tasks/{id}/documents 相关性匹配回归测试。

核心回归：纯中文标题任务（_tokens 提取不到英文 token → keys 为空）
不得把工作区全部 discovered 文档拉成 related。
"""
import os

from fastapi.testclient import TestClient

from mio_taskhub.main import app

client = TestClient(app)


def _mk_task(payload):
    r = client.post('/api/v1/tasks', json=payload)
    assert r.status_code in (200, 201), r.text
    return r.json()['id']


def test_chinese_title_no_keys_links_nothing(tmp_path):
    """纯中文标题且无 spec/plan 字段 → documents 只能是空，不得全库关联。"""
    ws = tmp_path / 'ws'
    ws.mkdir()
    for name in ('spec-09-video.md', 'plan-11-water.md', 'spec-20-fullness.md'):
        (ws / name).write_text('# x', encoding='utf-8')

    tid = _mk_task({'title': '管网充满度展示', 'workspace': str(ws)})
    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    assert docs == [], f'keys 为空时应不关联任何文档，实际返回 {len(docs)} 条: {docs}'


def test_english_title_still_matches_related(tmp_path):
    """英文 token 命中的 discovered 文档仍应被标记 related。"""
    ws = tmp_path / 'ws2'
    ws.mkdir()
    (ws / 'spec-15-longitudinal-section.md').write_text('# a', encoding='utf-8')
    (ws / 'spec-99-unrelated.md').write_text('# b', encoding='utf-8')

    tid = _mk_task({'title': 'longitudinal section view', 'workspace': str(ws)})
    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    names = [d['name'] for d in docs]
    assert 'spec-15-longitudinal-section.md' in names
    assert 'spec-99-unrelated.md' not in names
    hit = next(d for d in docs if d['name'] == 'spec-15-longitudinal-section.md')
    assert hit['related'] is True


def test_discovered_docs_never_carry_lifecycle_status(tmp_path):
    """扫描发现的文档不得挂生命周期状态（只有登记的那一份才属于某个 kind）。

    doc_statuses 是 kind → 状态，而扫描结果的 kind 只是文件名启发式归类：同一任务
    里常有多份文件都被判成 requirement。若给它们统一套状态，前端会显示成一排假
    「草稿」徽标（2026-09-18 实测某任务 14 份文件全部被标成 requirement/draft）。
    """
    ws = tmp_path / 'ws4'
    ws.mkdir()
    # 三份文件名都命中 requirement 的启发式，但只有 doc_paths 里登记的那份是"真身"
    (ws / 'requirement-main.md').write_text('# a', encoding='utf-8')
    (ws / 'requirement-extra.md').write_text('# b', encoding='utf-8')
    (ws / 'requirement-other.md').write_text('# c', encoding='utf-8')

    tid = _mk_task({
        'title': 'requirement main doc',
        'workspace': str(ws),
        'doc_paths': {'requirement': 'requirement-main.md'},
    })
    # 写入登记文档 → 自动落初始状态 draft
    r = client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': 'requirement'},
                   json={'content': '# a\n', 'path': 'requirement-main.md'})
    assert r.status_code == 200, r.text

    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    with_status = [d['name'] for d in docs if d.get('status')]
    assert with_status == ['requirement-main.md'], with_status
    # 响应形状统一：发现的文档也要有 status 键（值为 None），而不是缺字段
    assert all('status' in d for d in docs), [d['name'] for d in docs if 'status' not in d]


def test_field_docs_kept_when_keys_empty(tmp_path):
    """keys 为空时，字段绑定的 spec/plan 必须原样保留。"""
    ws = tmp_path / 'ws3'
    ws.mkdir()
    spec = ws / 'my-spec.md'
    plan = ws / 'my-plan.md'
    spec.write_text('# s', encoding='utf-8')
    plan.write_text('# p', encoding='utf-8')
    (ws / 'spec-other.md').write_text('# o', encoding='utf-8')

    tid = _mk_task({
        'title': '中文标题无token',
        'workspace': str(ws),
        'spec_path': 'my-spec.md',
        'plan_path': 'my-plan.md',
    })
    docs = client.get(f'/api/v1/tasks/{tid}/documents').json()['documents']
    names = sorted(d['name'] for d in docs)
    assert names == ['my-plan.md', 'my-spec.md'], names
    assert all(d['source'] == 'field' for d in docs)
