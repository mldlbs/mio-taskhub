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
        r = client.post('/api/v1/reviews/meetings/open', json={'task_id': tid})
        assert r.status_code == 422
        assert 'shortlist' in r.json()['detail']
