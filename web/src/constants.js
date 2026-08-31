export const LANES = [
  { id: 'queued',    label: '待处理', en: 'QUEUED',    tone: 'dim' },
  { id: 'claimed',   label: '已领取', en: 'CLAIMED',   tone: 'dim' },
  { id: 'running',   label: '进行中', en: 'RUNNING',   tone: 'live' },
  { id: 'retrying',  label: '重试中', en: 'RETRYING',  tone: 'warn' },
  { id: 'completed', label: '已完成', en: 'COMPLETED', tone: 'dim' },
  { id: 'failed',    label: '失败',   en: 'FAILED',    tone: 'danger' },
]

export const STATE_META = Object.fromEntries(LANES.map(l => [l.id, l]))

// 开发阶段看板泳道（拖拽改阶段用）
export const STAGES = [
  { id: 'brainstorming', label: '头脑风暴', en: 'BRAINSTORM', tone: 'dim' },
  { id: 'design',        label: '设计',     en: 'DESIGN',      tone: 'dim' },
  { id: 'planning',      label: '计划',     en: 'PLANNING',    tone: 'dim' },
  { id: 'ready',         label: '就绪',     en: 'READY',       tone: 'live' },
  { id: 'implementing',  label: '实现中',   en: 'IMPLEMENTING', tone: 'live' },
  { id: 'review',        label: '评审',     en: 'REVIEW',      tone: 'warn' },
  { id: 'done',          label: '完成',     en: 'DONE',        tone: 'ok' },
  { id: 'cancelled',     label: '已取消',   en: 'CANCELLED',   tone: 'danger' },
]

export const STAGE_META = Object.fromEntries(STAGES.map(l => [l.id, l]))

export const PRIORITY = [
  { p: 0, label: 'P0', text: '低' },
  { p: 1, label: 'P1', text: '中' },
  { p: 2, label: 'P2', text: '高' },
  { p: 3, label: 'P3', text: '紧急' },
]

export const prio = (p) => PRIORITY[Math.min(3, Math.max(0, p ?? 0))]

export const COMPOSITE_LABELS = {
  'queued,brainstorming':    { label: '待认领 · 需求理解', tone: 'dim' },
  'queued,design':           { label: '待认领 · 设计', tone: 'dim' },
  'queued,planning':         { label: '待认领 · 计划', tone: 'dim' },
  'queued,ready':            { label: '待认领', tone: 'dim' },
  'queued,implementing':     { label: '返工重排', tone: 'warn' },
  'claimed,brainstorming':   { label: '需求理解中', tone: 'dim' },
  'claimed,design':          { label: '设计中', tone: 'dim' },
  'claimed,planning':        { label: '计划中', tone: 'dim' },
  'claimed,ready':           { label: '就绪 · 待执行', tone: 'live' },
  'claimed,implementing':    { label: '已认领 · 实现', tone: 'live' },
  'claimed,review':          { label: '评审中', tone: 'warn' },
  'running,implementing':    { label: '执行中', tone: 'live' },
  'retrying,implementing':   { label: '重试中', tone: 'warn' },
  'completed,implementing':  { label: '实现完成 · 待评审', tone: 'ok-soft' },
  'completed,review':        { label: '评审通过 · 待归并', tone: 'ok-soft' },
  'completed,done':          { label: '已完成', tone: 'ok' },
  'failed,brainstorming':    { label: '失败 · 需求理解', tone: 'danger' },
  'failed,design':           { label: '失败 · 设计', tone: 'danger' },
  'failed,planning':         { label: '失败 · 计划', tone: 'danger' },
  'failed,implementing':     { label: '失败 · 实现', tone: 'danger' },
  'failed,review':           { label: '失败 · 评审不通过', tone: 'danger' },
  'cancelled,brainstorming': { label: '已取消', tone: 'muted' },
  'cancelled,design':        { label: '已取消', tone: 'muted' },
  'cancelled,planning':      { label: '已取消', tone: 'muted' },
  'cancelled,ready':         { label: '已取消', tone: 'muted' },
  'cancelled,implementing':  { label: '已取消', tone: 'muted' },
  'cancelled,review':        { label: '已取消', tone: 'muted' },
}

export const compositeLabel = (state, stage) => {
  return COMPOSITE_LABELS[`${state},${stage}`] || { label: `${state}·${stage}`, tone: 'dim' }
}

export const fmtDur = (min) => {
  if (min == null) return '—'
  if (min < 60) return `${min}m`
  const h = Math.floor(min / 60)
  const m = min % 60
  return m ? `${h}h${m}m` : `${h}h`
}

export const fmtTime = (iso) => {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(+d)) return '—'
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

export const fmtDate = (iso) => {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(+d)) return '—'
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')} ${fmtTime(iso)}`
}

export const fmtAgo = (iso) => {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(+d)) return '—'
  const s = (Date.now() - +d) / 1000
  if (s < 45) return '刚刚'
  if (s < 3600) return `${Math.round(s / 60)} 分钟前`
  if (s < 86400) return `${Math.round(s / 3600)} 小时前`
  return `${Math.round(s / 86400)} 天前`
}

const AG_PALETTE = [
  ['#ffb454', 'rgba(255,180,84,0.14)'],
  ['#7dd3fc', 'rgba(125,211,252,0.14)'],
  ['#f0abfc', 'rgba(240,171,252,0.14)'],
  ['#86efac', 'rgba(134,239,172,0.14)'],
  ['#fca5a5', 'rgba(252,165,165,0.14)'],
  ['#fde047', 'rgba(253,224,71,0.14)'],
  ['#c9f73a', 'rgba(201,247,58,0.14)'],
]

export const agMono = (agent) => {
  if (!agent) return null
  const parts = agent.split('-').filter(Boolean)
  return (parts[0][0] + (parts[1]?.[0] || '')).toUpperCase()
}

export const agColor = (agent) => {
  if (!agent) return null
  let h = 0
  for (let i = 0; i < agent.length; i++) h = (h * 31 + agent.charCodeAt(i)) >>> 0
  const [fg, bg] = AG_PALETTE[h % AG_PALETTE.length]
  return { fg, bg }
}
