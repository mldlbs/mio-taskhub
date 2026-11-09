import { useRef } from 'react'
import { prio, fmtDur, fmtAgo, fmtDate, LANES, agMono, agColor, compositeLabel } from '../constants'

export default function TaskCard({ task, index, onCancel, onDragStart, onOpen, onMove, canDrag = true }) {
  const moved = useRef(false)
  const meta = { 'queued': 'dim', 'claimed': 'dim', 'running': 'live', 'retrying': 'warn', 'completed': 'dim', 'failed': 'danger' }[task.state] || 'dim'
  const p = prio(task.priority)
  const attempts = Math.max(1, task.max_retries ?? 3)
  const done = Math.min(attempts, task.attempt ?? 0)
  const ac = task.target_agent_type && agColor(task.target_agent_type)

  const klass = [
    'task',
    'mag',
    canDrag ? '' : 'is-static',
    meta === 'live' ? 'is-running' : '',
    meta === 'warn' ? 'is-retrying' : '',
    meta === 'danger' ? 'is-failed' : '',
  ].filter(Boolean).join(' ')

  const open = () => onOpen && onOpen(task)

  const onKey = (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); return }
    if (!canDrag || !e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return
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
      draggable={canDrag}
      tabIndex={0}
      role="button"
      aria-label={canDrag
        ? `任务 ${task.title}，状态 ${task.state}。回车查看详情，Alt+方向键移动`
        : `任务 ${task.title}，状态 ${task.state}。回车查看详情`}
      onDragStart={e => { if (!canDrag) return; moved.current = true; e.dataTransfer.effectAllowed = 'move'; onDragStart && onDragStart(task.id) }}
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
        {/* 主行：只保留 4 个有决策价值的 chip（ID / 复合状态 / 优先级 / agent） */}
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
      </div>

      {/* 弱行：项目/标签/依赖/到期为次要信息，去边框、纯文字，不再与状态 chip 抢视觉权重 */}
      {(task.labels?.length > 0 || task.due_at || task.project || task.depends_on) && (
        <div className="task__badges">
          {task.depends_on && <span className="tag-weak" title="依赖任务">↳ {task.depends_on}</span>}
          {task.project && <span className="tag-weak">{task.project}</span>}
          {(() => {
            const ls = (task.labels || []).filter(l => !l.startsWith('cron:'))
            const shown = ls.slice(0, 2)
            const rest = ls.length - shown.length
            return (<>
              {shown.map(l => <span key={l} className="tag-weak">#{l}</span>)}
              {rest > 0 && <span className="tag-weak" title={ls.slice(2).join('、')}>+{rest}</span>}
            </>)
          })()}
          {(task.labels || []).some(l => l.startsWith('cron:')) && (
            <span className="chip chip--cron" title="由定时任务创建">⏱ 定时</span>
          )}
          {task.due_at && <span className="chip chip--due" title="计划完成时间">⏰ {fmtDate(task.due_at)}</span>}
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
