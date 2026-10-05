# tests/test_ratchet.py
"""棘轮基线：文档质量分 / 用例数「只升不降」。

- 首次观察 → 记基线；
- 回退（current < baseline）→ 阻断，force 可绕过；
- 提升（current > baseline）→ 抬高基线。
"""
from fastapi.testclient import TestClient

from mio_taskhub.main import app
from mio_taskhub.ratchet import check_ratchet, current_metrics, applies
from mio_taskhub.db import engine
from sqlmodel import Session

client = TestClient(app)


# ── 纯逻辑 ────────────────────────────────────────────────────────────────

def test_applies_states():
    for s in ('review', 'approved', 'done', 'passed', 'failed'):
        assert applies(s) is True
    for s in ('draft', 'planned', ''):
        assert applies(s) is False


def test_current_metrics_kinds():
    assert current_metrics('spec', GOOD_SPEC)['score'] == 100
    assert 'test_cases' not in current_metrics('spec', GOOD_SPEC)
    t = GOOD_SPEC + "## 3. 用例清单\n| 用例 | 需求 |\n|---|---|\n| TC-1 | FR-1 |\n| TC-2 | FR-1 |\n"
    assert current_metrics('test', t)['test_cases'] == 2


def test_check_ratchet_baseline_block_bump():
    with Session(engine) as db:
        # 首次 → 建基线
        blocked, bumps = check_ratchet(db, 't1', 'spec', '# S\n## 文档信息\n| 项 | 值 |\n|---|---|\n| 文档版本 | v0.1 |\n| 最后更新 | 2026-09-23 |\n| 状态 | draft |\n| 负责人 | x |\n| 适用范围 | y |\n## 模块职责与边界\nok\n## 详细设计\nok\n')
        assert bumps and bumps[0]['metric'] == 'score' and bumps[0]['value'] == 100
        assert blocked == []
        db.commit()

        # 回退 → blocked
        bad = "# S\n## 文档信息\n| 项 | 值 |\n|---|---|\n| 文档版本 | v0.1 |\n| 最后更新 | 2026-09-23 |\n| 状态 | draft |\n| 负责人 | x |\n| 适用范围 | y |\n## 模块职责与边界\nok\n## 详细设计\nok\n## 额外\n<!-- 未填的指引注释 → warn 扣分 -->\n"
        blocked2, _ = check_ratchet(db, 't1', 'spec', bad)
        assert blocked2 and blocked2[0] == {'kind': 'spec', 'metric': 'score',
                                            'current': 95, 'baseline': 100}
        db.rollback()


# ── API 集成 ──────────────────────────────────────────────────────────────

INFO = ("## 文档信息\n| 项 | 值 |\n|---|---|\n| 文档版本 | v0.1 |\n"
        "| 最后更新 | 2026-09-23 |\n| 状态 | draft |\n| 负责人 | 张 |\n| 适用范围 | x |\n")
GOOD_SPEC = f"# Spec\n{INFO}## 1. 模块职责与边界\n做登录。\n## 2. 详细设计\n用 JWT。\n"
# 加一个带残留注释的可选章节 → 只产生 warn（errors 仍为 0）→ 分数 95
WEAK_SPEC = GOOD_SPEC + "## 3. 附注\n<!-- 待补 -->\n"


def _mk(tmp_path):
    ws = tmp_path / 'ws'
    ws.mkdir(exist_ok=True)
    tid = client.post('/api/v1/tasks', json={'title': 'ratchet', 'workspace': str(ws)}).json()['id']
    return tid, ws


def _put(tid, kind, content):
    return client.put(f'/api/v1/tasks/{tid}/doc', params={'kind': kind}, json={'content': content})


def test_api_ratchet_blocks_regression_and_force(tmp_path):
    tid, ws = _mk(tmp_path)
    assert _put(tid, 'spec', GOOD_SPEC).status_code == 200
    # 首次推进到 review → 建基线 score=100（review 也在棘轮状态集里）
    assert client.post(f'/api/v1/tasks/{tid}/doc/spec/status',
                       json={'state': 'review'}).status_code == 200

    b = client.get(f'/api/v1/tasks/{tid}/ratchet').json()
    assert b['enabled'] is True
    assert any(x['kind'] == 'spec' and x['metric'] == 'score' and x['value'] == 100
               for x in b['baselines'])

    # 换成更差的文档（0 error / 1 warn / 95 分）→ 再走合法前向 review→approved → 棘轮拦截
    assert _put(tid, 'spec', WEAK_SPEC).status_code == 200
    r = client.post(f'/api/v1/tasks/{tid}/doc/spec/status', json={'state': 'approved'})
    assert r.status_code == 422, r.text
    assert any(x['metric'] == 'score' and x['baseline'] == 100
               for x in r.json()['detail']['ratchet'])

    # force 可绕过并留痕
    r2 = client.post(f'/api/v1/tasks/{tid}/doc/spec/status',
                     json={'state': 'approved', 'force': True})
    assert r2.status_code == 200, r2.text


def test_check_ratchet_test_cases_metric():
    """test 的用例数指标：2 → 基线 2；降到 1 → 拦截（模块级，避开状态机终态限制）。"""
    two = (f"# Test\n{INFO}## 1. 验收标准\n- x\n## 2. 测试范围\n- y\n"
           "## 3. 用例清单\n| 用例 | 需求 |\n|---|---|\n| TC-1 | FR-1 |\n| TC-2 | FR-1 |\n")
    one = (f"# Test\n{INFO}## 1. 验收标准\n- x\n## 2. 测试范围\n- y\n"
           "## 3. 用例清单\n| 用例 | 需求 |\n|---|---|\n| TC-1 | FR-1 |\n")
    with Session(engine) as db:
        _, bumps = check_ratchet(db, 't-test', 'test', two)
        assert any(x['metric'] == 'test_cases' and x['value'] == 2 for x in bumps)
        db.commit()
        blocked, _ = check_ratchet(db, 't-test', 'test', one)
        assert any(x['metric'] == 'test_cases' and x['baseline'] == 2
                   and x['current'] == 1 for x in blocked)
        db.rollback()


def test_api_ratchet_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv('MIO_RATCHET_DISABLED', '1')
    tid, ws = _mk(tmp_path)
    assert _put(tid, 'spec', GOOD_SPEC).status_code == 200
    assert client.post(f'/api/v1/tasks/{tid}/doc/spec/status',
                       json={'state': 'review'}).status_code == 200
    assert _put(tid, 'spec', WEAK_SPEC).status_code == 200
    # 关闭后不建基线、不拦截
    assert client.post(f'/api/v1/tasks/{tid}/doc/spec/status',
                       json={'state': 'approved'}).status_code == 200
    assert client.get(f'/api/v1/tasks/{tid}/ratchet').json()['baselines'] == []
