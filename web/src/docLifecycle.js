// 文档生命周期状态的展示映射。
//
// 与后端 mio_taskhub/doc_lifecycle.py 的 DOC_LIFECYCLE 保持一致（8 类文档）。
// 后端状态存 task.doc_statuses[kind] = { state, at, note }，清单接口
// GET /tasks/{id}/documents 的每条条目带 status 字段；总览接口
// GET /tasks/{id}/doc/statuses 额外给出 states 全序列与 allowed_next。
//
// 这里只做「状态 → 中文名 + 色调」的纯映射，不含副作用，供 DocPanel 等复用。
// 词表变更时请同步本文件。

// 状态 → 中文名（跨 kind 共用同一套词）
export const DOC_STATE_LABELS = {
  draft: '草稿',
  review: '待审',
  approved: '已批准',
  proposed: '提议',
  accepted: '已接受',
  superseded: '已取代',
  planned: '已计划',
  done: '已完成',
  passed: '通过',
  failed: '未通过',
  released: '已发布',
  open: '待处理',
  resolved: '已解决',
  closed: '已关闭',
}

// 状态 → 色调：
//   pending   起点/未开始推进
//   attention 待人工审核（卡在这里需要有人/agent 动作）
//   positive  目标达成
//   danger    负向终态（需处理）
//   neutral   归档态（不再变化，但不是失败）
const DOC_STATE_TONES = {
  draft: 'pending',
  proposed: 'pending',
  planned: 'pending',
  open: 'pending',
  review: 'attention',
  approved: 'positive',
  accepted: 'positive',
  done: 'positive',
  passed: 'positive',
  released: 'positive',
  resolved: 'positive',
  failed: 'danger',
  superseded: 'neutral',
  closed: 'neutral',
}

export const docStateLabel = (state) =>
  state ? (DOC_STATE_LABELS[state] || state) : '未落状态'

export const docStateTone = (state) => DOC_STATE_TONES[state] || 'pending'

// 清单条目 / doc_statuses 条目 → 状态字符串（两者形状不同，统一在这里取）
//   清单条目:   { kind, status: { state, at, note } | null }
//   statuses:   { state, at, note, states, allowed_next, has_doc }
export const stateOfDoc = (doc) => (doc && doc.status && doc.status.state) || null

export const stateOfStatus = (entry) => (entry && entry.state) || null

// 生命周期推进条：把 states 全序列标注为 done / current / todo
// 返回 [{ state, label, tone, phase }]，phase ∈ done | current | todo
export function lifecycleTrack(states, current) {
  const list = Array.isArray(states) ? states : []
  const idx = current ? list.indexOf(current) : -1
  return list.map((s, i) => ({
    state: s,
    label: docStateLabel(s),
    tone: docStateTone(s),
    phase: idx === -1 ? 'todo' : i < idx ? 'done' : i === idx ? 'current' : 'todo',
  }))
}
