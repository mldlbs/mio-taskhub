# -*- coding: utf-8 -*-
"""文档质量校验回归测试（doc_quality.py）。

写阶段拦空心文档：结构完整性 / 模板注释残留 / 必需表格数据行 / 链接有效 /
FR↔测试追溯；质量门控在 set_doc_status（review/approved/done，force 可绕过）。
"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.doc_quality import check_content, traceability

client = TestClient(app)

GOOD_SPEC = """# Spec
> 文档链
## 1. 模块职责与边界
做登录。
## 2. 详细设计
用 JWT。
"""


def _mk_with_doc(tmp_path, kind, content):
    ws = tmp_path / 'ws'
    ws.mkdir(exist_ok=True)
    tid = client.post('/api/v1/tasks',
                      json={'title': 'q', 'workspace': str(ws)}).json()['id']
    r = client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': kind},
                   json={'content': content})
    assert r.status_code == 200, r.text
    return tid, ws


def test_check_content_template_has_errors_good_content_passes():
    """模板原样 → errors（必需章节残留指引注释）；填好 → 100 分无 error。"""
    from mio_taskhub.doc_chain import render_chain
    tpl = render_chain('spec')
    q = check_content('spec', tpl)
    assert q['errors'], '模板必然含未填章节'
    assert q['score'] < 100
    assert any('未填写' in e for e in q['errors'])

    good = check_content('spec', GOOD_SPEC)
    assert good['errors'] == []
    assert good['score'] == 100


def test_check_content_missing_section_and_empty_table():
    bad = "# Spec\n## 模块职责与边界\nok\n## 详细设计\n| 列 | 列 |\n|---|---|\n"
    q = check_content('spec', bad)
    # 详细设计无指引注释但表格 0 数据行 — spec 无必需表格要求，故无 error；构造缺章节：
    worse = "# Spec\n## 模块职责与边界\nok\n"
    q2 = check_content('spec', worse)
    assert any('缺少必需章节「详细设计」' in e for e in q2['errors'])
    # requirement 的功能需求表必须有数据行
    bad_req = "# Req\n## 功能需求\n<!-- 写需求 -->\n"
    q3 = check_content('requirement', bad_req)
    assert any('缺少必需章节' in e or '未填写' in e for e in q3['errors'])


def test_broken_link_warns():
    content = GOOD_SPEC + "\n参考 [架构](docs/architecture.md)\n"
    import pathlib, tempfile
    ws = pathlib.Path(tempfile.mkdtemp())
    q = check_content('spec', content, ws)
    assert any('architecture' in w for w in q['warns'])


def test_put_doc_returns_quality(tmp_path):
    """PUT /doc 响应带 quality（有规格 kind），无规格 kind 为 None。"""
    tid, _ = _mk_with_doc(tmp_path, 'spec', GOOD_SPEC)
    r = client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': 'spec'},
                   json={'content': GOOD_SPEC})
    assert r.status_code == 200, r.text
    assert r.json()['quality']['score'] == 100
    r2 = client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': 'plan'},
                    json={'content': '# plan', 'overwrite': False})
    assert r2.status_code == 200, r2.text
    assert r2.json()['quality'] is None  # plan 有生命周期但无质量规格


def test_status_gate_blocks_and_force(tmp_path):
    """draft→review 空心文档被 422；填好后放行；force 绕过留痕。"""
    tid, _ = _mk_with_doc(tmp_path, 'spec', '占位 <!-- 没写 -->')
    r = client.post(f'/api/v1/tasks/{tid}/doc/spec/status', json={'state': 'review'})
    assert r.status_code == 422
    assert 'quality gate' in str(r.json()['detail'])
    # force 绕过（留痕）
    r2 = client.post(f'/api/v1/tasks/{tid}/doc/spec/status',
                     json={'state': 'review', 'force': True})
    assert r2.status_code == 200, r2.text
    # 已 review → approved 同样有门（还是那份空心文档）
    r3 = client.post(f'/api/v1/tasks/{tid}/doc/spec/status', json={'state': 'approved'})
    assert r3.status_code == 422
    r4 = client.post(f'/api/v1/tasks/{tid}/doc/spec/status',
                     json={'state': 'approved', 'force': True})
    assert r4.status_code == 200


def test_status_gate_passes_quality_doc(tmp_path):
    tid, _ = _mk_with_doc(tmp_path, 'spec', GOOD_SPEC)
    assert client.post(f'/api/v1/tasks/{tid}/doc/spec/status',
                       json={'state': 'review'}).status_code == 200
    assert client.post(f'/api/v1/tasks/{tid}/doc/spec/status',
                       json={'state': 'approved'}).status_code == 200


def test_quality_report_endpoint_and_traceability(tmp_path):
    """报告端点：未登记 kind 标 registered=False；FR 未被测试引用进 fr_uncovered。"""
    ws = tmp_path / 'ws'
    ws.mkdir()
    tid = client.post('/api/v1/tasks', json={'title': 'q2', 'workspace': str(ws)}).json()['id']
    client.post(f'/api/v1/tasks/{tid}/docs/scaffold', json={})
    client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': 'test'},
               json={'content': '# Test\n## 用例清单\n| 用例 | 需求 |\n|---|---|\n| TC-1 | FR-1 |\n'})
    r = client.get(f'/api/v1/tasks/{tid}/doc/quality')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['docs']['spec']['registered'] is True
    assert body['docs']['spec']['errors'], 'scaffold 模板未填应有 error'
    # requirement 仍是模板（无 FR-1 可引用），但 test 引用了 FR-1 —
    # requirement 未写 FR 时 fr_total=0；写上 FR-1 后 covered
    client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': 'requirement'},
               json={'content': '# Req\n## 功能需求\n| 编号 | 需求 |\n|---|---|\n| FR-1 | 登录 |\n',
                     'overwrite': True})
    body2 = client.get(f'/api/v1/tasks/{tid}/doc/quality').json()
    assert body2['traceability']['fr_total'] == 1
    assert body2['traceability']['fr_covered'] == 1
    assert body2['traceability']['fr_uncovered'] == []


def test_traceability_helper():
    t = traceability('FR-1 FR-2 FR-3', '用例引用 FR-1 和 FR-2')
    assert t['fr_total'] == 3 and t['fr_covered'] == 2
    assert t['fr_uncovered'] == ['FR-3']


def test_issues_carry_fix_hints_and_revision_prompt(tmp_path):
    """反哺闭环：问题带修法提示；revision 端点产出可执行修订指令。"""
    from mio_taskhub.doc_quality import revision_prompt
    tid, _ = _mk_with_doc(tmp_path, 'spec', '占位 <!-- 没写 -->')
    q = client.get(f'/api/v1/tasks/{tid}/doc/spec/revision').json()
    assert q['quality']['errors']
    rp = q['revision_prompt']
    assert rp and '必须修复' in rp and '修法：' in rp and '完成标准' in rp
    # 缺章节的 hint 带该章节写作指引
    q2 = check_content('spec', '# Spec\n## 模块职责与边界\nok\n')
    assert any('详细设计' in e and '修法：' in e for e in q2['errors'])
    # 好文档 → 无需修订
    tid2, _ = _mk_with_doc(tmp_path, 'spec', GOOD_SPEC)
    q3 = client.get(f'/api/v1/tasks/{tid2}/doc/spec/revision').json()
    assert q3['revision_prompt'] is None
    # helper 直测
    assert revision_prompt('spec', None, {'score': 100, 'errors': [], 'warns': []}) is None
