// 浏览器侧鉴权凭据注入（交付方案 S-4）。
//
// 约束：
// - 无 token 时所有 helper 退化为原生行为，默认（无鉴权）部署的任何一个请求都不变。
// - token 来源策略只集中在 getToken()，后续换策略只改这一处。
// - token 不长期留在地址栏：首次从 ?token= 读取后立即从 URL 移除，避免落到
//   浏览器历史 / Referer / 截图。REST 走 Authorization: Bearer，WS 走 ?token=。
// - /raw 的图片与下载链接由标签发出、无法附 header，改由 fetch + blob URL 取回。

const TOKEN_KEY = 'mio-taskhub.token'

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
