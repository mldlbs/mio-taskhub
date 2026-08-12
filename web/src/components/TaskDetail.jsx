import { useEffect, useRef } from 'react'
import { LANES, STATE_META, prio, fmtDur, fmtDate } from '../constants'

const tone = (s) => STATE_META[s]?.tone || 'dim'
const ACTIVE = ['queued', 'claimed', 'running', 'retrying']

function scheduleOf(t) {
  if (t.schedule_type === 'cron') return t.cron_expr || 'cron'
  if (t.run_at) return fmtDate(t.run_at)
  return '立即执行'
}

function payloadText(p) {
  if (p == null) return ''
  return typeof p === 'string' ? p : JSON.stringify(p)
}

export default function TaskDetail({ task, tasks, onClose, onCancel, onMove, onToggleSubtask }) {
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
  const due = task.due_at ? new Date(task.due_at) : null
  const overdue = !!due && !Number.isNaN(+due) && due < new Date()
  const hasCtx = task.project || task.workspace || (task.files && task.files.length) || (task.deliverables && task.deliverables.length)

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
          {due && (
            <div className={`kv${overdue ? ' is-due' : ''}`}>
              <span>截止时间</span>
              <b className="mono">{fmtDate(task.due_at)}{overdue ? ' ⚠' : ''}</b>
            </div>
          )}
        </div>

        {task.labels && task.labels.length > 0 && (
          <section className="drawer__sec">
            <h3>标签</h3>
            <div className="drawer__labels">
              {task.labels.map(l => (
                <span key={l} className="chip chip--label">{l}</span>
              ))}
            </div>
          </section>
        )}

        {hasCtx && (
          <section className="drawer__sec">
            <h3>上下文</h3>
            {task.project && <div className="kv"><span>项目</span><b>{task.project}</b></div>}
            {task.workspace && <div className="kv"><span>工作区</span><b className="mono">{task.workspace}</b></div>}
            {task.files && task.files.length > 0 && (
              <>
                <div className="ctx-label">关联文件</div>
                <div className="ctx-list">
                  {task.files.map((f, idx) => <div key={idx} className="ctx-file mono">▸ {f}</div>)}
                </div>
              </>
            )}
            {task.deliverables && task.deliverables.length > 0 && (
              <>
                <div className="ctx-label">产出物</div>
                <div className="ctx-list">
                  {task.deliverables.map((f, idx) => <div key={idx} className="ctx-file mono">◆ {f}</div>)}
                </div>
              </>
            )}
          </section>
        )}

        {task.description && (
          <section className="drawer__sec">
            <h3>描述</h3>
            <p className="drawer__desc">{task.description}</p>
          </section>
        )}

        {task.acceptance_criteria && (
          <section className="drawer__sec">
            <h3>验收标准</h3>
            <p className="drawer__desc">{task.acceptance_criteria}</p>
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

        {task.subtasks && task.subtasks.length > 0 && (
          <section className="drawer__sec">
            <h3>子任务</h3>
            <div className="drawer__subtasks">
              {task.subtasks.map(st => {
                const done = st.status === 'done'
                return (
                  <label key={st.id} className={`subtask-row${done ? ' is-done' : ''}`}>
                    <input
                      type="checkbox"
                      checked={done}
                      onChange={() => onToggleSubtask(st.id, !done)}
                      aria-label={`子任务 ${st.title}`}
                    />
                    <span className="subtask-title">{st.title}</span>
                    <span className="subtask-status">{st.status}</span>
                  </label>
                )
              })}
            </div>
          </section>
        )}

        {task.discussions && task.discussions.length > 0 && (
          <section className="drawer__sec">
            <h3>讨论</h3>
            {task.discussions.map(d => (
              <div key={d.id} className="disc-block">
                <div className="disc-block__head">
                  <b className="disc-block__topic">{d.topic}</b>
                  <span className={`chip disc-status${d.status === 'closed' ? ' is-closed' : ''}`}>
                    {d.status === 'closed' ? '已结束' : '进行中'}
                  </span>
                </div>
                {d.agent && <div className="disc-block__agent mono">{d.agent}</div>}
                {d.summary && <p className="disc-block__summary">{d.summary}</p>}
                {d.conclusions && (
                  <p className="disc-block__concl"><em>结论:</em> {d.conclusions}</p>
                )}
              </div>
            ))}
          </section>
        )}

        {task.history && task.history.length > 0 && (
          <section className="drawer__sec">
            <h3>执行历史</h3>
            <div className="drawer__timeline">
              {task.history.map(h => (
                <div key={h.id} className="hist-row">
                  <span className="hist-row__dot" aria-hidden="true" />
                  <div className="hist-row__body">
                    <div className="hist-row__head">
                      <b>{h.type}</b>
                      <span className="hist-row__at mono">{fmtDate(h.at)}</span>
                    </div>
                    {h.payload != null && <pre className="hist-row__payload mono">{payloadText(h.payload)}</pre>}
                  </div>
                </div>
              ))}
            </div>
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
