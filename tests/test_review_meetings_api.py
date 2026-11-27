# -*- coding: utf-8 -*-
"""研发评审会 REST 端点：extract / points / shortlist / decide / meeting open-close-trace。

契约见 docs/taskhub/review-meeting/api.md §18。测试库由 conftest 隔离。
"""
import pytest
from fastapi.testclient import TestClient

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Decision, Discussion, ReviewPoint, Subtask, Task, TaskReview
from sqlmodel import Session

client = TestClient(app)

DOC = '''## 1. 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 状态 | approved |

## 2. 模块职责与边界

- 负责四级阈值的定义与校准；不负责前端渲染。

## 3. 详细设计

### 3.1 阈值定义

- FR-1 / 四级阈值定义 / 进入阈值、退出门槛、最短持续 τ
- NFR-1 超时 8s

### 3.2 待确认项

| 项 | 值 |
|---|---|
| 依赖 | iServer 主数据 |
| 待确认 | 换算关系未定版 |

### 3.3 一致口径

- 跨文档共有评审点：阈值口径需与运营确认
'''


def _mk_task(tmp_path, monkeypatch):
    p = tmp_path / 'spec.md'
    p.write_text(DOC, encoding='utf-8')
    with Session(engine) as s:
        t = Task(title='评审测试任务', workspace=str(tmp_path),
                 doc_paths={'spec': 'spec.md'},
                 doc_statuses={'spec': {'state': 'approved'}})
        s.add(t)
        s.commit()
        tid = t.id
    return tid


def _review_payload():
    return {
        'risks': ['阈值口径未对齐'],
        'divergences': '无分歧，达成一致',
        'suggestions': '按四级阈值定稿推进',
        'decisions': ['采纳 FR-1', '转 Decision 处理换算关系'],
        'action_items': [{
            'id': 'AI-1', 'owner': 'tech', 'action': '补齐换算关系测试',
            'due': '2026-12-31', 'status': 'pending', 'task_id': None,
        }],
    }


class TestExtractAndPoints:
    def test_extract_then_idempotent(self, tmp_path, monkeypatch):
        tid = _mk_task(tmp_path, monkeypatch)
        r = client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['task_id'] == tid
        assert len(body['points']) >= 3
        assert body['points'][0]['id'].startswith('RP-' + tid)
        assert body['version'].startswith('sha256:')

        r2 = client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        assert r2.status_code == 200
        assert r2.json()['skipped'] == len(body['points'])
        assert r2.json()['points'] == []

    def test_extract_validation(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        assert client.post('/api/v1/reviews/extract', json={}).status_code == 422
        assert client.post('/api/v1/reviews/extract',
                           json={'task_id': 'nope'}).status_code == 404
        r = client.post('/api/v1/reviews/extract',
                        json={'task_id': tid, 'kinds': ['plan']})
        assert r.status_code == 422
        assert '你要做的是' in r.json()['detail']

    def test_points_list_and_detail(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        r = client.get('/api/v1/reviews/points', params={'task_id': tid, 'page': 1, 'size': 2})
        assert r.status_code == 200
        b = r.json()
        assert b['total'] >= 3 and len(b['items']) == 2
        pid = b['items'][0]['id']
        d = client.get('/api/v1/reviews/points/' + pid)
        assert d.status_code == 200
        assert d.json()['source_doc']['kind'] == 'spec'
        assert client.get('/api/v1/reviews/points/RP-none').status_code == 404

    def test_points_pagination_validation(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        r = client.get('/api/v1/reviews/points',
                       params={'task_id': tid, 'size': 999})
        assert r.status_code == 422


class TestDecisionFlow:
    def _extract_and_shortlist(self, tid):
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        with Session(engine) as s:
            pts = s.query(ReviewPoint).filter_by(task_id=tid).order_by(ReviewPoint.seq).all()
            ids = [p.id for p in pts[:2]]
        for pid in ids:
            r = client.patch(f'/api/v1/reviews/points/{pid}/shortlist', json={'selected': True})
            assert r.status_code == 200, r.text
        return ids

    def test_shortlist_validation_and_toggle(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        with Session(engine) as s:
            pid = s.query(ReviewPoint).filter_by(task_id=tid).first().id
        assert client.patch(f'/api/v1/reviews/points/{pid}/shortlist', json={}).status_code == 422
        assert client.patch(f'/api/v1/reviews/points/{pid}/shortlist',
                            json={'selected': True}).json()['status'] == 'shortlisted'
        assert client.patch(f'/api/v1/reviews/points/{pid}/shortlist',
                            json={'selected': False}).json()['status'] == 'candidate'

    def test_decide_reject_requires_reason(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        pid = self._extract_and_shortlist(tid)[0]
        r = client.post(f'/api/v1/reviews/points/{pid}/decide',
                        json={'action': 'reject', 'rationale': '短'})
        assert r.status_code == 422
        r = client.post(f'/api/v1/reviews/points/{pid}/decide',
                        json={'action': 'reject', 'rationale': '该点已被认证层完整覆盖，无需改动'})
        assert r.status_code == 200, r.text
        assert r.json()['status'] == 'rejected'
        # 终态再裁决 409
        assert client.post(f'/api/v1/reviews/points/{pid}/decide',
                           json={'action': 'confirm'}).status_code == 409

    def test_decide_to_decision_and_subtask(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        ids = self._extract_and_shortlist(tid)
        r = client.post(f'/api/v1/reviews/points/{ids[0]}/decide', json={
            'action': 'decide', 'options': [{'label': '接入认证', 'note': '安全优先'}],
        })
        assert r.status_code == 200, r.text
        did = r.json()['decision_id']
        assert did
        with Session(engine) as s:
            d = s.get(Decision, did)
            assert d.source.startswith('RP-')
            assert d.status == 'open'
        r2 = client.post(f'/api/v1/reviews/points/{ids[1]}/decide', json={'action': 'test'})
        assert r2.status_code == 200
        with Session(engine) as s:
            assert s.query(Subtask).filter_by(task_id=tid).count() == 1

    def test_other_reviewer_forbidden(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        pid = self._extract_and_shortlist(tid)[0]
        client.post(f'/api/v1/reviews/points/{pid}/decide',
                    json={'action': 'confirm', 'reviewer': 'alice'})
        r = client.post(f'/api/v1/reviews/points/{pid}/decide',
                        json={'action': 'amend', 'reviewer': 'bob'})
        assert r.status_code == 403
        assert '无权' in r.json()['detail']


class TestMeetingFlow:
    def _shortlist_all(self, tid):
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        with Session(engine) as s:
            ids = [p.id for p in s.query(ReviewPoint).filter_by(task_id=tid).order_by(ReviewPoint.seq).all()]
        for pid in ids:
            client.patch(f'/api/v1/reviews/points/{pid}/shortlist', json={'selected': True})
        return ids

    def _decide_all(self, ids):
        """开会后把所有点裁决到终态（前 2 点决策/采纳，其余暂缓）。"""
        client.post(f'/api/v1/reviews/points/{ids[0]}/decide',
                    json={'action': 'decide', 'options': [{'label': '方案A'}]})
        client.post(f'/api/v1/reviews/points/{ids[1]}/decide', json={'action': 'confirm'})
        for pid in ids[2:]:
            client.post(f'/api/v1/reviews/points/{pid}/decide',
                        json={'action': 'defer', 'rationale': '留待下轮评审再确认该点口径'})

    def test_meeting_open_close_trace(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        ids = self._shortlist_all(tid)
        r = client.post('/api/v1/reviews/meetings/open',
                        json={'task_id': tid, 'roles': ['product', 'tech']})
        assert r.status_code == 200, r.text
        mid = r.json()['discussion_id']
        assert r.json()['point_count'] == len(ids)
        # 重复开会 409
        assert client.post('/api/v1/reviews/meetings/open',
                           json={'task_id': tid}).status_code == 409
        # 关会门控：点未终态
        r = client.post(f'/api/v1/reviews/meetings/{mid}/close',
                        json={'decision': 'approve', 'summary': '六点裁决完成，待补纪要'})
        assert r.status_code == 409
        assert '未终态' in r.json()['detail']

        self._decide_all(ids)
        # 关会门控：缺 review 五段
        r = client.post(f'/api/v1/reviews/meetings/{mid}/close',
                        json={'decision': 'approve', 'summary': '六点裁决完成，待补纪要'})
        assert r.status_code == 409
        assert '门控未过' in r.json()['detail']

        r = client.post(f'/api/v1/reviews/meetings/{mid}/close', json={
            'decision': 'approve', 'summary': '六点裁决完成，一项转决策待拍板',
            'review': _review_payload(),
        })
        assert r.status_code == 200, r.text
        assert r.json()['status'] == 'closed'
        assert r.json()['task_review_id']
        assert len(r.json()['subtask_ids']) == 1

        with Session(engine) as s:
            assert s.query(TaskReview).filter_by(task_id=tid).count() == 1
            d = s.get(Discussion, mid)
            assert d.status == 'closed' and d.mode == 'review'
            assert len(d.review['action_items']) == 1

        tr = client.get(f'/api/v1/reviews/meetings/{mid}/trace')
        assert tr.status_code == 200
        assert tr.json()['count'] == len(ids)
        one = client.get(f'/api/v1/reviews/meetings/{mid}/trace',
                         params={'point_id': ids[0]})
        assert one.json()['count'] == 1

    def test_meeting_open_requires_shortlist(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        # FR-6：强制入会已自动 shortlist 部分点；手动取消全部后再开会应 422
        with Session(engine) as s:
            ids = [p.id for p in s.query(ReviewPoint).filter_by(task_id=tid).all()]
        for pid in ids:
            client.patch(f'/api/v1/reviews/points/{pid}/shortlist', json={'selected': False})
        r = client.post('/api/v1/reviews/meetings/open', json={'task_id': tid})
        assert r.status_code == 422
        assert 'shortlist' in r.json()['detail']


API_DOC = '''## 1. 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.0 |
| 状态 | approved |

## 2. 文档信息与范围

本接口约定评审会的调用契约。

## 3. 环境与基础地址

- 开发：http://127.0.0.1:48620/api/v1

## 4. 认证与鉴权

- 本机信任域，无需鉴权。

## 5. 通用响应结构

| 字段 | 说明 |
|---|---|
| code | 状态码 |

## 6. 统一错误码

| code | 含义 |
|---|---|
| 422 | 参数错误 |

## 7. 字段命名与类型规范

- 驼峰命名。

## 8. 幂等性与重试

- POST 可重试。

## 9. 接口清单

| 接口 | 方法 |
|---|---|
| /reviews/extract | POST |

## 10. 接口明细

### 10.1 POST /reviews/extract

#### 请求参数

| 参数 | 说明 |
|---|---|
| task_id | 任务 ID |

#### 响应字段

| 字段 | 说明 |
|---|---|
| points | 评审点 |

#### 错误码

| code | 含义 |
|---|---|
| 404 | 任务不存在 |

#### 示例

```json
{"task_id":"abc12345"}
```

## 11. 变更记录

- v1.0 初版

## 12. 共享口径

- 跨文档共有评审点：阈值口径需与运营确认

## 13. 版本策略

- 语义化版本。

## 14. 通用请求头

- Content-Type: application/json

## 15. 分页

- page / size。

## 16. 时间 / 数值 / 空值约定

- ISO 8601。

## 17. 枚举全集

- status: open/closed。

## 18. 限流与配额

- 600 req/min。

## 19. 超时与并发

- 5s 超时。

## 20. 文件上传与下载

- 不涉及。

## 21. 安全与脱敏

- 本机信任域。

## 22. 兼容性与废弃策略

- 无废弃。

## 23. 附录

- 无。
'''


class TestFR5FR6:
    """FR-5 跨文档聚类 / FR-6 强制入会与排序。"""

    def test_cluster_id_stable_and_cross_doc(self, tmp_path):
        """同 task 两份文档同 dedup_key → 同 cluster_id，且重跑稳定。"""
        (tmp_path / 'spec.md').write_text(DOC, encoding='utf-8')
        (tmp_path / 'api.md').write_text(API_DOC, encoding='utf-8')
        with Session(engine) as s:
            t = Task(title='cluster测试', workspace=str(tmp_path),
                     doc_paths={'spec': 'spec.md', 'api': 'api.md'},
                     doc_statuses={'spec': {'state': 'approved'}, 'api': {'state': 'approved'}})
            s.add(t); s.commit(); tid = t.id
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec', 'api']})
        with Session(engine) as s:
            pts = s.query(ReviewPoint).filter_by(task_id=tid).all()
            clusters = {p.cluster_id for p in pts if p.cluster_id}
            assert clusters, '应有 cluster_id'
            snap = {p.dedup_key: p.cluster_id for p in pts if p.dedup_key}
        # 重跑：cluster 稳定
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec', 'api']})
        with Session(engine) as s:
            pts2 = s.query(ReviewPoint).filter_by(task_id=tid).all()
            snap2 = {p.dedup_key: p.cluster_id for p in pts2 if p.dedup_key}
        assert snap == snap2

    def test_global_seq_unique_across_kinds(self, tmp_path):
        """跨文档提取 RP-id 不撞（全局 seq 连续）。"""
        (tmp_path / 'spec.md').write_text(DOC, encoding='utf-8')
        (tmp_path / 'api.md').write_text(API_DOC, encoding='utf-8')
        with Session(engine) as s:
            t = Task(title='seq测试', workspace=str(tmp_path),
                     doc_paths={'spec': 'spec.md', 'api': 'api.md'},
                     doc_statuses={'spec': {'state': 'approved'}, 'api': {'state': 'approved'}})
            s.add(t); s.commit(); tid = t.id
        r = client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec', 'api']})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            ids = [p.id for p in s.query(ReviewPoint).filter_by(task_id=tid).all()]
        assert len(ids) == len(set(ids)), f'RP-id 重复: {ids}'

    def test_force_shortlist_high_risk(self, tmp_path):
        """risk=high 的点自动 shortlisted。"""
        tid = _mk_task(tmp_path, None)
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        with Session(engine) as s:
            pts = s.query(ReviewPoint).filter_by(task_id=tid).all()
            high = [p for p in pts if p.risk_level == 'high']
            assert high
            assert all(p.status == 'shortlisted' for p in high)

    def test_force_shortlist_every_kind_and_cap(self, tmp_path):
        """每个出现的 kind 至少 1 点入会；总数 ≤10。"""
        tid = _mk_task(tmp_path, None)
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        with Session(engine) as s:
            pts = s.query(ReviewPoint).filter_by(task_id=tid).all()
        shortlisted = [p for p in pts if p.status == 'shortlisted']
        assert len(shortlisted) <= 10
        kinds_all = {p.kind for p in pts}
        kinds_sel = {p.kind for p in shortlisted}
        assert kinds_all <= kinds_sel, f'kind 未覆盖: {kinds_all - kinds_sel}'


class TestFR3LLMEndpoint:
    """FR-3 端点到库：LLM 建议落字段，删点指令无效，点数不变。"""

    def test_include_llm_fills_fields(self, tmp_path, monkeypatch):
        from mio_taskhub.api import review_meetings as rm
        tid = _mk_task(tmp_path, None)

        def fake_enhancer(points, ctx):
            return {'points': [
                {'id': points[0]['id'], 'why_it_matters': '换算缺失会误告警',
                 'risk_level': 'high', 'suggested_action': 'clarify'},
            ]}

        monkeypatch.setattr(rm, '_LLM_ENHANCER', fake_enhancer)
        r = client.post('/api/v1/reviews/extract',
                        json={'task_id': tid, 'kinds': ['spec'], 'include_llm': True})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['llm']['applied'] == 1
        with Session(engine) as s:
            pts = s.query(ReviewPoint).filter_by(task_id=tid).all()
            first = min(pts, key=lambda p: p.seq)
            assert first.why_it_matters == '换算缺失会误告警'
            assert first.risk_level == 'high'
            assert first.suggested_action == 'clarify'
            assert first.origin == 'rule'  # LLM 只是建议层，来源仍 rule
            assert all(p.status != 'deleted' for p in pts)  # 无删点状态

    def test_include_llm_default_no_enhancer_degrades(self, tmp_path, monkeypatch):
        from mio_taskhub.api import review_meetings as rm
        tid = _mk_task(tmp_path, None)
        monkeypatch.setattr(rm, '_LLM_ENHANCER', None)
        r = client.post('/api/v1/reviews/extract',
                        json={'task_id': tid, 'kinds': ['spec'], 'include_llm': True})
        assert r.status_code == 200
        assert r.json()['llm']['degraded'] == 'no_enhancer'


class TestFR11DecisionWriteback:
    """FR-11：Decision resolve 后回写来源评审点。"""

    def test_resolve_writeback_deferred_point(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        with Session(engine) as s:
            pid = s.query(ReviewPoint).filter_by(task_id=tid).first().id
        # 先 defer（带理由），再用 decide 转 Decision
        client.post(f'/api/v1/reviews/points/{pid}/shortlist', json={'selected': True})
        client.post(f'/api/v1/reviews/points/{pid}/decide',
                    json={'action': 'defer', 'rationale': '留待决策拍板后再定'})
        # defer 是终态，无法再 decide；直接建一个带正确 source 的 Decision 模拟异步流转
        from mio_taskhub.models import Decision
        with Session(engine) as s:
            d = Decision(question='测试决策', status='open', task_id=tid, source=pid)
            s.add(d); s.commit(); did = d.id
        r = client.post(f'/api/v1/decisions/{did}/resolve',
                        json={'choice': '接入认证', 'rationale': '安全优先', 'decider': 'user'})
        assert r.status_code == 200, r.text
        with Session(engine) as s:
            p = s.get(ReviewPoint, pid)
            assert p.status == 'resolved'
            assert '接入认证' in (p.reject_reason or '')

    def test_decide_creates_source_equal_point_id(self, tmp_path):
        tid = _mk_task(tmp_path, None)
        client.post('/api/v1/reviews/extract', json={'task_id': tid, 'kinds': ['spec']})
        with Session(engine) as s:
            pid = s.query(ReviewPoint).filter_by(task_id=tid).first().id
        client.post(f'/api/v1/reviews/points/{pid}/shortlist', json={'selected': True})
        r = client.post(f'/api/v1/reviews/points/{pid}/decide',
                        json={'action': 'decide', 'options': [{'label': '方案A'}]})
        assert r.status_code == 200
        from mio_taskhub.models import Decision
        with Session(engine) as s:
            d = s.get(Decision, r.json()['decision_id'])
            assert d.source == pid  # source == 点 ID，无重复 RP- 前缀
