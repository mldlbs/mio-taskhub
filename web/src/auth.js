// 浏览器侧鉴权凭据注入（交付方案 S-4）。
//
// 约束：
// - 无 token 时所有 helper 退化为原生行为，默认（无鉴权）部署的任何一个请求都不变。
// - token 来源策略只集中在 getToken()，后续换策略只改这一处。
// - token 不长期留在地址栏：首次从 ?token= 读取后立即从 URL 移除，避免落到
//   浏览器历史 / Referer / 截图。REST 走 Authorization: Bearer，WS 走 ?token=。
// - /raw 的图片与下载链接由标签发出、无法附 header，改由 fetch + blob URL 取回。
// - 管理面凭据（ADMIN_TOKEN_KEY）比 API 凭据敏感：?admin_token= 注入只用于本机运维
//   首次录入，读到后立即从地址栏移除，不要留在日志 / 截图里；两类凭据键名不复用。

// API 访问凭据（--auth / MIO_TASKHUB_TOKEN）：REST 走 Bearer，WS 走 ?token=。
const TOKEN_KEY = 'mio-taskhub.token'
// 管理面凭据（MIO_TASKHUB_ADMIN_TOKEN）：只由 Web 前端 / 运维持有，绝不下发给 agent。
// 与 TOKEN_KEY **不可复用**：前者回答「是不是管理面操作者」，后者只回答「能不能访问 API」。
export const ADMIN_TOKEN_KEY = 'mio-taskhub.admin-token'

export function getToken() {
  try {
    const params = new URLSearchParams(location.search)
    const fromQuery = params.get('token')
    if (fromQuery) {
      sessionStorage.setItem(TOKEN_KEY, fromQuery)
      params.delete('token')
      const rest = params.toString()
      history.replaceState(null, '', location.pathname + (rest ? '?' + rest : '') + location.hash)
      return fromQuery
    }
    return sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch { /* storage 不可用时忽略 */ }
}

// 管理面凭据：与 getToken() 同构但读的是 ADMIN_TOKEN_KEY，绝不落到 API 凭据的键上。
// 优先从 ?admin_token= 读一次（本机运维首次录入），读到立即从地址栏移除并写入
// sessionStorage；否则依次读 sessionStorage → localStorage。全程 try/catch，
// storage 不可用退化为空串，默认（无鉴权）部署的任何一个请求都不变。
export function getAdminToken() {
  try {
    const params = new URLSearchParams(location.search)
    const fromQuery = params.get('admin_token')
    if (fromQuery) {
      sessionStorage.setItem(ADMIN_TOKEN_KEY, fromQuery)
      params.delete('admin_token')
      const rest = params.toString()
      history.replaceState(null, '', location.pathname + (rest ? '?' + rest : '') + location.hash)
      return fromQuery
    }
    return sessionStorage.getItem(ADMIN_TOKEN_KEY) || localStorage.getItem(ADMIN_TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

export function setAdminToken(token) {
  try {
    if (token) sessionStorage.setItem(ADMIN_TOKEN_KEY, token)
    else sessionStorage.removeItem(ADMIN_TOKEN_KEY)
  } catch { /* storage 不可用时忽略 */ }
}

export function authHeaders(extra) {
  const token = getToken()
  if (!token) return extra || {}
  return { ...(extra || {}), Authorization: `Bearer ${token}` }
}

export function authFetch(url, opts = {}) {
  if (!getToken()) return fetch(url, opts)
  return fetch(url, { ...opts, headers: authHeaders(opts.headers) })
}

export function wsUrl(path = '/ws') {
  const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:'
  const base = `${scheme}//${location.host}${path}`
  const token = getToken()
  return token ? `${base}?token=${encodeURIComponent(token)}` : base
}
