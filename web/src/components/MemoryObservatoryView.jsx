import { useEffect, useMemo, useRef, useState } from 'react'
import { buildAdjacency, nodeEdges } from '../lib/observatoryAdjacency'

/**
 * Rail「记忆观测」（FR-7）：只读观测 · Mio 为唯一数据源。
 * 仅调 GET /api/v1/memory/observatory/data（FR-1），不写入、不做权威检索。
 */

// 类型徽标（色阶取 hermes-graph.html palette）
const TYPE_META = {
  rule: { label: '决策', tag: 'rgba(16,185,129,.12)', fg: '#34D399' },
  context: { label: '上下文', tag: 'rgba(167,139,250,.12)', fg: '#C4B5FD' },
  problem: { label: '问题', tag: 'rgba(239,68,68,.12)', fg: '#F87171' },
  experience: { label: '经验', tag: 'rgba(34,211,238,.12)', fg: '#67E8F9' },
  note: { label: '笔记', tag: 'rgba(56,189,248,.12)', fg: '#7DD3FC' },
  log: { label: '日志', tag: 'rgba(71,85,105,.12)', fg: '#94A3B8' },
  source: { label: '来源', tag: 'rgba(100,116,139,.14)', fg: '#94A3B8' },
  project: { label: '项目', tag: 'rgba(59,130,246,.12)', fg: '#60A5FA' },
}
const TYPE_ORDER = ['rule', 'context', 'problem', 'experience', 'note', 'log', 'source', 'project']
const DEF_META = { label: '其他', tag: 'rgba(122,92,26,.12)', fg: '#c4a87a' }
const typeMeta = (t) => TYPE_META[t] || DEF_META

// 复用热度阈值（对齐 hermes-graph.html reuseTier：≥8 高 / ≥3 中 / 其余低）；
// 基于复用证据的呈现信号，非权威价值判断（无证据 ≠ 无价值）。
const HIGH = 8
const MID = 3
function reuseTier(score) {
  const s = score || 0
  if (s >= HIGH) return { label: '复用高', bg: 'rgba(16,185,129,.15)', fg: '#34D399' }
  if (s >= MID) return { label: '复用中', bg: 'rgba(245,158,11,.15)', fg: '#FBBF24' }
  return { label: '复用低', bg: 'rgba(100,116,139,.16)', fg: '#94A3B8' }
}

const firstContent = (e) => (e.obs && (e.obs[1] || e.obs[0])) || ''

export default function MemoryObservatoryView() {
  const [data, setData] = useState(null)
  const [showLogs, setShowLogs] = useState(false)
  const [typeFilter, setTypeFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [selectedId, setSelectedId] = useState(null)
  const [aggTab, setAggTab] = useState('project')
  const [fragTab, setFragTab] = useState(false)
  const [err, setErr] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reloadKey, setReloadKey] = useState(0)
  const [fragFb, setFragFb] = useState('')
  const fbTimer = useRef(null)

  useEffect(() => {
    const ac = new AbortController()
    setLoading(true)
    setErr(null)
    fetch(`/api/v1/memory/observatory/data${showLogs ? '?logs=1' : ''}`, { signal: ac.signal })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((d) => { setData(d); setLoading(false) })
      .catch((e) => {
        if (e && e.name === 'AbortError') return
        setErr(e?.message || String(e))
        setLoading(false)
      })
    return () => ac.abort()
  }, [showLogs, reloadKey])

  const entities = useMemo(() => data?.entities || [], [data])
  const meta = data?.meta || null
  const adj = useMemo(() => buildAdjacency(data?.relations || []), [data])
  const byId = useMemo(() => {
    const m = new Map()
    for (const e of entities) m.set(e.id, e)
    return m
  }, [entities])

  const typeCounts = useMemo(() => {
    const c = {}
    for (const e of entities) c[e.type] = (c[e.type] || 0) + 1
    return c
  }, [entities])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return entities.filter((e) => {
      if (typeFilter !== 'all' && e.type !== typeFilter) return false
      if (!q) return true
      if ((e.title || e.name || '').toLowerCase().includes(q)) return true
      return (e.obs || []).join('\n').toLowerCase().includes(q)
    })
  }, [entities, typeFilter, query])

  const selected = selectedId != null ? byId.get(selectedId) || null : null

  const aggregates = useMemo(() => {
    const key = aggTab === 'source' ? 'source' : 'project'
    return entities
      .filter((e) => e.type === key)
      .map((node) => {
        const { inc } = nodeEdges(adj, node.id)
        const mems = inc.map((i) => byId.get(i.from)).filter((e) => e && e.type !== key)
        return {
          node,
          memCount: mems.length,
          ruleCount: mems.filter((m) => m.type === 'rule').length,
          highCount: mems.filter((m) => (m.reuse_score || 0) >= HIGH).length,
        }
      })
      .sort((a, b) => b.memCount - a.memCount || a.node.title.localeCompare(b.node.title))
  }, [entities, byId, adj, aggTab])

  const fragments = useMemo(
    () => entities
      .filter((e) => e.type === 'rule' || e.type === 'experience')
      .slice()
      .sort((a, b) => (b.reuse_score || 0) - (a.reuse_score || 0)),
    [entities],
  )

  function fragmentsMarkdown() {
    let md = `# 提示词片段\n\n共 ${fragments.length} 条\n\n`
    for (const f of fragments) {
      const line = firstContent(f)
      md += `- ${f.title || f.name}（${typeMeta(f.type).label} · ${reuseTier(f.reuse_score).label}）：${line}（来源：${f.name}）\n`
    }
    return md
  }

  function flash(msg) {
    setFragFb(msg)
    if (fbTimer.current) clearTimeout(fbTimer.current)
    fbTimer.current = setTimeout(() => setFragFb(''), 3000)
  }

  async function copyFragments() {
    if (!fragments.length) return
    try {
      await navigator.clipboard.writeText(fragmentsMarkdown())
      flash(`✓ 已复制 ${fragments.length} 条`)
    } catch {
      flash('✗ 复制失败')
    }
  }

  function downloadFragments() {
    if (!fragments.length) return
    const blob = new Blob([fragmentsMarkdown()], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'mio-observatory-fragments.md'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    flash(`✓ mio-observatory-fragments.md 已下载（${fragments.length} 条）`)
  }

  const reload = () => setReloadKey((k) => k + 1)
  const ev = meta?.evidence || { memTotal: 0, memWithEvidence: 0, reuseRecords: 0 }
  const chips = TYPE_ORDER.filter((t) => typeCounts[t])

  return (
    <div className="obs-view">
      <div className="obs-head">
        <h2 className="obs-title">记忆观测</h2>
        <span className="detail-muted">只读观测 · Mio 为唯一数据源</span>
        <span className="obs-ev">
          复用证据 {ev.memWithEvidence}/{ev.memTotal}
          <span className="detail-muted">（无复用证据 ≠ 无价值）</span>
        </span>
        <span className="detail-muted">{ev.reuseRecords} 次复用记录</span>
        <button
          className="btn btn--ghost btn--xs"
          onClick={() => setShowLogs((v) => !v)}
          title={showLogs ? '收起 task-outcome 日志实体' : '展开 task-outcome 日志实体'}
        >
          日志 {showLogs ? '开' : '关'}
        </button>
        <button className="btn btn--ghost btn--xs" onClick={reload}>刷新</button>
      </div>

      {err && (
        <div className="obs-err">
          加载失败：{err}
          <button className="btn btn--ghost btn--xs" onClick={reload}>重试</button>
        </div>
      )}

      <div className="obs-tools">
        <div className="obs-chips" role="tablist" aria-label="类型筛选">
          <button
            className={`obs-chip${typeFilter === 'all' ? ' is-active' : ''}`}
            onClick={() => setTypeFilter('all')}
          >
            全部 {entities.length}
          </button>
          {chips.map((t) => (
            <button
              key={t}
              className={`obs-chip${typeFilter === t ? ' is-active' : ''}`}
              style={typeFilter === t ? { background: typeMeta(t).tag, color: typeMeta(t).fg } : undefined}
              onClick={() => setTypeFilter(t)}
            >
              {typeMeta(t).label} {typeCounts[t]}
            </button>
          ))}
        </div>
        <label className="obs-filter">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="本地过滤（仅当前已加载数据）"
            aria-label="本地过滤"
          />
          <span className="obs-filter__note">非权威检索</span>
        </label>
      </div>

      {loading && !data && <p className="detail-muted">加载中…</p>}
      {!loading && !err && data && entities.length === 0 && (
        <p className="obs-empty">MIO_HOME 暂无记忆数据（memory.jsonl / experience_reuse.jsonl）。</p>
      )}

      {data && entities.length > 0 && (
        <>
          <div className={`obs-body${selected ? ' has-detail' : ''}`}>
            <div className="obs-grid">
              {visible.map((e) => {
                const tm = typeMeta(e.type)
                const tier = reuseTier(e.reuse_score)
                return (
                  <div
                    key={e.id}
                    className={`obs-card${selectedId === e.id ? ' is-active' : ''}`}
                    role="button"
                    tabIndex={0}
                    title={e.obs && e.obs[0] ? e.obs[0] : ''}
                    onClick={() => setSelectedId(e.id)}
                    onKeyDown={(ev2) => { if (ev2.key === 'Enter') setSelectedId(e.id) }}
                  >
                    <span className="obs-card__type" style={{ background: tm.tag, color: tm.fg }}>{tm.label}</span>
                    <span className="obs-card__tier" style={{ background: tier.bg, color: tier.fg }}>{tier.label}</span>
                    <div className="obs-card__name">{e.title || e.name}</div>
                    <div className="obs-card__id mono">{e.name}</div>
                  </div>
                )
              })}
              {visible.length === 0 && (
                <p className="detail-muted">当前筛选无匹配（仅本地过滤已加载数据）。</p>
              )}
            </div>

            {selected && (
              <aside className="obs-panel" aria-label="实体详情">
                <div className="obs-panel__head">
                  <span className="obs-card__type" style={{ background: typeMeta(selected.type).tag, color: typeMeta(selected.type).fg }}>
                    {typeMeta(selected.type).label}
                  </span>
                  <span className="obs-card__tier" style={{ background: reuseTier(selected.reuse_score).bg, color: reuseTier(selected.reuse_score).fg }}>
                    {reuseTier(selected.reuse_score).label}
                  </span>
                  <button className="btn btn--ghost btn--xs" onClick={() => setSelectedId(null)}>关闭</button>
                </div>
                <h3 className="obs-panel__title">{selected.title || selected.name}</h3>
                <p className="obs-panel__meta mono">{selected.obs && selected.obs[0] ? selected.obs[0] : selected.name}</p>
                <div className="obs-panel__body">
                  {(selected.obs || []).slice(1).map((line, i) => (
                    <p key={i}>{line}</p>
                  ))}
                </div>
                <DetailEdges adj={adj} byId={byId} node={selected} onJump={setSelectedId} />
              </aside>
            )}
          </div>

          <div className="obs-agg">
            <div className="obs-agg__tabs">
              <span className="obs-sec">聚合面板</span>
              <button className={`obs-agg__tab${aggTab === 'project' ? ' is-active' : ''}`} onClick={() => setAggTab('project')}>按项目</button>
              <button className={`obs-agg__tab${aggTab === 'source' ? ' is-active' : ''}`} onClick={() => setAggTab('source')}>按来源</button>
            </div>
            <table className="obs-agg__table">
              <thead>
                <tr>
                  <th>{aggTab === 'source' ? '来源' : '项目'}</th>
                  <th>记忆数</th>
                  <th>决策数</th>
                  <th>高复用数</th>
                </tr>
              </thead>
              <tbody>
                {aggregates.length === 0 && (
                  <tr><td colSpan={4} className="detail-muted">无</td></tr>
                )}
                {aggregates.map((a) => (
                  <tr key={a.node.id}>
                    <td>
                      <button className="obs-link" onClick={() => setSelectedId(a.node.id)}>
                        {a.node.title || a.node.name}
                      </button>
                    </td>
                    <td>{a.memCount}</td>
                    <td>{a.ruleCount}</td>
                    <td>{a.highCount}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="obs-frag">
            <div className="obs-frag__head" onClick={() => setFragTab((v) => !v)}>
              <span className="obs-sec">提示词片段 {fragTab ? '▾' : '▸'}</span>
              <span className="detail-muted">启发式片段，非权威规则（约 30% 可能非真规范）</span>
              {fragTab && (
                <span className="obs-frag__actions" onClick={(e) => e.stopPropagation()}>
                  <button className="btn btn--ghost btn--xs" onClick={copyFragments}>复制 Markdown</button>
                  <button className="btn btn--ghost btn--xs" onClick={downloadFragments}>下载 .md</button>
                  {fragFb && <span className="obs-frag__fb">{fragFb}</span>}
                </span>
              )}
            </div>
            {fragTab && (
              <ul className="obs-frag__list">
                {fragments.length === 0 && <li className="detail-muted">无 rule/experience 实体</li>}
                {fragments.map((f) => (
                  <li key={f.id} className="obs-frag__item">
                    <div className="obs-frag__row">
                      <span className="obs-frag__title">{f.title || f.name}</span>
                      <span className="obs-card__tier" style={{ background: reuseTier(f.reuse_score).bg, color: reuseTier(f.reuse_score).fg }}>
                        {reuseTier(f.reuse_score).label}
                      </span>
                    </div>
                    <div className="obs-frag__line">{firstContent(f)}</div>
                    <button className="obs-link mono" onClick={() => setSelectedId(f.id)}>↗ {f.name}</button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <p className="detail-muted obs-foot">
            数据只读自 MIO_HOME（memory.jsonl / experience_reuse.jsonl），由 mio-agent-runtime 维护；
            本地过滤仅作用于已加载数据（非权威检索），不写回记忆、不改动任务/文档数据。
            {meta?.skipped && (meta.skipped.mio > 0 || meta.skipped.reuse > 0)
              ? ` 坏行跳过 mio=${meta.skipped.mio} reuse=${meta.skipped.reuse}。`
              : ''}
          </p>
        </>
      )}
    </div>
  )
}

/** 详情面板出入边（ego view）：只渲染相邻节点，不渲染全图。 */
function DetailEdges({ adj, byId, node, onJump }) {
  const { out, inc } = nodeEdges(adj, node.id)
  if (!out.length && !inc.length) {
    return <p className="detail-muted obs-edges__empty">无关联边</p>
  }
  const renderItem = (entity, rel, key) => {
    if (!entity) return null
    const tm = typeMeta(entity.type)
    return (
      <li key={key}>
        <span className="obs-edge__rel">{rel}</span>
        <button className="obs-link" onClick={() => onJump(entity.id)}>
          {entity.title || entity.name}
        </button>
        <span className="obs-card__type" style={{ background: tm.tag, color: tm.fg }}>{tm.label}</span>
      </li>
    )
  }
  return (
    <div className="obs-edges">
      <h4 className="obs-sec">出边 {out.length}</h4>
      <ul>
        {out.map((e, i) => renderItem(byId.get(e.to), e.rel, `o${i}`))}
      </ul>
      <h4 className="obs-sec">入边 {inc.length}</h4>
      <ul>
        {inc.map((e, i) => renderItem(byId.get(e.from), e.rel, `i${i}`))}
      </ul>
    </div>
  )
}
