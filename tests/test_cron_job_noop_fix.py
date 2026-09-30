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
        action_config = {"url": "http://127.0.0.1:1/x", "method": "POST", "body": {}}
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

    def fake(goal, context, timeout=120.0, recently_seen=None, strategy=""):
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
