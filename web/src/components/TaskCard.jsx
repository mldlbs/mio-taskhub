import { useRef } from 'react'
import { prio, fmtDur, fmtAgo, fmtDate, LANES, agMono, agColor, compositeLabel } from '../constants'

export default function TaskCard({ task, index, onCancel, onDragStart, onOpen, onMove }) {
  const moved = useRef(false)
  const meta = { 'queued': 'dim', 'claimed': 'dim', 'running': 'live', 'retrying': 'warn', 'completed': 'dim', 'failed': 'danger' }[task.state] || 'dim'
  const p = prio(task.priority)
  const attempts = Math.max(1, task.max_retries ?? 3)
  const done = Math.min(attempts, task.attempt ?? 0)
  const ac = task.target_agent_type && agColor(task.target_agent_type)

  const klass = [
    'task',
    'mag',
    meta === 'live' ? 'is-running' : '',
    meta === 'warn' ? 'is-retrying' : '',
    meta === 'danger' ? 'is-failed' : '',
  ].filter(Boolean).join(' ')

  const open = () => onOpen && onOpen(task)

  const onKey = (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); return }
    if (!e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return
    const i = LANES.findIndex(l => l.id === task.state)
    const target = e.key === 'ArrowLeft' ? LANES[i - 1] : LANES[i + 1]
    if (target) { e.preventDefault(); onMove && onMove(task.id, target.id) }
  }

  const onMouseMove = (e) => {
    const el = e.currentTarget
    const r = el.getBoundingClientRect()
    el.style.setProperty('--mx', ((e.clientX - r.left) / r.width - 0.5) * 2)
    el.style.setProperty('--my', ((e.clientY - r.top) / r.height - 0.5) * 2)
  }

  const onMouseLeave = (e) => {
    e.currentTarget.style.removeProperty('--mx')
    e.currentTarget.style.removeProperty('--my')
  }

  return (
    <article
      className={klass}
      style={{ '--i': index }}
      draggable
      tabIndex={0}
      role="button"
      aria-label={`任务 ${task.title}，状态 ${task.state}。回车查看详情，Alt+方向键移动`}
      onDragStart={e => { moved.current = true; e.dataTransfer.effectAllowed = 'move'; onDragStart && onDragStart(task.id) }}
      onDragEnd={() => onDragStart && onDragStart(null)}
      onClick={(e) => { if (moved.current) { moved.current = false; return } open() }}
      onKeyDown={onKey}
      onMouseMove={onMouseMove}
      onMouseLeave={onMouseLeave}
    >
      <div className="task__top">
        <h4 className="task__title">{task.title}</h4>
        <span className="task__chev" aria-hidden="true">›</span>
        {(task.state === 'queued' || task.state === 'claimed' || task.state === 'retrying') && (
          <button
            className="task__del"
            title="取消任务"
            aria-label={`取消任务 ${task.title}`}
            onClick={e => { e.stopPropagation(); onCancel && onCancel(task.id) }}
          >×</button>
        )}
      </div>

      {task.description && <p className="task__desc">{task.description}</p>}

      <div className="task__meta">
        <span className="chip chip--id">{task.id}</span>
        <span className={`chip chip--composite chip--${compositeLabel(task.state, task.stage || 'ready').tone}`}>{compositeLabel(task.state, task.stage || 'ready').label}</span>
        <span className={`chip${p.p >= 3 ? ' chip--p3' : ''}${p.p === 2 ? ' chip--p2' : ''}`}>{p.label}</span>
        {task.target_agent_type ? (
          <span className="task__agent" title={task.target_agent_type}>
            <span className="agent-mono" style={{ background: ac.bg, borderColor: ac.fg, color: ac.fg }}>{agMono(task.target_agent_type)}</span>
            {task.target_agent_type}
          </span>
        ) : (
          <span className="chip chip--agent">任意 agent</span>
        )}
        {task.depends_on && <span className="chip chip--id">↳ {task.depends_on}</span>}
      </div>

      {(task.labels?.length > 0 || task.due_at || task.project) && (
        <div className="task__badges">
          {task.project && <span className="chip chip--label">{task.project}</span>}
          {(task.labels || []).slice(0, 3).map(l => (
            <span key={l} className="chip chip--label">{l}</span>
          ))}
          {task.due_at && <span className="chip chip--due">⏰ {fmtDate(task.due_at)}</span>}
        </div>
      )}

      <div className="task__foot">
        <span>{fmtDur(task.est_duration_min)}<span className="task__ago" title={fmtDate(task.created_at)}> · {fmtAgo(task.created_at)}</span></span>
        <span className="task__attempt" title={`尝试 ${done}/${attempts}`}>
          {Array.from({ length: attempts }).map((_, i) => (
            <span key={i} className={`attempt-dot${i < done ? ' on' : ''}`} />
          ))}
        </span>
      </div>

      {meta === 'live' && <span className="task__live" aria-hidden="true" />}
    </article>
  )
}
