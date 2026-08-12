import { useEffect, useRef } from 'react'
import { LANES, STATE_META, prio, fmtDur, fmtDate } from '../constants'

const tone = (s) => STATE_META[s]?.tone || 'dim'
const ACTIVE = ['queued', 'claimed', 'running', 'retrying']

function scheduleOf(t) {
  if (t.schedule_type === 'cron') return t.cron_expr || 'cron'
  if (t.run_at) return fmtDate(t.run_at)
  return '立即执行'
}

export default function TaskDetail({ task, tasks, onClose, onCancel, onMove }) {
  const closeRef = useRef(null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const p = prio(task.priority)
  const i = LANES.findIndex(l => l.id === task.state)
  const prev = i > 0 ? LANES[i - 1] : null
  const next = i < LANES.length - 1 ? LANES[i + 1] : null
  const dep = task.depends_on && tasks.find(t => t.id === task.depends_on)
  const st = tone(task.state)
  const cancellable = task.state === 'queued' || task.state === 'claimed' || task.state === 'retrying'
  const movable = ACTIVE.includes(task.state)

  return (
    <div className="overlay drawer-overlay" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label="任务详情" onClick={e => e.stopPropagation()}>
        <header className="drawer__head">
          <div>
            <span className={`state-chip${st === 'live' ? ' is-live' : ''}${st === 'warn' ? ' is-warn' : ''}${st === 'danger' ? ' is-danger' : ''}`}>
              <i />{task.state}
            </span>
            <h2>{task.title}</h2>
          </div>
          <button className="modal__close" ref={closeRef} onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="drawer__meta">
          <div className="kv"><span>ID</span><b className="mono">{task.id}</b></div>
          <div className="kv"><span>优先级</span><b>{p.label} · {p.text}</b></div>
          <div className="kv"><span>目标 Agent</span><b>{task.target_agent_type || '任意 agent'}</b></div>
          <div className="kv"><span>预估时长</span><b>{fmtDur(task.est_duration_min)}</b></div>
          <div className="kv"><span>排期</span><b>{scheduleOf(task)}</b></div>
          <div className="kv"><span>创建时间</span><b className="mono">{fmtDate(task.created_at)}</b></div>
          <div className="kv"><span>尝试次数</span><b className="mono">{task.attempt ?? 0} / {task.max_retries ?? 3}</b></div>
          <div className="kv"><span>最大重试</span><b className="mono">{task.max_retries ?? 3}</b></div>
        </div>

        {task.description && (
          <section className="drawer__sec">
            <h3>描述</h3>
            <p className="drawer__desc">{task.description}</p>
          </section>
        )}

        {task.depends_on && (
          <section className="drawer__sec">
            <h3>依赖</h3>
            <p className="mono" style={{ fontSize: 12, color: 'var(--ink-dim)' }}>
              ↳ {dep ? dep.title : '（未知任务）'}
              <span style={{ color: 'var(--ink-faint)' }}> · {task.depends_on}</span>
            </p>
          </section>
        )}

        <footer className="drawer__foot">
          {movable && prev && (
            <button className="btn btn--ghost" onClick={() => onMove(task.id, prev.id)}>← {prev.label}</button>
          )}
          {movable && next && (
            <button className="btn btn--ghost" onClick={() => onMove(task.id, next.id)}>{next.label} →</button>
          )}
          {cancellable && (
            <button
              className="btn btn--ghost btn--danger"
              onClick={() => { onCancel(task.id); onClose() }}
            >取消任务</button>
          )}
        </footer>
      </aside>
    </div>
  )
}
