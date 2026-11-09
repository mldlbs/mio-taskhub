// 机器噪声任务判定（E7 实验结论落地，2026-11-04）
//
// 背景：看板曾同时承载 1771 个任务，其中 1562 个（88%）是机器自动生成的
// 「不需要人决策」的卡片——创意引擎产出的想法评审/变更/溯因任务、洞察消费
// 类任务、自测任务。真实决策信息被稀释 9:1，用户会直接放弃看板。
//
// 规则必须与 `scripts/e7_decision_scan.py` 保持一致（同一份口径，两边各存一份
// 是因为前端不能跑 python、脚本不能跑 js；改规则时两处同改）。
const NOISE_AGENT_TYPES = new Set(['idea-reviewer'])
const NOISE_TITLE_RE = /想法评审|^\[insight\]|^\[变更\]|^\[溯因\]|^__verify_/

export function isNoiseTask(t) {
  if (!t) return false
  if (NOISE_AGENT_TYPES.has(t.target_agent_type || '')) return true
  return NOISE_TITLE_RE.test(t.title || '')
}

export function countNoiseTasks(tasks) {
  if (!Array.isArray(tasks)) return 0
  return tasks.reduce((n, t) => n + (isNoiseTask(t) ? 1 : 0), 0)
}
