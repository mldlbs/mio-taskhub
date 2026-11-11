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


# ── F-004 第 2 步：管理面凭据录入对齐 API 凭据 ─────────────────────────────────
# 服务端配了 MIO_TASKHUB_ADMIN_TOKEN 后旧头 X-TaskHub-Admin: 1 失效，前端若没有
# 录入入口，三个管理面操作全 403。以下护栏盯住「录入方式 / 键名隔离 / 双头策略 /
# req() 默认不带管理面头」四个点，防止改录入方式时把 S-4 的鉴权通道带歪。

def test_admin_token_module_exports_and_url_injection():
    """auth.js 需导出管理面凭据三件套，且 ?admin_token= 读后立即从地址栏移除。"""
    auth = _src("auth.js")
    assert "export const ADMIN_TOKEN_KEY" in auth
    assert "export function getAdminToken" in auth
    assert "export function setAdminToken" in auth
    assert "params.get('admin_token')" in auth
    assert "params.delete('admin_token')" in auth
    assert "replaceState" in auth
    assert "location.hash" in auth, "移除注入参数时必须保留 hash 与其余 query"
    assert "sessionStorage.setItem(ADMIN_TOKEN_KEY, fromQuery)" in auth
    # storage 不可用时退化为空串，默认（无鉴权）部署的请求一个都不变
    assert re.search(r"catch \{\s*return ''\s*\}", auth), "getAdminToken 缺少 try/catch 退化分支"


def test_admin_token_key_differs_from_api_token_key():
    """两个键名不能复用：前者管理面，后者 API 访问。"""
    auth = _src("auth.js")
    m_admin = re.search(r"^export const ADMIN_TOKEN_KEY\s*=\s*'([^']+)'", auth, re.M)
    m_api = re.search(r"^const TOKEN_KEY\s*=\s*'([^']+)'", auth, re.M)
    assert m_admin, "auth.js 未导出 ADMIN_TOKEN_KEY"
    assert m_api, "auth.js 缺少 TOKEN_KEY 定义"
    assert m_admin.group(1) != m_api.group(1), "管理面键与 API 键复用了同一字面值"


def test_admin_headers_use_get_admin_token_not_localstorage():
    """adminHeaders() 双头策略不变；管理面 token 一律经 getAdminToken() 读取。"""
    api = _src("api.js")
    assert "import { getAdminToken } from './auth'" in api
    assert "const ADMIN_TOKEN_KEY" not in api, "api.js 仍在本地硬编码管理面键"
    m = re.search(r"export function adminHeaders\(\)\s*\{(.*?)\n\}", api, re.S)
    assert m, "找不到 adminHeaders()"
    body = m.group(1)
    assert "'X-TaskHub-Admin': '1'" in body, "双头策略被改：降级态头丢失"
    assert "getAdminToken()" in body, "adminHeaders 未走 auth.js 的统一读取入口"
    assert "X-TaskHub-Admin-Token" in body, "强隔离部署所需的 token 头丢失"
    assert "localStorage.getItem" not in body, "adminHeaders 仍在直接读 localStorage 管理面键"
    # 九个管理面调用点继续带 adminHeaders()：setDocStatus / updateTask /
    # cancelTask（F-004 第 3 步 destructive 收口）/ nightrun 三个 /
    # operator 面删除三件（B16-6 残余 #2：deleteTemplate / deleteScheduledJob / updateApply）
    calls = set(re.findall(r"^\s*(\w+):.*adminHeaders\(\)", api, re.M))
    assert {"setDocStatus", "updateTask", "cancelTask",
            "nrSaveConfig", "nrStop", "nrSpawnNow",
            "deleteTemplate", "deleteScheduledJob", "updateApply"} <= calls, \
        f"管理面调用点缺 adminHeaders()：{sorted(calls)}"
    # gated() 的 409 确认重试必须透传同一组头，否则 destructive 确认后仍 403
    assert re.search(
        r"async function gated\(method, path, label, extraHeaders\)", api), \
        "gated() 未透传 extraHeaders（409 确认重试会丢管理面头）"


def test_req_default_never_claims_admin():
    """req() 默认 headers 构造不得出现 adminHeaders()：防「所有请求都自称管理员」回归。"""
    api = _src("api.js")
    lines = api.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("async function req("))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    body = "\n".join(lines[start:end + 1])
    assert "authHeaders(" in body, "req() 丢了 S-4 的 Authorization 注入"
    assert "adminHeaders()" not in body, "req() 默认带上了管理面头"
