import { confirm } from './confirm'
import { authHeaders, authFetch, getToken } from './auth'
import { getAdminToken } from './auth'

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

// ── 管理面凭据（F-004）──────────────────────────────────────────────────────
// 与 auth.js 的 API 访问凭据（getToken / mio-taskhub.token）**完全分开**，键名不复用：
// - 未配置 MIO_TASKHUB_ADMIN_TOKEN：服务端认「环回 + 'X-TaskHub-Admin': '1'」这道降级态速度闸
// - 配置之后：服务端只认 X-TaskHub-Admin-Token: <MIO_TASKHUB_ADMIN_TOKEN>，旧头失效
// 两种头同时发，前端不必按部署形态分叉。管理面 token 由运维经 ?admin_token= 一次性
// 注入或 localStorage 键 mio-taskhub.admin-token 录入，读取统一走 auth.js 的
// getAdminToken()（与 API 凭据 getToken() 分流），绝不下发给执行任务的 agent。
// 注意：不要把这个头塞进 req() 的默认值，那等于所有请求都是管理面操作。

export function adminHeaders() {
  const headers = { 'X-TaskHub-Admin': '1' }
  const t = getAdminToken()
  if (t) headers['X-TaskHub-Admin-Token'] = t
  return headers
}

async function req(method, path, body, extraHeaders) {
  // S-4：启用鉴权时统一注入 Authorization: Bearer；无 token 时 authHeaders 原样返回。
  const opts = { method, headers: authHeaders({ 'Content-Type': 'application/json', ...(extraHeaders || {}) }) }
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
// extraHeaders：destructive 端点（F-004 第 3 步服务端收口）要求管理面凭据，
// 确认重试路径必须带上同一组头，否则 409 解除后仍会 403。
async function gated(method, path, label, extraHeaders) {
  try {
    return await req(method, path, undefined, extraHeaders)
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.error_code === 'policy_risk_high') {
      const risk = (e.detail && e.detail.risk) || 'high'
      const sug = (e.detail && e.detail.policy && e.detail.policy.suggestion) || ''
      const ok = await confirm(`【${label}】Mio 历史风险评估：${risk}\n${sug}\n\n仍要继续吗？`,
                               { title: '危险操作', danger: true, okText: '仍要继续' })
      if (!ok) throw e
      const sep = path.includes('?') ? '&' : '?'
      return await req(method, path + sep + 'confirm=true', undefined, extraHeaders)
    }
    throw e
  }
}

export const api = {
  listTasks: (params) => req('GET', '/tasks' + (params ? '?' + new URLSearchParams(params).toString() : '')),
  advanceStage: (id, body) => req('POST', `/tasks/${id}/stage`, body),
  moveToStage: (id, body) => req('POST', `/tasks/${id}/stage/move`, body),
  getTask: (id) => req('GET', `/tasks/${id}`),
  // 阻塞源聚合：前置/死结/子任务/待决策/文档门控/偏差，回答「为什么动不了」
  getTaskBlockers: (id) => req('GET', `/tasks/${id}/blockers`),
  // 执行就绪度：流程约束 + 输入缺口 + 风险 + 建议处理顺序
  getTaskReadiness: (id) => req('GET', `/tasks/${id}/readiness`),
  getTaskDoc: (id, kind) => req('GET', `/tasks/${id}/doc?kind=${kind}`),
  getTaskDocuments: (id) => req('GET', `/tasks/${id}/documents`),
  // 文档生命周期总览：各 kind 当前状态 + states 全序列 + 合法后继
  getDocStatuses: (id) => req('GET', `/tasks/${id}/doc/statuses`),
  // 推进文档生命周期状态（body: {state, note?, force?}）；受质量门约束，422 带缺口明细。
  // F-004：验收动作需管理面凭据，带 adminHeaders()。
  setDocStatus: (id, kind, body) => req('POST', `/tasks/${id}/doc/${kind}/status`, body, adminHeaders()),
  getTaskFile: (id, path) => req('GET', `/tasks/${id}/file?path=${encodeURIComponent(path)}`),
  // 原样返回 workspace 内文件（二进制安全），用于 <img src> 等直接引用。
  // 无 token 时保持原行为；启用鉴权时标签请求带不上 header，调用方改用 rawFileBlobUrl。
  rawFileUrl: (id, path) => `${BASE}/tasks/${id}/raw?path=${encodeURIComponent(path)}`,
  hasToken: () => !!getToken(),
  // S-4 通道 ④：带 Authorization 取回 /raw 内容，转成 blob URL 供 <img src> / <a href> 使用。
  // 调用方须在组件卸载或内容替换时 URL.revokeObjectURL，避免泄漏。
  rawFileBlobUrl: async (id, path) => {
    const r = await authFetch(`${BASE}/tasks/${id}/raw?path=${encodeURIComponent(path)}`)
    if (!r.ok) throw new Error(`raw HTTP ${r.status}`)
    return URL.createObjectURL(await r.blob())
  },
  getTaskGraph: (id) => req('GET', `/tasks/${id}/graph`),
  getWorkstream: (id) => req('GET', `/tasks/${id}/workstream`),  getFullGraph: () => req('GET', '/tasks/graph'),
  createTask: (t) => req('POST', '/tasks', t),
  // F-004：PATCH 的 workspace/files/deliverables/target_agent_type/depends_on
  // 属管理面字段，服务端逐字段校验；前端统一带 adminHeaders()，普通字段不受影响。
  updateTask: (id, body) => req('PATCH', `/tasks/${id}`, body, adminHeaders()),
  addSubtask: (id, body) => req('POST', `/tasks/${id}/subtasks`, body),
  updateSubtask: (id, sid, body) => req('PATCH', `/tasks/${id}/subtasks/${sid}`, body),
  addDiscussion: (id, body) => req('POST', `/tasks/${id}/discussions`, body),
  claim: (agent) => req('POST', `/tasks/claim?agent=${encodeURIComponent(agent)}`),
  // F-004 第 3 步：DELETE /tasks/{id} 是 destructive 端点，服务端要求管理面凭据
  // （REST 直连同判）。前端带 adminHeaders()，与 setDocStatus / updateTask 同口径。
  cancelTask: (id) => gated('DELETE', `/tasks/${id}`, '取消任务', adminHeaders()),
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
  // 危险端点（拉起/停止本机进程）：后端要求管理员确认头。F-004 后统一走
  // adminHeaders()——强隔离部署（配了 MIO_TASKHUB_ADMIN_TOKEN）时旧的 X-TaskHub-Admin: 1
  // 会失效，必须同时提供 X-TaskHub-Admin-Token，否则这组按钮会 403。
  nrSaveConfig: (cfg) => req('PUT', '/nightrun/config', cfg, adminHeaders()),
  nrStop: () => req('POST', '/nightrun/stop', {}, adminHeaders()),
  nrSpawnNow: (agents) => req('POST', '/nightrun/spawn-now', agents, adminHeaders()),
  nrStatus: () => req('GET', '/nightrun/full-config'),
  nrCronTasks: () => req('GET', '/nightrun/cron-tasks'),
  // Reviews
  reviewQueue: () => req('GET', '/tasks/reviews/queue'),
  listReviews: (taskId) => req('GET', `/tasks/${taskId}/reviews`),
  submitReview: (taskId, body) => req('POST', `/tasks/${taskId}/reviews`, body),
  listIdeas: (params) => req('GET', '/ideas' + (params ? '?' + new URLSearchParams(params).toString() : '')),
  getIdea: (id, params) => req('GET', `/ideas/${id}` + (params ? '?' + new URLSearchParams(params).toString() : '')),
  getIdeaCockpit: (id) => req('GET', `/ideas/${id}/cockpit`),
  dismissIdeaNextAction: (id, ruleId) => req('POST', `/ideas/${id}/next-action/dismiss`, { rule_id: ruleId }),
  createIdea: (body) => req('POST', '/ideas', body),
  updateIdea: (id, body) => req('PATCH', `/ideas/${id}`, body),
  // P5 FR-34：LLM 生成驾驶舱 8 字段草稿（不落库，返回 {draft, source, model, elapsed_ms}）
  draftIdeaFields: (id, body) => req('POST', `/ideas/${id}/draft-fields`, body || {}),
  // 观测守护：一键拉起（幂等；观察器 + 研究调度器）
  mioObserverStart: () => req('POST', '/mio/observer/start', {}),
  // 想法落地闭环 P1（FR-12/FR-15）：假设导入 + 单条人工回写
  importIdeaHypotheses: (id, ids) => req('POST', `/ideas/${id}/hypotheses/import`, { ids }),
  patchIdeaAssumption: (id, hid, body) => req('PATCH', `/ideas/${id}/assumptions/${hid}`, body),
  advanceIdea: (id, status) => req('POST', `/ideas/${id}/status`, { status }),
  openDiscussion: (body) => req('POST', '/discussions', body),
  getDiscussion: (id) => req('GET', `/discussions/${id}`),
  listDiscussions: (refType, refId) => req('GET', `/discussions?ref_type=${refType}&ref_id=${refId}`),
  replyDiscussion: (id, body) => req('POST', `/discussions/${id}/messages`, body),
  closeDiscussion: (id, body) => req('POST', `/discussions/${id}/close`, body),
  // FR-22：行动项幂等转任务（body: {item_ids?}）
  convertActionItems: (id, body) => req('POST', `/discussions/${id}/convert`, body || {}),
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
    const r = await authFetch('/api/v1/memory/health')
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
    const resp = await authFetch('/metrics')
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
  observabilityReport: () => req('GET', '/observability/report'),
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
  mioFermentRun: (limit = 5) => req('POST', `/mio/creativity/ferment?limit=${limit}`),
  // TH-01：历史决策闭环（待确认经验 / 决策反查 / 写回投递）
  mioExperiencePending: (project = '', limit = 20) =>
    req('GET', `/mio/experience/pending?project=${encodeURIComponent(project)}&limit=${limit}`),
  mioExperienceConfirm: (id, by = 'taskhub:human') =>
    req('POST', `/mio/experience/${encodeURIComponent(id)}/confirm`, { by }),
  mioRouteDecision: (runId) => req('GET', `/mio/route-decisions/${encodeURIComponent(runId)}`),
  mioOutbox: (params = {}) => req('GET', '/mio/outbox?' + new URLSearchParams(params).toString()),
  mioMaterialPanel: (days = 7, limit = 200) => req('GET', `/mio/material-panel?days=${days}&limit=${limit}`),
  valueDiscoveryRun: (date, internalIssues, label) => req('POST', '/mio/value-discovery', { date, internal_issues: internalIssues, label }),
  valueDiscoveryList: () => req('GET', '/mio/value-discovery'),
  schedulerStatus: () => req('GET', '/mio/scheduler-status'),
  schedulerActivate: () => req('POST', '/mio/scheduler-activate', {}),
  schedulerRestart: () => req('POST', '/mio/scheduler-restart', {}),
  schedulerStop: () => req('POST', '/mio/scheduler-stop', {}),

  // Decision 实体（任务 89a90af6）：待裁决决策
  listDecisions: (status = 'open', limit = 50) =>
    req('GET', `/decisions?status=${encodeURIComponent(status)}&limit=${limit}`),
  resolveDecision: (id, body) => req('POST', `/decisions/${id}/resolve`, body),
  supersedeDecision: (id, body) => req('POST', `/decisions/${id}/supersede`, body || {}),

  // Gate Status
  gateStatus: () => req('GET', '/ideas/gate/status'),
  forceGenerate: (jobId = '1271eac5', reason) => req('POST', `/ideas/gate/force-generate?job_id=${encodeURIComponent(jobId)}`, reason ? { reason } : {}),
  listInbox: (params) => req('GET', '/ideas/inbox' + (params ? '?' + new URLSearchParams(params).toString() : '')),
  triageIdea: (id, action, actor = 'user') => req('POST', `/ideas/${id}/triage`, { action, actor }),
  batchTriage: (ideaIds, action, actor = 'user') => req('POST', '/ideas/batch-triage', { idea_ids: ideaIds, action, actor }),
}
