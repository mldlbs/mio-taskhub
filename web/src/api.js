const BASE = '/api/v1'

const ERROR_MESSAGES = {
  400: '请求参数有误，请检查后重试',
  401: '未授权，请登录后重试',
  403: '无权执行此操作',
  404: '资源不存在',
  409: '操作冲突，请刷新后重试',
  422: '数据校验失败，请检查必填项',
  429: '请求过于频繁，请稍后重试',
  500: '服务器内部错误，请稍后重试',
  502: '服务暂不可用，请稍后重试',
  503: '服务维护中，请稍后重试',
  504: '请求超时，请稍后重试',
}

export class ApiError extends Error {
  constructor(message, status, detail, url) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.url = url
    this.userMessage = ERROR_MESSAGES[status] || `请求失败 (${status})`
    if (detail && typeof detail === 'object') {
      if (detail.hint) this.hint = detail.hint
      if (detail.error) this.error_code = detail.error
    }
  }
}

async function req(method, path, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } }
  if (body) opts.body = JSON.stringify(body)
  const url = BASE + path
  const r = await fetch(url, opts)
  if (r.status === 204) return null
  const data = await r.json().catch(() => ({}))
  if (!r.ok) {
    const detail = data && data.detail
    const msg = typeof detail === 'string' ? detail : (detail?.detail || detail?.error || (detail ? JSON.stringify(detail) : `HTTP ${r.status}`))
    throw new ApiError(msg, r.status, detail, url)
  }
  return data
}

// 危险操作门控：409 policy_risk_high → 弹窗确认后带 confirm=true 重试。
// 低风险时后端直接放行，此处零开销；Mio 不可用时 fail-open 也不会触发。
async function gated(method, path, label) {
  try {
    return await req(method, path)
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.error_code === 'policy_risk_high') {
      const risk = (e.detail && e.detail.risk) || 'high'
      const sug = (e.detail && e.detail.policy && e.detail.policy.suggestion) || ''
      const ok = window.confirm(`【${label}】Mio 历史风险评估：${risk}\n${sug}\n\n仍要继续吗？（confirm=true）`)
      if (!ok) throw e
      const sep = path.includes('?') ? '&' : '?'
      return await req(method, path + sep + 'confirm=true')
    }
    throw e
  }
}

export const api = {
  listTasks: (params) => req('GET', '/tasks' + (params ? '?' + new URLSearchParams(params).toString() : '')),
  advanceStage: (id, body) => req('POST', `/tasks/${id}/stage`, body),
  moveToStage: (id, body) => req('POST', `/tasks/${id}/stage/move`, body),
  getTask: (id) => req('GET', `/tasks/${id}`),
  getTaskDoc: (id, kind) => req('GET', `/tasks/${id}/doc?kind=${kind}`),
  getTaskDocuments: (id) => req('GET', `/tasks/${id}/documents`),
  // 文档生命周期总览：各 kind 当前状态 + states 全序列 + 合法后继
  getDocStatuses: (id) => req('GET', `/tasks/${id}/doc/statuses`),
  getTaskFile: (id, path) => req('GET', `/tasks/${id}/file?path=${encodeURIComponent(path)}`),
  // 原样返回 workspace 内文件（二进制安全），用于 <img src> 等直接引用
  rawFileUrl: (id, path) => `${BASE}/tasks/${id}/raw?path=${encodeURIComponent(path)}`,
  getTaskGraph: (id) => req('GET', `/tasks/${id}/graph`),
  getFullGraph: () => req('GET', '/tasks/graph'),
  createTask: (t) => req('POST', '/tasks', t),
  updateTask: (id, body) => req('PATCH', `/tasks/${id}`, body),
  addSubtask: (id, body) => req('POST', `/tasks/${id}/subtasks`, body),
  updateSubtask: (id, sid, body) => req('PATCH', `/tasks/${id}/subtasks/${sid}`, body),
  addDiscussion: (id, body) => req('POST', `/tasks/${id}/discussions`, body),
  claim: (agent) => req('POST', `/tasks/claim?agent=${encodeURIComponent(agent)}`),
  cancelTask: (id) => gated('DELETE', `/tasks/${id}`, '取消任务'),
  retryTask: (id) => req('POST', `/tasks/${id}/retry`, {}),
  heartbeat: (rid, body) => req('POST', `/runs/${rid}/heartbeat`, body),
  result: (rid, body) => req('POST', `/runs/${rid}/result`, body),
  nightPlan: (start, end, project) => {
    let url = `/plans/night?start=${start}&end=${end}`;
    if (project) url += `&project=${encodeURIComponent(project)}`;
    return req('GET', url);
  },
  nightPlanSaved: () => req('GET', '/plans/night/saved'),
  listProjects: () => req('GET', '/plans/projects'),
  nrConfig: () => req('GET', '/nightrun/config'),
  nrLoadFull: () => req('GET', '/nightrun/full-config'),
  nrSaveConfig: (cfg) => req('PUT', '/nightrun/config', cfg),
  nrStop: () => req('POST', '/nightrun/stop', {}),
  nrSpawnNow: (agents) => req('POST', '/nightrun/spawn-now', agents),
  nrStatus: () => req('GET', '/nightrun/full-config'),
  nrCronTasks: () => req('GET', '/nightrun/cron-tasks'),
  // Reviews
  reviewQueue: () => req('GET', '/tasks/reviews/queue'),
  listReviews: (taskId) => req('GET', `/tasks/${taskId}/reviews`),
  submitReview: (taskId, body) => req('POST', `/tasks/${taskId}/reviews`, body),
  listIdeas: (params) => req('GET', '/ideas' + (params ? '?' + new URLSearchParams(params).toString() : '')),
  getIdea: (id, params) => req('GET', `/ideas/${id}` + (params ? '?' + new URLSearchParams(params).toString() : '')),
  createIdea: (body) => req('POST', '/ideas', body),
  updateIdea: (id, body) => req('PATCH', `/ideas/${id}`, body),
  advanceIdea: (id, status) => req('POST', `/ideas/${id}/status`, { status }),
  openDiscussion: (body) => req('POST', '/discussions', body),
  getDiscussion: (id) => req('GET', `/discussions/${id}`),
  listDiscussions: (refType, refId) => req('GET', `/discussions?ref_type=${refType}&ref_id=${refId}`),
  replyDiscussion: (id, body) => req('POST', `/discussions/${id}/messages`, body),
  closeDiscussion: (id, body) => req('POST', `/discussions/${id}/close`, body),
  breakdownIdea: (id, body) => req('POST', `/ideas/${id}/breakdown`, body),
  suggestTasks: (id, body) => req('POST', `/ideas/${id}/suggest-tasks`, body),
  ideaHistory: (id, page = 1, pageSize = 20) =>
    req('GET', `/ideas/${id}/history?page=${page}&page_size=${pageSize}`),
  // ADR API
  evolveToAdr: (id, body) => req('POST', `/ideas/${id}/evolve-to-adr`, body),
  adrAction: (id, body) => req('POST', `/ideas/${id}/adr-action`, body),
  adrMarkdown: (id) => req('GET', `/ideas/${id}/adr-md`),
  boardSummary: (agent) => req('GET', '/board/summary' + (agent ? `?agent=${encodeURIComponent(agent)}` : '')),
  statsOverview: () => req('GET', '/board/overview'),
  getTaskEvents: (taskId, limit = 50) => req('GET', `/tasks/${taskId}/events` + (limit ? `?limit=${limit}` : '')),
  status: (agent) => req('GET', '/status' + (agent ? `?agent=${encodeURIComponent(agent)}` : '')),
  // Memory Gateway (v3)
  memoryHealth: async () => {
    const r = await fetch('/api/memory/health')
    if (!r.ok) throw new Error(`memory/health HTTP ${r.status}`)
    return r.json()
  },
  memoryEvents: async (limit = 50) => {
    // /api/v1/events 不支持 entity 过滤，客户端按 type 前缀 memory_ 过滤
    const r = await req('GET', `/events?limit=${limit}`)
    return (r.events || []).filter(e => typeof e.type === 'string' && e.type.startsWith('memory_'))
  },
  // Templates
  listTemplates: (params) => req('GET', '/tasks/templates' + (params ? '?' + new URLSearchParams(params).toString() : '')),
  getTemplate: (id) => req('GET', `/tasks/templates/${id}`),
  createTemplate: (body) => req('POST', '/tasks/templates', body),
  updateTemplate: (id, body) => req('PATCH', `/tasks/templates/${id}`, body),
  deleteTemplate: (id) => gated('DELETE', `/tasks/templates/${id}`, '删除模板'),
  createTemplateFromTask: (taskId, body) => req('POST', `/tasks/templates/from-task/${taskId}`, body),
  createTaskFromTemplate: (tplId, body) => req('POST', `/tasks/from-template/${tplId}`, body),
  listTemplateVersions: (tplId) => req('GET', `/tasks/templates/${tplId}/versions`),
  restoreTemplateVersion: (tplId, version) => req('POST', `/tasks/templates/${tplId}/restore/${version}`, {}),
  async metrics() {
    const resp = await fetch('/metrics')
    if (!resp.ok) throw new Error('metrics failed')
    const text = await resp.text()
    const lines = text.split('\n').filter(l => l && !l.startsWith('#'))
    const result = {}
    for (const line of lines) {
      const match = line.match(/^(\w+)\{?([^}]*)\}? ([\d.]+)$/)
      if (match) {
        const [, name, labels, value] = match
        if (labels) {
          const labelMatch = labels.match(/(\w+)="([^"]+)"/)
          if (labelMatch) {
            const [, key, val] = labelMatch
            if (!result[name]) result[name] = []
            result[name].push({ label: key, value: val, count: parseFloat(value) })
          }
        } else {
          result[name] = parseFloat(value)
        }
      }
    }
    return result
  },
  // Scheduled Jobs
  listScheduledJobs: () => req('GET', '/scheduled-jobs'),
  getScheduledJob: (id) => req('GET', `/scheduled-jobs/${id}`),
  createScheduledJob: (body) => req('POST', '/scheduled-jobs', body),
  updateScheduledJob: (id, body) => req('PATCH', `/scheduled-jobs/${id}`, body),
  deleteScheduledJob: (id) => gated('DELETE', `/scheduled-jobs/${id}`, '删除定时任务'),
  triggerScheduledJob: (id) => req('POST', `/scheduled-jobs/${id}/trigger`),
  pauseScheduledJob: (id) => req('POST', `/scheduled-jobs/${id}/pause`),
  resumeScheduledJob: (id) => req('POST', `/scheduled-jobs/${id}/resume`),
  listScheduledJobExecutions: (id) => req('GET', `/scheduled-jobs/${id}/executions`),
  validateCron: (expr) => req('GET', `/scheduled-jobs/validate-cron?expr=${encodeURIComponent(expr)}`),
  // Observability
  observabilitySummary: () => req('GET', '/observability/summary'),
  listAlerts: () => req('GET', '/alerts'),
  taskTraces: (limit = 50) => req('GET', `/traces?limit=${limit}`),
  sloHistory: (hours = 24) => req('GET', `/slo/history?hours=${hours}`),
  listInsights: (limit = 20) => req('GET', `/insights?limit=${limit}`),
  processInfo: () => req('GET', '/process'),
  // Software update
  updateStatus: () => req('GET', '/update/status'),
  updateCheck: () => req('POST', '/update/check'),
  updateDownload: () => req('POST', '/update/download'),
  updateApply: () => gated('POST', '/update/apply', '应用更新并重启 Hub'),
  updateDismiss: () => req('POST', '/update/dismiss'),
  // Mio Agent Runtime (read-only)
  mioStatus: () => req('GET', '/mio/status'),
  mioTraces: (limit = 20) => req('GET', `/mio/traces?limit=${limit}`),
  mioMemory: (limit = 20) => req('GET', `/mio/memory?limit=${limit}`),
  mioCreativity: (limit = 20) => req('GET', `/mio/creativity?limit=${limit}`),
  mioInsight: (limit = 20) => req('GET', `/mio/insight?limit=${limit}`),
  mioFerment: () => req('GET', '/mio/ferment'),
  mioFermentSync: (id) => req('POST', `/mio/ferment/${id}/sync`),
}
