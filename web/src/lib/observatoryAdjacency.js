/**
 * 记忆观测邻接表（FR-7）：relations 单次 O(E) 构建，供 MemoryObservatoryView
 * 详情面板 ego 出入边渲染复用，避免每次渲染遍历全量边。
 *
 * 输入关系形如 { source: number, target: number, rel: string }（实体顺序编号）。
 * 纯函数：无副作用、无 React 依赖，可独立单测。
 */

export function buildAdjacency(relations = []) {
  const out = new Map()
  const inc = new Map()
  for (const r of relations) {
    if (!r || typeof r.source !== 'number' || typeof r.target !== 'number') continue
    if (!out.has(r.source)) out.set(r.source, [])
    out.get(r.source).push({ to: r.target, rel: r.rel || '' })
    if (!inc.has(r.target)) inc.set(r.target, [])
    inc.get(r.target).push({ from: r.source, rel: r.rel || '' })
  }
  return { out, inc }
}

/** 取单节点 ego 出入边（缺失 → 空数组）。 */
export function nodeEdges(adj, id) {
  if (!adj) return { out: [], inc: [] }
  return { out: adj.out.get(id) || [], inc: adj.inc.get(id) || [] }
}
