// node scripts/verify-cpm.mjs
import { computeCPM } from '../src/cpm.js'

const tasks = [
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'b', est_duration_min: 60,  depends_on: ['a'], state: 'queued', stage: 'ready' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'd', est_duration_min: 30,  depends_on: ['b'], state: 'queued', stage: 'ready' },
]
// 依赖: a→b→d, c 独立并行。a+b+d=210 是关键路径, c 浮余 30
const r = computeCPM(tasks)
console.assert(r.total === 210, `total=${r.total} expected 210`)
console.assert(r.criticalPath.join(',') === 'a,b,d', `path=${r.criticalPath}`)
console.assert(r.critical.a && r.critical.b && r.critical.d && !r.critical.c, 'critical flags')
console.assert(r.float.c === 30, `float.c=${r.float.c}`)

// 已完成任务不阻塞
const done = [
  { id: 'x', est_duration_min: 999, depends_on: [], state: 'completed', stage: 'done' },
  { id: 'y', est_duration_min: 60, depends_on: ['x'], state: 'queued', stage: 'ready' },
]
const r2 = computeCPM(done)
console.assert(r2.total === 60, `done total=${r2.total}`)

// 无 est_duration 用默认值
const noEst = [{ id: 'n', depends_on: [], state: 'queued', stage: 'ready' }]
const r3 = computeCPM(noEst)
console.assert(r3.total === 60, `noEst total=${r3.total}`)

// 悬挂依赖忽略
const dangling = [
  { id: 'p', est_duration_min: 30, depends_on: ['ghost'], state: 'queued', stage: 'ready' },
]
const r4 = computeCPM(dangling)
console.assert(r4.total === 30, `dangling total=${r4.total}`)

// 多关键路径: a→b→d 与 c→d 都是 float≈0 的关键路径
const r5 = computeCPM([
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'b', est_duration_min: 60,  depends_on: ['a'], state: 'queued', stage: 'ready' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'd', est_duration_min: 30,  depends_on: ['b', 'c'], state: 'queued', stage: 'ready' },
])
// a+b+d = 210, c+d = 210 → 两条关键路径
const paths5 = r5.allCriticalPaths.map(p => p.join(',')).sort()
console.assert(JSON.stringify(paths5) === JSON.stringify(['a,b,d', 'c,d']),
  `allCriticalPaths=${JSON.stringify(paths5)} expected ['a,b,d','c,d']`)
console.assert(r5.critical.a && r5.critical.b && r5.critical.c && r5.critical.d,
  'critical 应为任一关键路径上的节点')
console.assert(r5.criticalPath.length === 3, `单条 criticalPath=${r5.criticalPath}`)

// parallelProfile: a[0,120) c[0,180) 重叠 [0,120) count=2, [120,180) count=1(c), [180,210) count=1(d 在 180 起, 与 c 无重叠)
const r6 = computeCPM([
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'd', est_duration_min: 30,  depends_on: ['c'], state: 'queued', stage: 'ready' },
])
const seg6 = r6.parallelProfile.map(s => `${s.start}-${s.end}:${s.count}`)
console.assert(JSON.stringify(seg6) === JSON.stringify(['0-120:2', '120-180:1', '180-210:1']),
  `parallelProfile=${JSON.stringify(seg6)}`)
console.assert(r6.parallel === 2, `parallel=${r6.parallel}`)

// resourceConflicts: 无 target_agent_type 时默认无阈值 → 空
console.assert(r6.resourceConflicts.length === 0, `默认无 agent 信息 resourceConflicts=${r6.resourceConflicts.length}`)

// 显式 maxParallel=1 → [0,120) count=2 是唯一冲突段
const r7 = computeCPM([
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready' },
  { id: 'd', est_duration_min: 30,  depends_on: ['c'], state: 'queued', stage: 'ready' },
], { maxParallel: 1 })
const seg7 = r7.resourceConflicts.map(s => `${s.start}-${s.end}:${s.count}`)
console.assert(JSON.stringify(seg7) === JSON.stringify(['0-120:2']), `resourceConflicts=${JSON.stringify(seg7)}`)

// target_agent_type 去重数作为默认阈值: 2 个 agent → [0,120) count=2 不冲突
const r8 = computeCPM([
  { id: 'a', est_duration_min: 120, depends_on: [], state: 'queued', stage: 'ready', target_agent_type: 'claude' },
  { id: 'c', est_duration_min: 180, depends_on: [], state: 'queued', stage: 'ready', target_agent_type: 'codex' },
  { id: 'd', est_duration_min: 30,  depends_on: ['c'], state: 'queued', stage: 'ready', target_agent_type: 'codex' },
])
console.assert(r8.resourceConflicts.length === 0, `默认 agent 阈值 resourceConflicts=${r8.resourceConflicts.length}`)

console.log('CPM verify OK:', JSON.stringify({ total: r.total, path: r.criticalPath, parallel: r.parallel }))
