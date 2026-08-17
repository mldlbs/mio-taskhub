import { useEffect, useState } from 'react'
import { LANES, fmtTime } from '../constants'

function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return now
}

export default function MissionBar({ tasks, ws, lastSync, refreshing, onRefresh, onOpenModal, onFocusLane,
                                     filter, onFilterChange, projectOptions, workspaceOptions }) {
  const now = useClock()
  const counts = Object.fromEntries(LANES.map(l => [l.id, 0]))
  tasks.forEach(t => { if (counts[t.state] != null) counts[t.state] += 1 })
  const total = tasks.length
  const time = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`

  return (
    <header className="mission">
      <div className="mission__brand">
        <img className="mission__mark" src="/icon.png" alt="" width="30" height="30" />
        <div>
          <h1 className="mission__name">MIO<em>·</em>HUB</h1>
          <span className="mission__sub">cross-agent task bus</span>
        </div>
      </div>

      <div className="mission__meters" aria-label="任务统计，点击可跳转到对应列">
        {LANES.map(l => (
          <button
            key={l.id}
            className={`meter${l.tone === 'live' ? ' is-live' : ''}`}
            title={`${l.label} · 点击跳转`}
            onClick={() => onFocusLane && onFocusLane(l.id)}
          >
            <span className="meter__label">{l.en}</span>
            <span key={counts[l.id]} className="meter__value">{counts[l.id]}</span>
          </button>
        ))}
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
