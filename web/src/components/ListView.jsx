import { useMemo, useState } from 'react'
import { prio, STATE_META, STATE_VAR, LANES, fmtDur, fmtDate } from '../constants'

const tone = (s) => STATE_META[s]?.tone || 'dim'

const SORTS = {
  priority: (a, b) => (b.priority ?? 0) - (a.priority ?? 0) || String(a.created_at).localeCompare(String(b.created_at)),
  created:  (a, b) => String(b.created_at).localeCompare(String(a.created_at)),
  duration: (a, b) => (b.est_duration_min ?? 0) - (a.est_duration_min ?? 0),
  title:    (a, b) => String(a.title).localeCompare(String(b.title)),
}

const SORT_LABEL = {
  priority: '优先级',
  created: '创建时间',
  duration: '时长',
  title: '标题',
}

export default function ListView({ tasks, onCancel, onOpen, onOpenModal, loading }) {
  const [q, setQ] = useState('')
  const [sort, setSort] = useState('created')
  const [stateF, setStateF] = useState('all')
  const [agentF, setAgentF] = useState('all')

  const agentTypes = useMemo(() => {
    const set = new Set(tasks.map(t => t.target_agent_type).filter(Boolean))
    return ['all', ...Array.from(set).sort()]
  }, [tasks])

  const rows = useMemo(() => {
    const needle = q.trim().toLowerCase()
    return tasks
      .filter(t => stateF === 'all' || t.state === stateF)
      .filter(t => agentF === 'all' || (t.target_agent_type || '') === agentF)
      .filter(t => !needle ||
        String(t.title).toLowerCase().includes(needle) ||
        String(t.id || '').toLowerCase().includes(needle) ||
        String(t.target_agent_type || '').toLowerCase().includes(needle) ||
        String(t.state || '').includes(needle))
      .sort(SORTS[sort])
  }, [tasks, q, sort, stateF, agentF])

  const schedulable = (s) => s === 'queued' || s === 'claimed' || s === 'retrying'

  // 空态分流（R2 C-6）：区分「任务库真的是空的」和「筛选后为空」。
  // 之前两种情况都显示「没有匹配的任务 + 清除筛选」，任务库为空时点清除毫无变化。
  const noTasksAtAll = tasks.length === 0
  const showSkeleton = loading && rows.length === 0

  return (
    <div className="list-view">
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
          {q && (
            <button className="toolbar__search-clear" onClick={() => setQ('')} aria-label="清除搜索">
              ×
            </button>
          )}
        </div>

        <div className="toolbar__group">
          <label className="toolbar__label">排序</label>
          <select className="toolbar__select" value={sort} onChange={e => setSort(e.target.value)} aria-label="排序方式">
            {Object.entries(SORT_LABEL).map(([k, v]) => (
              <option key={k} value={k}>{v}</option>
            ))}
          </select>
        </div>

        <div className="toolbar__group">
          <label className="toolbar__label">状态</label>
          <div className="state-filters" role="group" aria-label="按状态筛选">
            <button className={stateF === 'all' ? 'is-on' : ''} onClick={() => setStateF('all')}>全部</button>
            {LANES.map(l => (
              <button key={l.id} className={stateF === l.id ? 'is-on' : ''} onClick={() => setStateF(stateF === l.id ? 'all' : l.id)}>
                {l.label}
              </button>
            ))}
          </div>
        </div>

        <div className="toolbar__group">
          <label className="toolbar__label">Agent</label>
          <select className="toolbar__select" value={agentF} onChange={e => setAgentF(e.target.value)} aria-label="按 Agent 筛选">
            {agentTypes.map(a => (
              <option key={a} value={a}>{a === 'all' ? '全部' : a}</option>
            ))}
          </select>
        </div>

        <span className="toolbar__count">{rows.length} / {tasks.length}</span>
      </div>

      <div className="table-wrap">
        <table className="ledger">
          <colgroup>
            <col className="col-id" />
            <col className="col-title" />
            <col className="col-state" />
            <col className="col-priority" />
            <col className="col-duration" />
            <col className="col-schedule" />
            <col className="col-created" />
            <col className="col-action" />
          </colgroup>
          <thead>
            <tr>
              <th>ID</th>
              <th>任务</th>
              <th>状态</th>
              <th>优先级</th>
              <th>时长</th>
              <th>排期</th>
              <th>创建</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {showSkeleton && [0, 1, 2].map(i => (
              <tr key={`sk-${i}`} className="ledger__row ledger__row--skeleton" aria-hidden="true">
                <td colSpan={8}><div className="view-skeleton__row" /></td>
              </tr>
            ))}
            {rows.map((t, i) => {
              const p = prio(t.priority)
              // 状态点语义色（快赢 #3）：色值由 --state-color 注入，见 phase1 C3
              const st = tone(t.state)
              const stateStyle = { '--state-color': STATE_VAR[t.state] || 'var(--neutral)' }
              return (
                <tr key={t.id} style={{ '--i': i }} onClick={() => onOpen && onOpen(t)} className="ledger__row"
                    tabIndex={0}
                    aria-label={`任务 ${t.title}，状态 ${t.state}，回车查看详情`}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        if (onOpen) onOpen(t)
                      }
                    }}>
                  <td className="cell-mono cell-id" title={t.id}>{t.id}</td>
                  <td className="cell-title" title={t.title}>{t.title}</td>
                  <td>
                    <span className={`state-chip is-${st}`} style={stateStyle}>
                      <i />{t.state}
                    </span>
                  </td>
                  <td>
                    <span className={`chip${p.p >= 3 ? ' chip--p3' : ''}${p.p === 2 ? ' chip--p2' : ''}`}>{p.label}</span>
                  </td>
                  <td className="cell-mono">{fmtDur(t.est_duration_min)}</td>
                  <td className="cell-mono cell-schedule">{t.schedule_type === 'cron' ? t.cron_expr || 'cron' : t.run_at ? fmtDate(t.run_at) : '—'}</td>
                  <td className="cell-mono">{fmtDate(t.created_at)}</td>
                  <td className="cell-action" onClick={e => e.stopPropagation()}>
                    {schedulable(t.state) && (
                      <button className="btn btn--ghost btn--sm" onClick={() => onCancel && onCancel(t.id)}>
                        取消
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {rows.length === 0 && !showSkeleton && (
        <div className="list-view__empty">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.3-4.3" />
          </svg>
          {noTasksAtAll ? (
            <>
              <p>还没有任务</p>
              {onOpenModal && (
                <button className="btn btn--accent btn--sm" onClick={onOpenModal}>
                  新建任务 <kbd>N</kbd>
                </button>
              )}
            </>
          ) : (
            <>
              <p>没有匹配的任务</p>
              <button className="btn btn--ghost btn--sm" onClick={() => { setQ(''); setStateF('all'); setAgentF('all') }}>
                清除筛选
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
