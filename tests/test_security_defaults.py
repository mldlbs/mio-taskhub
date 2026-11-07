# -*- coding: utf-8 -*-
"""安全默认值测试（task 3b5128b6）：net_guard SSRF 校验 + CLI host 默认/拒绝。"""
import os

import pytest

from mio_taskhub.net_guard import check_outbound_url, is_private_host, is_loopback_bind


# ---------- 绑定地址语义（关键：0.0.0.0 不是 loopback）----------

@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "[::1]", "localhost"])
def test_loopback_bind_true(host):
    assert is_loopback_bind(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.5", "10.0.0.1", "8.8.8.8", ""])
def test_loopback_bind_false_for_exposed(host):
    # 0.0.0.0/:: = 所有网卡；真实 IP 也非本机 → 均不视为 loopback（须触发安全门）
    assert is_loopback_bind(host) is False


# ---------- SSRF 校验 ----------

@pytest.mark.parametrize("host", [
    "localhost", "127.0.0.1", "127.0.0.2", "::1", "[::1]",
    "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254",
    "0.0.0.0", "foo.localhost",
])
def test_private_hosts_blocked(host):
    assert is_private_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "1.1.1.1"])
def test_public_hosts_not_private(host):
    assert is_private_host(host) is False


def test_check_url_default_blocks_loopback():
    ok, reason = check_outbound_url("http://127.0.0.1:48620/x")
    assert ok is False and "private" in reason


def test_check_url_blocks_link_local_metadata():
    ok, reason = check_outbound_url("http://169.254.169.254/latest/meta-data/")
    assert ok is False


def test_check_url_allows_public():
    ok, reason = check_outbound_url("http://example.com/hook")
    assert ok is True


def test_check_url_allow_private_overrides():
    ok, reason = check_outbound_url("http://10.0.0.5/notify", allow_private=True)
    assert ok is True


def test_check_url_rejects_non_http_scheme():
    ok, reason = check_outbound_url("file:///etc/passwd")
    assert ok is False and "scheme" in reason


def test_webhook_blocks_private_url_via_engine():
    """cron webhook 打内网地址被 SSRF guard 拒绝（WebhookFailed）。"""
    from mio_taskhub.scheduling import cron_engine as ce

    class _J:
        timeout_seconds = 5
        action_config = {"url": "http://127.0.0.1:1/x", "method": "POST", "body": {}}

    with pytest.raises(ce.WebhookFailed) as ei:
        ce.CronEngine(poll_interval=999)._fire_webhook(_J())
    assert "SSRF" in str(ei.value) or "blocked" in ei.value.summary.lower()


def test_webhook_allow_private_flag_ignored_from_config(monkeypatch):
    """2026-11-07（F-008）：action_config.allow_private 不再能关掉 SSRF 守卫。"""
    from mio_taskhub.scheduling import cron_engine as ce

    monkeypatch.delenv("MIO_WEBHOOK_ALLOW_PRIVATE", raising=False)
    monkeypatch.delenv("MIO_WEBHOOK_ALLOWED_HOSTS", raising=False)

    class _J:
        timeout_seconds = 5
        action_config = {"url": "http://127.0.0.1:9999/x", "method": "POST",
                         "body": {}, "allow_private": True}

    with pytest.raises(ce.WebhookFailed) as ei:
        ce.CronEngine(poll_interval=999)._fire_webhook(_J())
    assert "blocked" in ei.value.summary.lower()


def test_webhook_allow_private_env_passes_guard(monkeypatch):
    """部署级 env 显式放行时通过 guard（随后由请求本身决定成败）。"""
    import httpx
    from mio_taskhub.scheduling import cron_engine as ce

    monkeypatch.setenv("MIO_WEBHOOK_ALLOW_PRIVATE", "1")

    def fake_request(self, method, url, headers=None, json=None):
        return httpx.Response(200, json={"ok": 1}, request=httpx.Request(method, url))
    monkeypatch.setattr(httpx.Client, "request", fake_request)

    class _J:
        timeout_seconds = 5
        action_config = {"url": "http://127.0.0.1:9999/x", "method": "POST", "body": {}}

    out = ce.CronEngine(poll_interval=999)._fire_webhook(_J())
    assert out.startswith("200")


def test_webhook_allowed_hosts_env(monkeypatch):
    """MIO_WEBHOOK_ALLOWED_HOSTS 可按主机放行，不影响其它内网地址。"""
    from mio_taskhub.net_guard import webhook_allow_private

    monkeypatch.delenv("MIO_WEBHOOK_ALLOW_PRIVATE", raising=False)
    monkeypatch.setenv("MIO_WEBHOOK_ALLOWED_HOSTS", "intranet.local,10.0.0.5")
    assert webhook_allow_private("http://intranet.local/hook") is True
    assert webhook_allow_private("http://10.0.0.5/hook") is True
    assert webhook_allow_private("http://10.0.0.6/hook") is False


# ---------- CLI host 默认/拒绝 ----------

def test_cli_host_default_is_loopback():
    """run() 的 --host 默认应为 127.0.0.1（安全默认）。"""
    import inspect
    import mio_taskhub.main as m
    src = inspect.getsource(m.run)
    assert 'default="127.0.0.1"' in src
    assert 'default="0.0.0.0"' not in src
