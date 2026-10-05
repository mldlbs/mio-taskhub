// web/src/cpm.js
// 独立纯函数：关键路径法（CPM）。不依赖 React/DOM，TopoView/FlowView/Dashboard 可复用。
export const DEFAULT_DURATION = 60

// 关键路径 float 容差；criticalPath（精确 float==0）与 critical（容差内）在 float∈(0,0.5] 时可能不一致
export const EPSILON = 0.5

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

export function computeCPM(tasks, opts = {}) {
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
  // 环检测：order 未覆盖的节点属于环（含自环）。CPM 假定 DAG，
  // 环节点 es/ef/float 是垃圾值，后面从关键路径计算中防御性忽略。
  const inOrder = new Set(order)

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

  // 单条关键路径：从 EF==total 的终节点回溯，逐级取 EF 最大的前驱。
  // 仅从拓扑序内终节点回溯（环节点已在 order 中排除，防死循环）
  const terminals = tasks.filter(t => inOrder.has(t.id) && ef[t.id] === total)
  const start = terminals.length ? terminals[0].id : null
  const criticalPath = []
  let cur = start
  const seenPath = new Set()
  while (cur && !seenPath.has(cur)) {
    seenPath.add(cur)  // 环防护
    criticalPath.push(cur)
    const preds = deps[cur]
    if (!preds.length) break
    cur = preds.reduce((a, b) => (ef[b] > ef[a] ? b : a))
  }
  criticalPath.reverse()
  const critical = {}

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

  // 多关键路径：float≈0 节点子图的 DFS，收集全部 root→terminal 路径
  const crit = {}
  tasks.forEach(t => { crit[t.id] = float[t.id] <= EPSILON })
  // 环节点不在拓扑序中，float 为垃圾值；从关键路径计算中排除
  // （CPM 假定 DAG，环输入被防御性忽略）
  if (order.length < tasks.length) {
    tasks.forEach(t => { if (!inOrder.has(t.id)) crit[t.id] = false })
  }
  const isRoot = id => deps[id].length === 0
  const isTerminal = id => (succ[id] || []).length === 0
  const allCriticalPaths = []
  const dfs = (id, path, onPath) => {
    if (onPath.has(id)) return  // 环防护
    const cur = path.concat(id)
    if (isTerminal(id)) { allCriticalPaths.push(cur); return }
    const nextOn = new Set(onPath).add(id)
    ;(succ[id] || []).forEach(c => { if (crit[c]) dfs(c, cur, nextOn) })
  }
  tasks.forEach(t => { if (crit[t.id] && isRoot(t.id)) dfs(t.id, [], new Set()) })

  // critical 标志 = 在任一关键路径上
  const onAny = new Set()
  allCriticalPaths.forEach(p => p.forEach(id => onAny.add(id)))
  tasks.forEach(t => { critical[t.id] = onAny.has(t.id) })

  // 并行度曲线：扫描线 [ES, EF) 区间覆盖计数
  const ev = []
  tasks.forEach(t => {
    if (dur[t.id] <= 0) return
    ev.push({ t: es[t.id], d: +1 })
    ev.push({ t: ef[t.id], d: -1 })
  })
  ev.sort((a, b) => a.t - b.t || a.d - b.d)
  const parallelProfile = []
  let curCount = 0
  let prevT = null
  ev.forEach(e => {
    if (prevT !== null && curCount > 0 && e.t > prevT) {
      parallelProfile.push({ start: prevT, end: e.t, count: curCount })
    }
    curCount += e.d
    prevT = e.t
  })

  // 资源冲突：超阈值区间（默认阈值 = target_agent_type 去重数，无则不标注）
  // 全局并行度告警启发式：并行任务总数超过可用 agent 数，而非同一 agent 争用检测（spec A3 已确认）
  const agentSet = new Set(tasks.map(t => t.target_agent_type).filter(Boolean))
  const maxParallel = opts.maxParallel !== undefined ? opts.maxParallel : (agentSet.size || null)
  const conflicts = maxParallel != null ? parallelProfile.filter(s => s.count > maxParallel) : []
  const resourceConflicts = conflicts.reduce((acc, s) => {
    const last = acc[acc.length - 1]
    if (last && last.end === s.start && last.count === s.count) last.end = s.end
    else acc.push({ start: s.start, end: s.end, count: s.count })
    return acc
  }, [])

  return {
    total,
    es, ef, ls, lf, float,
    criticalPath,
    critical,
    parallel,
    allCriticalPaths,
    parallelProfile,
    resourceConflicts,
  }
}
