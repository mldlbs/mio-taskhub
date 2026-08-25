import { useEffect, useRef, useState } from 'react'
import { LANES, STATE_META, prio, fmtDur, fmtDate } from '../constants'
import { api } from '../api'
import DependencyGraph from './DependencyGraph'

const tone = (s) => STATE_META[s]?.tone || 'dim'
const ACTIVE = ['queued', 'claimed', 'running', 'retrying']

const DISC_GROUPS = [
  { id: 'brainstorming', label: '需求理解' },
  { id: 'design',        label: '设计评审' },
  { id: 'planning',      label: '计划评审' },
  { id: 'review',        label: '审查验收' },
  { id: 'other',         label: '其他' },
]
const DISC_STAGE_LABEL = {
  brainstorming: '需求理解', design: '设计', planning: '计划',
  ready: '待执行', implementing: '执行中', review: '审查', done: '完成',
}
const discGroupId = (s) => DISC_GROUPS.some(g => g.id === s) ? s : 'other'
const discStageLabel = (s) => DISC_STAGE_LABEL[s] || s || '—'

function scheduleOf(t) {
  if (t.schedule_type === 'cron') return t.cron_expr || 'cron'
  if (t.run_at) return fmtDate(t.run_at)
  return '立即执行'
}

function payloadText(p) {
  if (p == null) return ''
  return typeof p === 'string' ? p : JSON.stringify(p)
}

export default function TaskDetail({ task, tasks, onClose, onCancel, onMove, onToggleSubtask, onAdvance, onOpenDocs, onOpenTask, onRetry }) {
  const closeRef = useRef(null)

  useEffect(() => {
    closeRef.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const STAGES = ['brainstorming','design','planning','ready','implementing','review','done']
  const sidx = STAGES.indexOf(task.stage)
  const spct = sidx >= 0 ? Math.round((sidx + 1) / STAGES.length * 100) : 0

  const p = prio(task.priority)
  const i = LANES.findIndex(l => l.id === task.state)
  const prev = i > 0 ? LANES[i - 1] : null
  const next = i < LANES.length - 1 ? LANES[i + 1] : null
  const st = tone(task.state)
  const cancellable = task.state === 'queued' || task.state === 'claimed' || task.state === 'retrying'
  const movable = ACTIVE.includes(task.state)
  const retryable = task.state === 'failed' || task.state === 'retrying'
  const [retryCountdown, setRetryCountdown] = useState(task.retry_countdown ?? null)
  useEffect(() => {
    if (task.state !== 'retrying' || !task.retry_at) {
      setRetryCountdown(null)
      return
    }
    const tick = () => {
      const rt = new Date(task.retry_at)
      const diff = Math.max(0, Math.floor((rt - new Date()) / 1000))
      setRetryCountdown(diff)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [task.retry_at, task.state])
  const due = task.due_at ? new Date(task.due_at) : null
  const overdue = !!due && !Number.isNaN(+due) && due < new Date()
  const hasCtx = task.project || task.workspace || (task.files && task.files.length) || (task.deliverables && task.deliverables.length)
  const discGroups = task.discussions
    ? DISC_GROUPS.map(g => ({ ...g, items: task.discussions.filter(d => discGroupId(d.stage) === g.id) })).filter(g => g.items.length > 0)
    : []

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
          {task.retry_at && task.state === 'retrying' && (
            <div className="kv"><span>下次重试</span><b className="mono">{fmtDate(task.retry_at)} {retryCountdown !== null ? `(${retryCountdown}s 后)` : ''}</b></div>
          )}
          {task.retry_backoff_seconds && task.state === 'retrying' && (
            <div className="kv"><span>退避间隔</span><b className="mono">{task.retry_backoff_seconds}s (2^{task.attempt}×2)</b></div>
          )}
          {due && (
            <div className={`kv${overdue ? ' is-due' : ''}`}>
              <span>截止时间</span>
              <b className="mono">{fmtDate(task.due_at)}{overdue ? ' ⚠' : ''}</b>
            </div>
          )}
          <div className="kv"><span>阶段</span><b>{task.stage || '—'}</b></div>
        </div>

        {task.stage && (
          <div className="stage-bar" title={`${sidx + 1}/${STAGES.length} · ${spct}%`}>
            <div className="stage-bar__fill" style={{ width: spct + '%' }} />
          </div>
        )}

        {(task.spec_path || task.plan_path || task.workspace) && (
          <section className="drawer__sec">
            <button className="btn btn--primary" onClick={onOpenDocs}>查看文档 · Spec / Plan / 自动发现</button>
          </section>
        )}
        {task.review_result && (
          <div className="drawer__sec"><h3>审查结论</h3><p>{task.review_result}</p></div>
        )}

        {task.runs && task.runs.length > 0 && (
          <section className="drawer__sec">
            <h3>完成报告 · {task.runs.length} 次执行</h3>
            {task.runs.map(r => {
              const ok = r.state === 'FINISHED' && (r.exit_code ?? 0) === 0
              return (
                <div key={r.id} className={`run-report${ok ? ' is-ok' : r.exit_code ? ' is-fail' : ''}`}>
                  <div className="run-report__head">
                    <b className="mono">{r.agent_name || 'agent'}</b>
                    <span className={`chip${ok ? ' disc-status' : r.exit_code ? ' chip--due' : ''}`}>{r.state}{r.attempt > 1 ? ` · 第 ${r.attempt} 次` : ''}</span>
                    <span className="hist-row__at mono">
                      {r.started_at ? fmtDate(r.started_at) : ''}{r.finished_at ? ` → ${fmtDate(r.finished_at)}` : ''}
                    </span>
                  </div>
                  {r.result && <pre className="run-report__body mono">{r.result}</pre>}
                </div>
              )
            })}
          </section>
        )}

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

        <section className="drawer__sec">
          <h3>依赖</h3>
          {(task.depends_on || []).length === 0 ? (
            <p className="detail-muted">无前置任务</p>
          ) : (
            <ul className="dep-list">
              {task.depends_on.map(did => {
                const dep = (tasks || []).find(x => x.id === did)
                return (
                  <li key={did}>
                    <span>{dep ? dep.title : did}</span>
                    <span className="tag">{dep ? (dep.state === 'completed' || dep.stage === 'done' ? '已完成' : dep.state) : '缺失'}</span>
                  </li>
                )
              })}
            </ul>
          )}
          <div style={{ marginTop: 12 }}>
            <DependencyGraph task={task} tasks={tasks} onOpen={onOpenTask} />
          </div>
        </section>

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
            {discGroups.map(g => (
              <div key={g.id} className="disc-group">
                <h4 className="disc-group__title">
                  {g.label}
                  <span className="disc-group__count">{g.items.length}</span>
                </h4>
                {g.items.map(d => (
                  <div key={d.id} className="disc-block">
                    <div className="disc-block__head">
                      <b className="disc-block__topic">{d.topic}</b>
                      <span className="disc-block__right">
                        <span className="chip disc-stage">{discStageLabel(d.stage)}</span>
                        <span className={`chip disc-status${d.status === 'closed' ? ' is-closed' : ''}`}>
                          {d.status === 'closed' ? '已结束' : '进行中'}
                        </span>
                      </span>
                    </div>
                    {d.agent && <div className="disc-block__agent mono">{d.agent}</div>}
                    {d.summary && <p className="disc-block__summary">{d.summary}</p>}
                    {d.conclusions && (
                      <p className="disc-block__concl"><em>结论:</em> {d.conclusions}</p>
                    )}
                  </div>
                ))}
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
          {retryable && onRetry && (
            <button
              className="btn btn--primary"
              onClick={async () => { try { await onRetry(task.id); onClose() } catch (e) { /* error handled by App */ } }}
            >重试</button>
          )}
          {task.state === 'retrying' && retryCountdown !== null && (
            <span className="mono" style={{ fontSize: 12, opacity: .7 }}>{retryCountdown}s 后自动重入队列</span>
          )}
          {task.stage && !['done','cancelled'].includes(task.stage) && onAdvance && (
            <button className="btn btn--ghost" onClick={() => onAdvance(task)}>推进阶段 →</button>
          )}
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
