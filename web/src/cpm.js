// web/src/cpm.js
// 独立纯函数：关键路径法（CPM）。不依赖 React/DOM，TopoView/FlowView/Dashboard 可复用。
export const DEFAULT_DURATION = 60

// 任务时长（分钟）：已完成=0，进行中按剩余时间退化，其余用 est_duration
export function taskDuration(task) {
  const stage = task.stage
  const state = task.state
  const parsed = Number(task.est_duration_min)
  const est = Number.isFinite(parsed) ? parsed : DEFAULT_DURATION
  if (state === 'completed' || stage === 'done' || stage === 'cancelled') return 0
  if (stage === 'implementing') return Math.round(est * 0.5)
  if (stage === 'review') return Math.round(est * 0.25)
  return est
}

export function computeCPM(tasks) {
  const byId = Object.fromEntries(tasks.map(t => [t.id, t]))
  const dur = {}
  tasks.forEach(t => { dur[t.id] = taskDuration(t) })

  // 拓扑序（Kahn）
  const deps = Object.fromEntries(tasks.map(t => [t.id, (t.depends_on || []).filter(d => byId[d])]))
  const succ = {}
  tasks.forEach(t => { succ[t.id] = [] })
  const indegree = {}
  tasks.forEach(t => { indegree[t.id] = 0 })
  tasks.forEach(t => {
    deps[t.id].forEach(d => { indegree[t.id] += 1; succ[d].push(t.id) })
  })
  const order = []
  const queue = tasks.filter(t => indegree[t.id] === 0).map(t => t.id)
  const seen = new Set(queue)
  while (queue.length) {
    const id = queue.shift()
    order.push(id)
    succ[id].forEach(c => {
      indegree[c] -= 1
      if (indegree[c] === 0 && !seen.has(c)) { seen.add(c); queue.push(c) }
    })
  }

  // 正向：ES / EF
  const es = {}, ef = {}
  tasks.forEach(t => { es[t.id] = 0; ef[t.id] = 0 })
  order.forEach(id => {
    const s = deps[id].length ? Math.max(...deps[id].map(d => ef[d])) : 0
    es[id] = s
    ef[id] = s + dur[id]
  })
  const total = tasks.length ? Math.max(...tasks.map(t => ef[t.id])) : 0

  // 反向：LF / LS
  const ls = {}, lf = {}
  tasks.forEach(t => { lf[t.id] = total; ls[t.id] = total - dur[t.id] })
  ;[...order].reverse().forEach(id => {
    const l = succ[id].length ? Math.min(...succ[id].map(c => ls[c])) : total
    lf[id] = l
    ls[id] = l - dur[id]
  })

  // 浮动
  const float = {}
  tasks.forEach(t => { float[t.id] = ls[t.id] - es[t.id] })

  // 单条关键路径：从 EF==total 的终节点回溯，逐级取 EF 最大的前驱
  const terminals = tasks.filter(t => ef[t.id] === total)
  const start = terminals.length ? terminals[0].id : (tasks[0] ? tasks[0].id : null)
  const criticalPath = []
  let cur = start
  while (cur) {
    criticalPath.push(cur)
    const preds = deps[cur]
    if (!preds.length) break
    cur = preds.reduce((a, b) => (ef[b] > ef[a] ? b : a))
  }
  criticalPath.reverse()
  const critical = {}
  tasks.forEach(t => { critical[t.id] = criticalPath.includes(t.id) })

  // 并行度：max 拓扑层宽
  const layer = {}
  tasks.forEach(t => { layer[t.id] = 0 })
  order.forEach(id => {
    deps[id].forEach(d => { layer[id] = Math.max(layer[id], layer[d] + 1) })
  })
  const layerWidth = {}
  tasks.forEach(t => { layerWidth[layer[t.id]] = (layerWidth[layer[t.id]] || 0) + 1 })
  const parallel = Object.keys(layerWidth).length
    ? Math.max(...Object.values(layerWidth))
    : 0

  return {
    total,
    es, ef, ls, lf, float,
    criticalPath,
    critical,
    parallel,
  }
}
