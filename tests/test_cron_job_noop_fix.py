# -*- coding: utf-8 -*-
"""定时任务空转修复（task 883ebd09）：webhook 非 2xx 如实报错 + 生成去重/降级。

不真调 LLM：_generate_ideas 用 monkeypatch 打桩。
"""
import httpx
import pytest
from fastapi import HTTPException

from mio_taskhub.api import idea_templates as it
from mio_taskhub.scheduling import cron_engine as ce


# ---------- A：webhook 非 2xx 不再假绿 ----------

def _fake_job(status_code: int):
    class _J:
        # allow_private：这些用例测的是 HTTP 响应处理（4xx/2xx），非 SSRF；
        # 用 loopback URL 需显式放行（task 3b5128b6 起默认拒绝内网地址）。
        action_config = {"url": "http://127.0.0.1:1/x", "method": "POST", "body": {},
                         "allow_private": True}
        timeout_seconds = 5
    return _J()


def test_fire_webhook_raises_on_4xx(monkeypatch):
    def fake_request(self, method, url, headers=None, json=None):
        return httpx.Response(409, json={"detail": "all pairs already explored"},
                              request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.Client, "request", fake_request)
    runner = ce.CronEngine(poll_interval=999)
    with pytest.raises(ce.WebhookFailed) as ei:
        runner._fire_webhook(_fake_job(409))
    assert ei.value.status_code == 409
    assert "all pairs already explored" in ei.value.summary


def test_fire_webhook_ok_on_2xx(monkeypatch):
    def fake_request(self, method, url, headers=None, json=None):
        return httpx.Response(200, json={"generated": 1},
                              request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.Client, "request", fake_request)
    runner = ce.CronEngine(poll_interval=999)
    out = runner._fire_webhook(_fake_job(200))
    assert out.startswith("200")


# ---------- C：recently_seen 拼装 + 409 降级 ----------

def test_generate_ideas_includes_recently_seen(monkeypatch):
    seen_args = {}

    class _P:
        returncode = 0
        stderr = ""
        stdout = '{"ideas": [{"title": "T1", "idea": "x"}]}'

    def fake_run(args, **kw):
        seen_args["args"] = args
        return _P()

    monkeypatch.setattr(it.mio_runtime, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(it, "_fetch_observer_insights", lambda *a, **k: [])
    monkeypatch.setattr(it.subprocess, "run", fake_run)
    ideas = it._generate_ideas("G", "C", recently_seen=["老标题A", "老标题B"])
    assert ideas and ideas[0]["title"] == "T1"
    joined = " ".join(seen_args["args"])
    assert "already-explored" in joined
    assert "老标题A" in joined and "老标题B" in joined


def test_generate_with_degrade_retries_on_409(monkeypatch):
    calls = []

    def fake(goal, context, timeout=120.0, recently_seen=None, strategy="",
             sources_limit=5, cross_domain=True):
        calls.append(strategy)
        if strategy == "":
            raise HTTPException(409, "creativity 未生成：all pairs already explored")
        return [{"title": "降级后标题", "idea": "ok"}]

    monkeypatch.setattr(it, "_generate_ideas", fake)
    ideas, used = it._generate_with_degrade("G", "C", recently_seen=["x"])
    assert used == "signal"                      # 换角度成功
    assert ideas[0]["title"] == "降级后标题"
    assert calls == ["", "signal"]


def test_generate_with_degrade_all_fail_raises_409_with_tried(monkeypatch):
    def fake(*a, **k):
        raise HTTPException(409, "creativity 未生成：all pairs already explored")

    monkeypatch.setattr(it, "_generate_ideas", fake)
    with pytest.raises(HTTPException) as ei:
        it._generate_with_degrade("G", "C")
    assert ei.value.status_code == 409
    assert "signal" in ei.value.detail and "stable" in ei.value.detail


def test_generate_with_degrade_passthrough_non_409(monkeypatch):
    def fake(*a, **k):
        raise HTTPException(504, "timeout")

    monkeypatch.setattr(it, "_generate_ideas", fake)
    with pytest.raises(HTTPException) as ei:
        it._generate_with_degrade("G", "C")
    assert ei.value.status_code == 504

# ---------- 素材池扩大 / 跨领域（task 299f1ad3）----------

def test_generate_ideas_cross_domain_and_limit(monkeypatch):
    seen = {}

    class _P:
        returncode = 0
        stderr = ""
        stdout = '{"ideas": [{"title": "T", "idea": "x"}]}'

    def fake_run(args, **kw):
        seen["args"] = args
        return _P()

    monkeypatch.setattr(it.mio_runtime, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(it, "_fetch_observer_insights",
                        lambda limit=5, timeout=15.0: [{"name": "ins", "content": "C"}])
    monkeypatch.setattr(it.subprocess, "run", fake_run)

    it._generate_ideas("G", "C", sources_limit=7, cross_domain=True)
    joined = " ".join(seen["args"])
    assert "already-explored" not in joined          # 未传 recently_seen
    assert "cross" in joined.lower() or "类比" in joined
    # 断言 sources_limit 生效：ins 源存在
    assert "ins:" in joined


def test_generate_ideas_cross_domain_can_be_disabled(monkeypatch):
    seen = {}

    class _P:
        returncode = 0
        stderr = ""
        stdout = '{"ideas": [{"title": "T", "idea": "x"}]}'

    monkeypatch.setattr(it.mio_runtime, "mio_cli", lambda: ["mio"])
    monkeypatch.setattr(it, "_fetch_observer_insights", lambda limit=5, timeout=15.0: [])
    monkeypatch.setattr(it.subprocess, "run", lambda args, **kw: (seen.update(args=args), _P())[1])
    it._generate_ideas("G", "C", cross_domain=False)
    assert "类比" not in " ".join(seen["args"])


def test_mojibake_repair_helper_roundtrip():
    """递归修复：latin1 包装的 utf8 可还原；正常中文不受影响。"""
    good = "每日创意生成"
    bad = good.encode("utf-8").decode("latin-1")
    assert bad != good
    assert bad.encode("latin-1").decode("utf-8") == good
    # 正常中文不含 0x80-0xff 之外的 latin1 包装特征 → 不会被误改
    assert not any(0x80 <= ord(c) <= 0xff for c in good)
