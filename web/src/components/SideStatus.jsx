import { useEffect, useState } from 'react'
import { api } from '../api'

export default function SideStatus() {
  const [summary, setSummary] = useState(null)
  const [collapsed, setCollapsed] = useState(false)
  useEffect(() => {
    let cancelled = false
    const fetch = () => api.boardSummary().then(d => { if (!cancelled) setSummary(d) }).catch(() => {})
    fetch()
    const t = setInterval(fetch, 8000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  const q = summary?.ready_queue || []
  const running = summary?.running || []
  const alerts = summary?.alerts || []

  return (
    <aside className={`side-status${collapsed ? ' is-collapsed' : ''}`} aria-label="调度队列">
      <div className="side-status__head">
        <span className="side-status__title">调度队列</span>
        <button className="side-status__toggle" onClick={() => setCollapsed(v => !v)} aria-label={collapsed ? '展开' : '收起'}>
          {collapsed ? '›' : '‹'}
        </button>
      </div>
      {!collapsed && (
        <div className="side-status__body">
          {q.length > 0 ? (
            <div className="side-status__section">
              <div className="side-status__label">按优先级 · {q.length}</div>
              <ol className="side-status__list">
                {q.slice(0, 8).map(item => (
                  <li key={item.id} className="side-status__item" title={`${item.title} [P${item.priority}]`}>
                    <span className="side-status__p">P{item.priority}</span>
                    <span className="side-status__name">{item.title}</span>
                  </li>
                ))}
              </ol>
              {q.length > 8 && <div className="side-status__more">…还有 {q.length - 8} 个</div>}
            </div>
          ) : (
            <div className="side-status__empty">暂无排队</div>
          )}

          {running.length > 0 && (
            <div className="side-status__section">
              <div className="side-status__label">执行中 · {running.length}</div>
              <ul className="side-status__list">
                {running.slice(0, 5).map(r => (
                  <li key={r.id} className="side-status__item is-running">
                    <span className="side-status__dot" />
                    <span className="side-status__name">{r.title}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {alerts.length > 0 && (
            <div className="side-status__section is-alert">
              <div className="side-status__label">告警 · {alerts.length}</div>
              {alerts.slice(0, 3).map((a, i) => (
                <div key={i} className="side-status__alert">⚠ {a.message}</div>
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  )
}
