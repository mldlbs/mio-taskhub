import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import { fmtAgo } from '../constants'
import { confirm } from '../confirm'

const STATUS_META = {
  inbox:        { label: '收集箱', tone: 'dim' },
  new:          { label: '记录中', tone: 'dim' },
  fermenting:   { label: '发酵中', tone: 'live' },
  formed:       { label: '已成形', tone: 'ok' },
  broken_down:  { label: '已拆解', tone: 'ok' },
  archived:     { label: '已归档', tone: 'dim' },
  cancelled:    { label: '已取消', tone: 'danger' },
}

export default function InboxView({ onClose }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(50)
  const [total, setTotal] = useState(0)
  const [actioning, setActioning] = useState({})
  const [selected, setSelected] = useState(new Set())

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.listInbox({ offset: page * pageSize, limit: pageSize })
      setItems(res.ideas || [])
      setTotal(res.total || 0)
      setErr(null)
    } catch (e) {
      setErr(e.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize])

  useEffect(() => { load() }, [load])

  const doAction = async (action) => {
    const ids = selected.size ? Array.from(selected) : (items.map(i => i.id))
    if (!ids.length) return
    const word = action === 'promote' ? '晋升到 NEW' : action === 'archive' ? '归档' : '暂留 INBOX'
    if (!confirm(`对 ${ids.length} 条想法执行「${word}」？`, { title: '批量初筛' })) return

    setActioning(prev => ({ ...prev, [action]: true }))
    try {
      if (selected.size) {
        await api.batchTriage(ids, action)
      } else {
        for (const id of ids) {
          await api.triageIdea(id, action)
        }
      }
      setSelected(new Set())
      await load()
    } catch (e) {
      setErr(e.message)
    } finally {
      setActioning(prev => ({ ...prev, [action]: false }))
    }
  }

  const toggleSel = (id) => setSelected(s => {
    const ns = new Set(s)
    ns.has(id) ? ns.delete(id) : ns.add(id)
    return ns
  })
  const toggleAll = () => setSelected(s => s.size === items.length ? new Set() : new Set(items.map(i => i.id)))

  if (loading && !items.length) {
    return (
      <div className="inbox-view skeleton">
        <div className="inbox-view__head-skel"></div>
        <div className="inbox-view__list-skel">
          {[...Array(5)].map((_, i) => <div key={i} className="inbox-row-skel" />)}
        </div>
      </div>
    )
  }

  return (
    <div className="inbox-view">
      {err && <div className="errorbar" role="alert"><span>▲</span><span>{err}</span><button onClick={() => setErr(null)} aria-label="关闭">×</button></div>}

      <div className="inbox-view__head">
        <h2 className="inbox-view__title">收集箱 INBOX</h2>
        <span className="inbox-view__count">{total} 条</span>
        {onClose && <button className="btn btn--ghost btn--sm" onClick={onClose}>返回想法</button>}
      </div>

      {items.length === 0 && !loading && (
        <div className="inbox-view__empty">
          收集箱为空 —— 新生成的想法会自动落入这里，经初筛后晋升到 NEW。
        </div>
      )}

      <div className="inbox-view__toolbar">
        <div className="inbox-view__sel">
          {selected.size > 0 && (
            <span className="inbox-view__sel-count">{selected.size} / {items.length} 已选</span>
          )}
          <button className="btn btn--ghost btn--sm" onClick={toggleAll}>
            {selected.size === items.length && items.length ? '取消全选' : '全选'}
          </button>
        </div>
        <div className="inbox-view__actions">
          <button className="btn btn--primary" onClick={() => doAction('promote')} disabled={actioning.promote}>
            {actioning.promote ? '晋升中…' : '⬆ 晋升到 NEW'}
          </button>
          <button className="btn btn--ghost" onClick={() => doAction('archive')} disabled={actioning.archive}>
            {actioning.archive ? '归档中…' : '📦 归档'}
          </button>
          <button className="btn btn--ghost" onClick={() => doAction('defer')} disabled={actioning.defer}>
            {actioning.defer ? '暂留中…' : '⏸ 暂留 INBOX'}
          </button>
        </div>
      </div>

      <div className="inbox-view__list" role="list">
        {items.map(item => {
          const meta = STATUS_META[item.status] || STATUS_META.inbox
          return (
            <article key={item.id} className={`inbox-row${selected.has(item.id) ? ' is-selected' : ''}`} role="listitem">
              <label className="inbox-row__chk">
                <input type="checkbox" checked={selected.has(item.id)} onChange={() => toggleSel(item.id)} />
                <span className="inbox-row__chk-box" />
              </label>
              <div className="inbox-row__main" onClick={() => toggleSel(item.id)}>
                <div className="inbox-row__title-row">
                  <h4 className="inbox-row__title">{item.title}</h4>
                  <span className={`badge badge--${meta.tone}`}>{meta.label}</span>
                </div>
                {item.description && <p className="inbox-row__desc">{item.description.slice(0, 200)}{item.description.length > 200 ? '…' : ''}</p>}
                <div className="inbox-row__meta mono">
                  <span>ID: {item.id}</span>
                  <span>{fmtAgo(item.created_at)}</span>
                  {item.novelty != null && <span>N{item.novelty} F{item.feasibility} I{item.impact}</span>}
                </div>
              </div>
              <div className="inbox-row__actions">
                <button className="btn btn--ghost btn--sm" onClick={e => { e.stopPropagation(); api.triageIdea(item.id, 'promote').then(load).catch(setErr) }} disabled={actioning.promote} title="晋升到 NEW">⬆ 晋升</button>
                <button className="btn btn--ghost btn--sm" onClick={e => { e.stopPropagation(); api.triageIdea(item.id, 'archive').then(load).catch(setErr) }} disabled={actioning.archive} title="归档">📦 归档</button>
                <button className="btn btn--ghost btn--sm" onClick={e => { e.stopPropagation(); api.triageIdea(item.id, 'defer').then(load).catch(setErr) }} disabled={actioning.defer} title="暂留 INBOX">⏸ 暂留</button>
              </div>
            </article>
          )
        })}
      </div>

      {total > pageSize && (
        <div className="inbox-view__pager">
          <button className="btn btn--ghost btn--sm" onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}>上一页</button>
          <span className="inbox-view__page-info">第 {page + 1} 页 / 共 {Math.ceil(total / pageSize)} 页</span>
          <button className="btn btn--ghost btn--sm" onClick={() => setPage(p => Math.min(Math.ceil(total / pageSize) - 1, p + 1))} disabled={page >= Math.ceil(total / pageSize) - 1}>下一页</button>
        </div>
      )}
    </div>
  )
}