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

console.log('CPM verify OK:', JSON.stringify({ total: r.total, path: r.criticalPath, parallel: r.parallel }))
