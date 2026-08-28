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
