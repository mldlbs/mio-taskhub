# -*- coding: utf-8 -*-
"""S-4 前端全通道 token 联动的静态护栏（交付方案 AC-26）。

背景：服务端启用 --auth / MIO_TASKHUB_TOKEN 后，全局中间件保护 /api/、/metrics、/logs，
WS 要求 ?token= 或 Bearer。前端若有一处请求没带凭据，启用鉴权即整站取不到数据。

本用例不引入 web/ 测试框架，只在后端对前端源码做静态断言：
- 所有 /api/ 与 /metrics 请求必须经 req()（authHeaders）或 authFetch
- 所有 WS 建连必须经 wsUrl()
- /raw 的图片与链接必须有 blob 通道
- 无 token 时必须退化为原生行为，保证默认无鉴权部署零回归

行为层的端到端取证见 docs/taskhub/delivery-plan/final-delivery-plan.md §4.4.1「第三次取证」。
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web" / "src"


def _src(rel):
    return (WEB / rel).read_text(encoding="utf-8")


def _all_frontend():
    return sorted(p for p in WEB.rglob("*") if p.suffix in (".js", ".jsx"))


def test_auth_module_exists_and_degrades_without_token():
    """无 token 时 authFetch 必须直接退化为原生 fetch，不改变默认部署的任何请求。"""
    auth = _src("auth.js")
    assert "export function getToken" in auth
    assert "export function authHeaders" in auth
    assert "export function authFetch" in auth
    assert "export function wsUrl" in auth
    assert re.search(r"if \(!getToken\(\)\) return fetch\(url, opts\)", auth), \
        "authFetch 缺少无 token 直通分支，默认部署会被改变"
    # token 不能长期留在地址栏
    assert "params.delete('token')" in auth
    assert "replaceState" in auth


def test_req_injects_auth_headers():
    """① req() 必须把 Authorization 注入所有 REST 请求，且保留 extraHeaders。"""
    api = _src("api.js")
    assert "import { authHeaders, authFetch, getToken } from './auth'" in api
    assert re.search(
        r"const opts = \{ method, headers: authHeaders\(\{ 'Content-Type': 'application/json'",
        api,
    ), "req() 未走 authHeaders"
    # nightrun 的 X-TaskHub-Admin 头不能被吃掉
    assert "'X-TaskHub-Admin': '1'" in api


def test_no_bare_fetch_to_protected_paths():
    """② 不能残留绕过 req()/authFetch 的裸 fetch 打受保护路径。"""
    bare = re.compile(r"(?<![\w.])fetch\(\s*['\"`](/api/|/metrics)")
    offenders = []
    for p in _all_frontend():
        if p.name == "auth.js":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if bare.search(line):
                offenders.append(f"{p.relative_to(REPO)}:{i}: {line.strip()}")
    assert not offenders, "仍有裸 fetch 打受保护路径：\n" + "\n".join(offenders)


def test_all_websockets_use_ws_url():
    """③ 所有 WS 建连必须经 wsUrl()，不能裸连 ws://host/ws。"""
    offenders = []
    for p in _all_frontend():
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if "new WebSocket(" in line and "wsUrl(" not in line:
                offenders.append(f"{p.relative_to(REPO)}:{i}: {line.strip()}")
    assert not offenders, "仍有裸 WS 建连：\n" + "\n".join(offenders)
    for rel in ("App.jsx", "components/EmbedView.jsx"):
        assert "new WebSocket(wsUrl('/ws'))" in _src(rel), f"{rel} 未用 wsUrl"


def test_raw_assets_have_blob_channel():
    """④ <img src>/<a href> 无法带 header，必须存在 fetch→blob 的替代通道。"""
    api = _src("api.js")
    assert "rawFileBlobUrl: async" in api
    assert "await authFetch(" in api
    assert "URL.createObjectURL" in api
    assert "hasToken: () => !!getToken()" in api
    doc = _src("components/DocPanel.jsx")
    assert "api.rawFileBlobUrl" in doc, "DocPanel 未接入 blob 通道"
    assert "rawBlobUrls" in doc and "revokeObjectURL" in doc, "blob URL 未回收"
    assert "const authed = api.hasToken()" in doc, "缺少无 token 零变化分支"
