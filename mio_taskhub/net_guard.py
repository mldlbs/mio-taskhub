# -*- coding: utf-8 -*-
"""出站网络地址校验（SSRF 防护，task 3b5128b6 / 评估 round2 P2-A）。

cron webhook 的 URL 来自用户配置，可直接指向内网/元数据地址
（如 169.254.169.254、10.x、192.168.x），构成 SSRF。本模块提供默认拒绝
「环回 / 私有 / 链路本地 / 保留」地址的校验；确需访问内网时用
`allow_private=True` 显式放开。

注意：本校验是**字面量 + DNS 解析后**双重判定，降低 DNS rebinding 风险
（解析后地址仍须通过校验）。
"""
import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger("mio_taskhub.net_guard")

_ALLOWED_SCHEMES = ("http", "https")


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def is_private_host(host: str) -> bool:
    """host 是否为环回/私有/链路本地/保留地址（含 localhost 及解析结果）。"""
    if not host:
        return True
    h = host.strip().strip("[]").lower()
    if h in ("localhost", "localhost.localdomain") or h.endswith(".localhost"):
        return True
    # 字面量 IP
    try:
        return _is_blocked_ip(ipaddress.ip_address(h))
    except ValueError:
        pass
    # 域名 → DNS 解析后判定（防 rebinding 的第一道）
    try:
        infos = socket.getaddrinfo(h, None)
    except OSError:
        # 无法解析：按"目标不可信"处理，交由请求阶段失败（不因解析失败误放行内网）
        return False
    for info in infos:
        addr = info[4][0]
        try:
            if _is_blocked_ip(ipaddress.ip_address(addr)):
                return True
        except ValueError:
            continue
    return False


def is_loopback_bind(host: str) -> bool:
    """**绑定地址**语义下是否为"仅本机"。

    与 is_private_host 不同：`0.0.0.0` / `::`（所有网卡）虽然是 is_unspecified、
    在 is_private_host 下返回 True，但作为**监听地址**意味着对外暴露，必须视为
    非本机。本函数只把 127.0.0.0/8 与 ::1 判为 loopback。
    """
    if not host:
        return False
    h = host.strip().strip("[]").lower()
    if h in ("localhost", "localhost.localdomain"):
        return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv4Address):
        return ip in ipaddress.ip_network("127.0.0.0/8")
    return ip == ipaddress.IPv6Address("::1")


def check_outbound_url(url: str, allow_private: bool = False) -> tuple[bool, str]:
    """校验出站 URL。返回 (allowed, reason)。"""
    u = urlparse(url or "")
    if u.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, f"scheme '{u.scheme}' not allowed (only http/https)"
    if not u.hostname:
        return False, "missing host"
    if allow_private:
        return True, "allowed (allow_private=true)"
    if is_private_host(u.hostname):
        return False, (
            f"host '{u.hostname}' resolves to a private/loopback/link-local address; "
            f"blocked by SSRF guard (set allow_private=true to override)"
        )
    return True, "ok"
