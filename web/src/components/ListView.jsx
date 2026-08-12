import { useMemo, useState } from 'react'
import { prio, STATE_META, LANES, fmtDur, fmtDate } from '../constants'

const tone = (s) => STATE_META[s]?.tone || 'dim'

const SORTS = {
  priority: (a, b) => (b.priority ?? 0) - (a.priority ?? 0) || String(a.created_at).localeCompare(String(b.created_at)),
  created:  (a, b) => String(b.created_at).localeCompare(String(a.created_at)),
  duration: (a, b) => (b.est_duration_min ?? 0) - (a.est_duration_min ?? 0),
  title:    (a, b) => String(a.title).localeCompare(String(b.title)),
}

export default function ListView({ tasks, onCancel, onOpen }) {
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('priority')
  const [stateF, setStateF] = useState('all')

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return tasks
      .filter(t => stateF === 'all' || t.state === stateF)
      .filter(t => !needle ||
        String(t.title).toLowerCase().includes(needle) ||
        String(t.id || '').toLowerCase().includes(needle) ||
        String(t.target_agent_type || '').toLowerCase().includes(needle) ||
        String(t.state || '').includes(needle))
      .sort(SORTS[sort])
  }, [tasks, q, sort, stateF])

  const schedulable = (s) => s === 'queued' || s === 'claimed' || s === 'retrying'

  return (
    <div>
      <div className="toolbar">
        <div className="toolbar__search">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.3-4.3" />
          </svg>
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="搜索标题 / ID / Agent / 状态…"
            aria-label="搜索任务"
          />
        </div>

        <select className="toolbar__select" value={sort} onChange={e => setSort(e.target.value)} aria-label="排序方式">
          <option value="priority">按优先级</option>
          <option value="created">按创建时间</option>
          <option value="duration">按时长</option>
          <option value="title">按标题</option>
        </select>

        <div className="state-filters" role="group" aria-label="按状态筛选">
          <button className={stateF === 'all' ? 'is-on' : ''} onClick={() => setStateF('all')}>ALL</button>
          {LANES.map(l => (
            <button key={l.id} className={stateF === l.id ? 'is-on' : ''} onClick={() => setStateF(stateF === l.id ? 'all' : l.id)}>
              {l.en}
            </button>
          ))}
        </div>

        <span className="toolbar__count">{rows.length} / {tasks.length}</span>
      </div>

      <table className="ledger">
        <thead>
          <tr>
            <th>ID</th>
            <th>任务</th>
            <th>状态</th>
            <th>优先级</th>
            <th>Agent</th>
            <th>时长</th>
            <th>排期</th>
            <th>创建</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t, i) => {
            const p = prio(t.priority)
            const st = tone(t.state)
            return (
              <tr key={t.id} style={{ '--i': i }} onClick={() => onOpen && onOpen(t)}>
                <td className="cell-mono cell-id">{t.id}</td>
                <td className="cell-title" style={{ maxWidth: 340 }}>{t.title}</td>
                <td>
                  <span className={`state-chip${st === 'live' ? ' is-live' : ''}${st === 'warn' ? ' is-warn' : ''}${st === 'danger' ? ' is-danger' : ''}`}>
                    <i />{t.state}
                  </span>
                </td>
                <td>
                  <span className={`chip${p.p >= 3 ? ' chip--p3' : ''}${p.p === 2 ? ' chip--p2' : ''}`}>{p.label}</span>
                </td>
                <td className="cell-mono">{t.target_agent_type || '任意'}</td>
                <td className="cell-mono">{fmtDur(t.est_duration_min)}</td>
                <td className="cell-mono">{t.schedule_type === 'cron' ? t.cron_expr || 'cron' : t.run_at ? fmtDate(t.run_at) : '—'}</td>
                <td className="cell-mono">{fmtDate(t.created_at)}</td>
                <td style={{ textAlign: 'right' }} onClick={e => e.stopPropagation()}>
                  {schedulable(t.state) && (
                    <button className="btn btn--ghost" style={{ padding: '4px 10px', fontSize: 11 }}
                      onClick={() => onCancel && onCancel(t.id)}>
                      取消
                    </button>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      {rows.length === 0 && (
        <div className="plan__empty" style={{ marginTop: 16 }}>
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.3-4.3" />
          </svg>
          没有匹配的任务
        </div>
      )}
    </div>
  )
}
