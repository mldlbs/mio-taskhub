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

# 一份「事无巨细」的接口契约参考样本：21 节齐全，接口明细含完整四子块。
# 接口契约的质量规格最严（10 必需章节 + 每个接口四子块），这里当基准用。
GOOD_API = """# 接口契约
## 1. 文档信息与范围
| 项 | 值 |
|----|----|
| 契约版本 | v1 |
- 明确不含：内部 RPC。

## 2. 环境与基础地址
| 环境 | Base URL |
|------|----------|
| prod | https://api.example.com |

## 3. 版本策略
- 版本位置：URL 路径 `/api/v1`。

## 4. 认证与鉴权
| 项 | 约定 |
|----|------|
| 认证方式 | Bearer |
| 未认证响应 | 401 UNAUTHENTICATED |
| 越权响应 | 403 FORBIDDEN |

## 5. 通用请求头
| Header | 必填 | 类型 | 默认 | 说明 |
|--------|------|------|------|------|
| `Authorization` | 是 | string | — | Bearer token |

## 6. 通用响应结构
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `code` | integer | 是 | 业务码，0 成功 |
| `message` | string | 是 | 仅展示用 |
| `data` | object \\| null | 否 | 失败时为 null |

## 7. 统一错误码
| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | TASK_NOT_FOUND | 任务不存在 | id 查无 | 核对 id |
| 429 | RATE_LIMITED | 超出限流 | 超阈值 | 按 Retry-After 退避 |

## 8. 分页 / 排序 / 过滤
| 项 | 参数 | 类型 | 默认 | 上限 | 说明 |
|----|------|------|------|------|------|
| 每页条数 | size | integer | 20 | 100 | 超限截断为 100 |

## 9. 字段命名与类型规范
| 项 | 规范 |
|----|------|
| 命名风格 | snake_case，反例：userName |
| 可选字段 | 缺失=未设置；显式 null=清空 |

## 10. 时间 / 数值 / 空值约定
- 时间格式：RFC3339，UTC。

## 11. 枚举全集
| 枚举 | 取值 | 含义 | 出现在哪些接口 |
|------|------|------|---------------|
| state | queued | 待领取 | GET /tasks |

## 12. 幂等性与重试
| 接口 / 场景 | 幂等 | 幂等键 | 重试建议 |
|------------|------|--------|---------|
| GET /tasks/{id} | 是 | — | 可直接重试 |
- 不可重试：409 冲突。

## 13. 限流与配额
| 范围 | 阈值 | 窗口 | 超限响应 |
|------|------|------|---------|
| 全局 | 120 | 1min | 429 + Retry-After |

## 14. 超时与并发
- 建议客户端超时：5s。

## 15. 文件上传与下载
| 项 | 约定 |
|----|------|
| 上传方式 | multipart |

## 16. 安全与脱敏
- 敏感字段：token 一律脱敏。

## 17. 接口清单
| Method | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|--------|------|------|------|------|------|---------|
| GET | /api/v1/tasks/{id} | 查任务详情 | task:read | 是 | 全局 | 任意 |

## 18. 接口明细

### GET /api/v1/tasks/{id}

- 用途：按 ID 查询任务
- 权限：`task:read`
- 幂等：是
- 副作用：无

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| id | path | string | 是 | — | 8 位 hex | 任务 ID | a1b2c3d4 |
| verbose | query | boolean | 否 | false | — | 扩展字段 | true |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `data.id` | string | 是 | 任务 ID | a1b2c3d4 |
| `data.state` | string | 是 | 执行状态 | queued |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | TASK_NOT_FOUND | 任务不存在 | id 查无 | 核对 id |
| 403 | FORBIDDEN | 无权限 | 非归属者 | 检查权限点 |

#### 示例

请求：

```bash
curl -s "https://api.example.com/api/v1/tasks/a1b2c3d4"
```

响应（200）：

```json
{"code": 0, "message": "ok", "data": {"id": "a1b2c3d4"}}
```

## 19. 兼容性与废弃策略
- 破坏性变更：字段删除、枚举删值。

## 20. 变更记录
| 日期 | 契约版本 | 变更内容 | 类型 | 影响方 |
|------|---------|---------|------|--------|
| 2026-09-17 | v1 | 首次发布 | — | — |

## 21. 附录
- 相关文档：`docs/data-model.md`
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


# ── 接口契约「事无巨细」规格（用户要求：必须足够详细记录每个细节）───────────────

def test_api_spec_is_the_strictest():
    """接口契约的必需章节数应为全链最多，且带 detail_rule。"""
    from mio_taskhub.doc_quality import QUALITY_SPEC
    api = QUALITY_SPEC['api']
    others = [len(v['sections']) for k, v in QUALITY_SPEC.items() if k != 'api']
    assert len(api['sections']) > max(others), '接口契约必须是要求最细的文档'
    assert len(api['sections']) >= 10
    assert api['recommended'], '接口契约应有建议章节层'
    assert api['detail_rule']['section'] == '接口明细'
    assert set(api['detail_rule']['unit_blocks']) == {'请求参数', '响应字段', '错误码', '示例'}


def test_api_template_errors_cover_every_required_section():
    """模板原样 → 每个必需章节各报一条「未填写」，写作者一眼看到全部待填项。"""
    from mio_taskhub.doc_chain import render_chain
    from mio_taskhub.doc_quality import QUALITY_SPEC
    tpl = render_chain('api')
    # 模板自身要覆盖全部必需 + 建议章节（否则是模板缺节，不是作者的问题）
    for sec in QUALITY_SPEC['api']['sections'] + QUALITY_SPEC['api']['recommended']:
        assert sec in tpl, f'模板缺少章节：{sec}'
    q = check_content('api', tpl)
    unfilled = [e for e in q['errors'] if '未填写' in e]
    assert len(unfilled) == len(QUALITY_SPEC['api']['sections'])
    assert q['score'] < 100


def test_api_good_contract_passes_clean():
    """填满的接口契约 → 0 error；分点/表格齐全时无 warn，满分。"""
    q = check_content('api', GOOD_API)
    assert q['errors'] == [], q['errors']
    assert q['score'] == 100


def test_api_detail_rule_requires_every_endpoint_block():
    """接口明细：缺子块 / 子块空表 / 无接口，都要被拦下并给出可执行修法。"""
    # 删掉「错误码」子块
    missing_block = GOOD_API.replace('#### 错误码\n', '#### 备注\n')
    q1 = check_content('api', missing_block)
    assert any('缺少「错误码」子块' in e for e in q1['errors']), q1['errors']
    assert any('请求参数 / 响应字段 / 错误码 / 示例' in e for e in q1['errors'])

    # 「响应字段」表格只剩表头（0 数据行）
    empty_table = GOOD_API.replace(
        '| `data.id` | string | 是 | 任务 ID | a1b2c3d4 |\n'
        '| `data.state` | string | 是 | 执行状态 | queued |\n', '')
    assert empty_table != GOOD_API, '测试夹具未生效'
    q2 = check_content('api', empty_table)
    assert any('「响应字段」子块表格数据行不足' in e for e in q2['errors']), q2['errors']

    # 接口明细完全没有接口小节（注释已删）
    no_unit = GOOD_API.replace(
        GOOD_API[GOOD_API.index('### GET'):GOOD_API.index('## 19.')], '')
    q3 = check_content('api', no_unit)
    assert any('接口明细不足' in e for e in q3['errors']), q3['errors']


def test_api_quality_gate_blocks_status_advance(tmp_path):
    """接口契约纳入生命周期后，质量门控真正生效（此前只提示不阻断）。

    draft → review 要求 errors=0：空心契约被 422；填满的契约放行；force 可绕过。
    """
    from mio_taskhub.doc_lifecycle import INITIAL_STATE
    assert INITIAL_STATE['api'] == 'draft', 'api 需有生命周期，否则门控不生效'

    # 空心契约：PUT 写入即自动落 draft
    tid, _ = _mk_with_doc(tmp_path, 'api', '占位 <!-- 没写 -->')
    st = client.get(f'/api/v1/tasks/{tid}/doc/statuses').json()['statuses']['api']
    assert st['state'] == 'draft'

    r = client.post(f'/api/v1/tasks/{tid}/doc/api/status', json={'state': 'review'})
    assert r.status_code == 422, r.text
    assert 'quality gate' in str(r.json()['detail'])

    # force 可绕过
    r2 = client.post(f'/api/v1/tasks/{tid}/doc/api/status',
                     json={'state': 'review', 'force': True})
    assert r2.status_code == 200, r2.text

    # 填满的契约从 draft 直接放行
    tid2, _ = _mk_with_doc(tmp_path, 'api', GOOD_API)
    r3 = client.post(f'/api/v1/tasks/{tid2}/doc/api/status', json={'state': 'review'})
    assert r3.status_code == 200, r3.text
    r4 = client.post(f'/api/v1/tasks/{tid2}/doc/api/status', json={'state': 'approved'})
    assert r4.status_code == 200, r4.text
    st2 = client.get(f'/api/v1/tasks/{tid2}/doc/statuses').json()['statuses']['api']
    assert st2['state'] == 'approved' and st2['allowed_next'] == []


def test_api_recommended_sections_warn_but_do_not_block():
    """建议章节缺失只记 warn（扣分不阻断）；必需章节缺失才记 error。"""
    from mio_taskhub.doc_quality import QUALITY_SPEC
    # 抽掉全部建议章节
    trimmed = GOOD_API
    for sec in ('版本策略', '通用请求头', '分页', '时间 / 数值 / 空值约定',
                '枚举全集', '限流与配额', '超时与并发', '文件上传与下载',
                '安全与脱敏', '兼容性与废弃策略', '附录'):
        start = next((l for l in trimmed.splitlines()
                      if l.startswith('## ') and sec in l), None)
        assert start, sec
        # 切掉该节到下一个 H2
        i = trimmed.index(start)
        nxt = trimmed.find('\n## ', i + 1)
        trimmed = trimmed[:i] + (trimmed[nxt + 1:] if nxt != -1 else '')
    q = check_content('api', trimmed)
    assert q['errors'] == [], q['errors']
    missing_warns = [w for w in q['warns'] if '缺少建议章节' in w]
    assert len(missing_warns) == len(QUALITY_SPEC['api']['recommended'])
    assert 0 < q['score'] < 100

    # 必需章节缺失 → error
    no_auth = trimmed.replace('## 4. 认证与鉴权\n', '')
    q2 = check_content('api', no_auth)
    assert any('缺少必需章节「认证与鉴权」' in e for e in q2['errors'])


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
