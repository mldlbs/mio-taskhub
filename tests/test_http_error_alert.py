# -*- coding: utf-8 -*-
"""P1-OBS/F10：HighHttpErrorRate 只计 5xx，4xx（正常客户端错误）不应触发。"""
import mio_taskhub.observability.alerts as al


def _http_metrics(total, by_class):
    return {"request_count_total": total, "error_count": by_class, "active_requests": 0}


def test_4xx_does_not_trigger_alert(monkeypatch):
    monkeypatch.setattr(al, "get_http_metrics", lambda: _http_metrics(100, {"4xx": 90}))
    alerts = al.AlertManager()._check_http()
    assert not any(a.name == "HighHttpErrorRate" for a in alerts), "4xx 不应触发"


def test_5xx_triggers_alert(monkeypatch):
    monkeypatch.setattr(al, "get_http_metrics", lambda: _http_metrics(100, {"4xx": 50, "5xx": 10}))
    alerts = al.AlertManager()._check_http()
    assert any(a.name == "HighHttpErrorRate" for a in alerts), "5xx>5% 应触发"


def test_no_errors_no_alert(monkeypatch):
    monkeypatch.setattr(al, "get_http_metrics", lambda: _http_metrics(100, {}))
    alerts = al.AlertManager()._check_http()
    assert not any(a.name == "HighHttpErrorRate" for a in alerts)
