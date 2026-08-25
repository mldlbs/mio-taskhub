import { useEffect, useState } from 'react'
import { api } from '../api'
import { fmtTime } from '../constants'

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return now
}

export default function MissionBar({ tasks, ws, lastSync, refreshing, onRefresh, onOpenModal,
                                     filter, onFilterChange, projectOptions, workspaceOptions }) {
  const now = useClock()
  const total = tasks.length
  const time = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`
  const [summary, setSummary] = useState(null)
  const [open, setOpen] = useState(false)
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
    <header className="mission">
      <div className="mission__brand">
        <img className="mission__mark" src="/icon.png" alt="" width="30" height="30" />
        <div>
          <h1 className="mission__name">MIO<em>·</em>HUB</h1>
          <span className="mission__sub">cross-agent task bus</span>
        </div>
      </div>

      <div className="mission__filters" aria-label="按项目/工作区筛选">
        <select
          className="fsel"
          value={filter?.project || ''}
          onChange={e => onFilterChange({ ...filter, project: e.target.value })}
          aria-label="按项目筛选"
        >
          <option value="">项目：全部</option>
          {projectOptions.map(p => <option key={p} value={p}>{p}</option>)}
        </select>
        <select
          className="fsel"
          value={filter?.workspace || ''}
          onChange={e => onFilterChange({ ...filter, workspace: e.target.value })}
          aria-label="按工作区筛选"
        >
          <option value="">工作区：全部</option>
          {workspaceOptions.map(w => <option key={w} value={w}>{w}</option>)}
        </select>
      </div>

      <div className="mission__right">
        <div className="clock">
          <span className="ws-tag">
            <span className={`ws-dot${ws ? ' is-live' : ' is-dead'}`} />
            <span className={`ws-txt${ws ? ' is-live' : ' is-dead'}`}>{ws ? 'LIVE' : 'OFFLINE'}</span>
          </span>
          <span>{time}</span>
          <span className="mono" style={{ color: 'var(--ink-faint)' }}>×{total}</span>
        </div>

        <div className="queue-capsule">
          <button className={`queue-capsule__pill${alerts.length ? ' has-alert' : ''}`} onClick={() => setOpen(v => !v)} aria-label="调度队列">
            <span>队列 {q.length}</span>
            <span className="queue-capsule__sep">·</span>
            <span>运行 {running.length}</span>
            {alerts.length > 0 && <span className="queue-capsule__alert">⚠{alerts.length}</span>}
            <span className="queue-capsule__chev">{open ? '▴' : '▾'}</span>
          </button>
          {open && (
            <div className="queue-capsule__pop" role="dialog" aria-label="调度详情">
              <div className="queue-capsule__sec">
                <div className="queue-capsule__title">调度队列 · {q.length}（按优先级）</div>
                {q.length ? (
                  <ol className="queue-capsule__list">
                    {q.slice(0, 8).map(it => (
                      <li key={it.id} className="queue-capsule__item"><span className="queue-capsule__p">P{it.priority}</span>{it.title}</li>
                    ))}
                  </ol>
                ) : <div className="queue-capsule__empty">暂无排队</div>}
                {q.length > 8 && <div className="queue-capsule__more">…还有 {q.length - 8} 个</div>}
              </div>
              {running.length > 0 && (
                <div className="queue-capsule__sec">
                  <div className="queue-capsule__title">执行中 · {running.length}</div>
                  <ul className="queue-capsule__list">
                    {running.slice(0, 5).map(r => <li key={r.id} className="queue-capsule__item is-running"><span className="queue-capsule__dot" />{r.title}</li>)}
                  </ul>
                </div>
              )}
              {alerts.length > 0 && (
                <div className="queue-capsule__sec is-alert">
                  <div className="queue-capsule__title">告警 · {alerts.length}</div>
                  {alerts.slice(0, 3).map((a, i) => <div key={i} className="queue-capsule__alert-row">⚠ {a.message}</div>)}
                </div>
              )}
            </div>
          )}
        </div>

        <div className="sync">
          <span className="sync__txt">{lastSync ? `SYNC ${fmtTime(lastSync.toISOString())}` : 'SYNC …'}</span>
          <button
            className="sync__btn"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="手动刷新"
            title="手动刷新"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className={refreshing ? 'spin' : ''}>
              <path d="M21 12a9 9 0 11-2.64-6.36M21 3v6h-6" />
            </svg>
          </button>
        </div>

        <button
          className="btn btn--accent mag"
          onClick={onOpenModal}
          onMouseMove={e => {
            const r = e.currentTarget.getBoundingClientRect()
            e.currentTarget.style.setProperty('--mx', ((e.clientX - r.left) / r.width - 0.5) * 2)
            e.currentTarget.style.setProperty('--my', ((e.clientY - r.top) / r.height - 0.5) * 2)
          }}
          onMouseLeave={e => {
            e.currentTarget.style.removeProperty('--mx')
            e.currentTarget.style.removeProperty('--my')
          }}
        >＋ 新建任务</button>
      </div>

    </header>
  )
}
