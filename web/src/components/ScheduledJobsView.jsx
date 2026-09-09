import { useState, useEffect } from 'react'
import { api } from '../api'
import { fmtAgo } from '../constants'

export default function ScheduledJobsView({ onNavigateToTask }) {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [editJob, setEditJob] = useState(null)
  const [execJob, setExecJob] = useState(null)
  const [executions, setExecutions] = useState([])

  const load = () => {
    setLoading(true)
    api.listScheduledJobs().then(d => { setJobs(d); setLoading(false) }).catch(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  useEffect(() => {
    if (execJob) {
      api.listScheduledJobExecutions(execJob.id).then(setExecutions).catch(() => setExecutions([]))
    }
  }, [execJob])

  const handlePause = async (job) => {
    try {
      await api.pauseScheduledJob(job.id)
      load()
    } catch (e) { alert('暂停失败: ' + e.message) }
  }

  const handleResume = async (job) => {
    try {
      await api.resumeScheduledJob(job.id)
      load()
    } catch (e) { alert('恢复失败: ' + e.message) }
  }

  const handleTrigger = async (job) => {
    try {
      await api.triggerScheduledJob(job.id)
      load()
    } catch (e) { alert('触发失败: ' + e.message) }
  }

  const handleDelete = async (job) => {
    if (!confirm(`确认删除定时任务「${job.name}」？`)) return
    try {
      await api.deleteScheduledJob(job.id)
      load()
    } catch (e) { alert('删除失败: ' + e.message) }
  }

  return (
    <div className="sjv">
      <div className="sjv__head">
        <h2>定时任务</h2>
        <button className="btn btn--accent" onClick={() => { setEditJob(null); setShowCreate(true) }}>
          + 新建定时任务
        </button>
      </div>

      {loading ? (
        <div className="sjv__empty">加载中…</div>
      ) : jobs.length === 0 ? (
        <div className="sjv__empty">
          <p>暂无定时任务</p>
          <p className="sjv__empty-hint">创建定时任务后，系统会按 cron 表达式自动触发执行。</p>
        </div>
      ) : (
        <div className="sjv__list">
          {jobs.map(job => (
            <div key={job.id} className={`sj-card ${!job.enabled ? 'sj-card--disabled' : ''}`}>
              <div className="sj-card__head">
                <span className="sj-card__name">{job.name}</span>
                <span className={`sj-card__badge sj-card__badge--${job.last_status || 'pending'}`}>
                  {job.last_status === 'ok' ? '正常' : job.last_status === 'error' ? '异常' : '待执行'}
                </span>
              </div>
              <div className="sj-card__body">
                <div className="sj-card__row">
                  <span className="sj-card__label">Cron</span>
                  <code className="sj-card__code">{job.cron_expr}</code>
                </div>
                <div className="sj-card__row">
                  <span className="sj-card__label">动作</span>
                  <span>{job.action_type === 'webhook' ? 'Webhook' : '创建任务'}</span>
                </div>
                <div className="sj-card__row">
                  <span className="sj-card__label">下次执行</span>
                  <span>{job.next_run_at ? new Date(job.next_run_at).toLocaleString('zh-CN') : '—'}</span>
                </div>
                <div className="sj-card__row">
                  <span className="sj-card__label">执行次数</span>
                  <span>{job.run_count}</span>
                </div>
                {job.last_run_at && (
                  <div className="sj-card__row">
                    <span className="sj-card__label">上次执行</span>
                    <span>{fmtAgo(job.last_run_at)}</span>
                  </div>
                )}
                {job.last_error && (
                  <div className="sj-card__err">{job.last_error}</div>
                )}
              </div>
              <div className="sj-card__foot">
                <button className="btn btn--ghost btn--sm" onClick={() => handleTrigger(job)}
                  disabled={!job.enabled}>立即执行</button>
                {job.enabled ? (
                  <button className="btn btn--ghost btn--sm" onClick={() => handlePause(job)}>暂停</button>
                ) : (
                  <button className="btn btn--ghost btn--sm" onClick={() => handleResume(job)}>恢复</button>
                )}
                <button className="btn btn--ghost btn--sm" onClick={() => { setEditJob(job); setShowCreate(true) }}>编辑</button>
                <button className="btn btn--ghost btn--sm btn--danger" onClick={() => handleDelete(job)}>删除</button>
                <button className="btn btn--ghost btn--sm" onClick={() => setExecJob(job)}>执行历史</button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showCreate && (
        <ScheduledJobModal
          editJob={editJob}
          onClose={() => { setShowCreate(false); setEditJob(null) }}
          onSaved={() => { setShowCreate(false); setEditJob(null); load() }}
        />
      )}

      {execJob && (
        <div className="overlay" onClick={() => setExecJob(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal__head">
              <h3>执行历史 — {execJob.name}</h3>
              <button className="modal__close" onClick={() => setExecJob(null)}>×</button>
            </div>
            <div className="modal__body">
              {executions.length === 0 ? (
                <p className="sjv__empty">暂无执行记录</p>
              ) : (
                <table className="sj-table">
                  <thead>
                    <tr><th>时间</th><th>状态</th><th>结果</th></tr>
                  </thead>
                  <tbody>
                    {executions.map(e => (
                      <tr key={e.id}>
                        <td>{new Date(e.started_at).toLocaleString('zh-CN')}</td>
                        <td><span className={`sj-badge sj-badge--${e.status}`}>{e.status === 'ok' ? '成功' : '失败'}</span></td>
                        <td className="sj-table__result">{e.error || e.result || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}


function ScheduledJobModal({ editJob, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: editJob?.name || '',
    cron_expr: editJob?.cron_expr || '',
    action_type: editJob?.action_type || 'create_task',
    action_config: editJob?.action_config || {},
    enabled: editJob?.enabled ?? true,
  })
  const [cronPreview, setCronPreview] = useState([])
  const [cronError, setCronError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!form.cron_expr.trim()) { setCronPreview([]); setCronError(''); return }
    const timer = setTimeout(() => {
      api.validateCron(form.cron_expr.trim())
        .then(d => { setCronPreview(d.next_runs || []); setCronError('') })
        .catch(e => { setCronPreview([]); setCronError(e.userMessage || e.message) })
    }, 400)
    return () => clearTimeout(timer)
  }, [form.cron_expr])

  const isWebhook = form.action_type === 'webhook'
  const cfg = form.action_config

  const submit = async (e) => {
    e.preventDefault()
    if (!form.name.trim() || !form.cron_expr.trim()) return
    setBusy(true)
    try {
      const body = {
        name: form.name.trim(),
        cron_expr: form.cron_expr.trim(),
        action_type: form.action_type,
        action_config: cfg,
        enabled: form.enabled,
      }
      if (editJob) {
        await api.updateScheduledJob(editJob.id, body)
      } else {
        await api.createScheduledJob(body)
      }
      onSaved()
    } catch (e) {
      alert('保存失败: ' + (e.userMessage || e.message))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" role="dialog" onClick={e => e.stopPropagation()}>
        <div className="modal__head">
          <h3>{editJob ? '编辑定时任务' : '新建定时任务'}</h3>
          <button className="modal__close" onClick={onClose}>×</button>
        </div>
        <form onSubmit={submit}>
          <div className="modal__body">
            <div className="field">
              <label className="field__label">名称 <b>*</b></label>
              <input autoFocus value={form.name} onChange={e => setForm({ ...form, name: e.target.value })}
                placeholder="例如：每日数据同步" />
            </div>
            <div className="field">
              <label className="field__label">Cron 表达式 <b>*</b></label>
              <input value={form.cron_expr} onChange={e => setForm({ ...form, cron_expr: e.target.value })}
                placeholder="分 时 日 月 周，如 0 9 * * 1-5" className={cronError ? 'field--error' : ''} />
              {cronError && <span className="field__err">{cronError}</span>}
              {cronPreview.length > 0 && (
                <div className="cron-preview">
                  <span className="cron-preview__label">下次执行：</span>
                  {cronPreview.map((t, i) => (
                    <span key={i} className="cron-preview__time">
                      {new Date(t).toLocaleString('zh-CN', { month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit' })}
                    </span>
                  ))}
                </div>
              )}
            </div>
            <div className="field">
              <label className="field__label">动作类型</label>
              <div className="sched-row">
                <button type="button" className={`sched-btn ${!isWebhook ? 'is-on' : ''}`}
                  onClick={() => setForm({ ...form, action_type: 'create_task', action_config: {} })}>创建任务</button>
                <button type="button" className={`sched-btn ${isWebhook ? 'is-on' : ''}`}
                  onClick={() => setForm({ ...form, action_type: 'webhook', action_config: { url: '', method: 'POST' } })}>Webhook</button>
              </div>
            </div>
            {isWebhook ? (
              <>
                <div className="field">
                  <label className="field__label">URL</label>
                  <input value={cfg.url || ''} onChange={e => setForm({ ...form, action_config: { ...cfg, url: e.target.value } })}
                    placeholder="https://example.com/hook" />
                </div>
                <div className="field field--row">
                  <div>
                    <label className="field__label">Method</label>
                    <select value={cfg.method || 'POST'} onChange={e => setForm({ ...form, action_config: { ...cfg, method: e.target.value } })}>
                      <option value="POST">POST</option>
                      <option value="PUT">PUT</option>
                      <option value="GET">GET</option>
                    </select>
                  </div>
                  <div style={{flex:1}}>
                    <label className="field__label">Headers (JSON)</label>
                    <input value={JSON.stringify(cfg.headers || {})}
                      onChange={e => { try { setForm({ ...form, action_config: { ...cfg, headers: JSON.parse(e.target.value) } }) } catch {} }}
                      placeholder='{"Authorization":"Bearer xxx"}' />
                  </div>
                </div>
                <div className="field">
                  <label className="field__label">Body (JSON)</label>
                  <textarea value={JSON.stringify(cfg.body || {}, null, 2)}
                    onChange={e => { try { setForm({ ...form, action_config: { ...cfg, body: JSON.parse(e.target.value) } }) } catch {} }}
                    rows={3} placeholder='{"event":"trigger"}' />
                </div>
              </>
            ) : (
              <>
                <div className="field">
                  <label className="field__label">任务标题</label>
                  <input value={cfg.title || ''} onChange={e => setForm({ ...form, action_config: { ...cfg, title: e.target.value } })}
                    placeholder="留空则使用定时任务名称" />
                </div>
                <div className="field">
                  <label className="field__label">任务描述</label>
                  <textarea value={cfg.description || ''} onChange={e => setForm({ ...form, action_config: { ...cfg, description: e.target.value } })}
                    rows={2} placeholder="任务详细说明…" />
                </div>
                <div className="field field--row">
                  <div>
                    <label className="field__label">优先级</label>
                    <select value={cfg.priority || 0} onChange={e => setForm({ ...form, action_config: { ...cfg, priority: +e.target.value } })}>
                      <option value={0}>P0 低</option>
                      <option value={1}>P1 中</option>
                      <option value={2}>P2 高</option>
                      <option value={3}>P3 紧急</option>
                    </select>
                  </div>
                  <div style={{flex:1}}>
                    <label className="field__label">目标 Agent</label>
                    <input value={cfg.target_agent_type || ''} onChange={e => setForm({ ...form, action_config: { ...cfg, target_agent_type: e.target.value } })}
                      placeholder="留空任意" />
                  </div>
                </div>
                <div className="field field--row">
                  <div>
                    <label className="field__label">项目</label>
                    <input value={cfg.project || ''} onChange={e => setForm({ ...form, action_config: { ...cfg, project: e.target.value } })} />
                  </div>
                  <div>
                    <label className="field__label">工作区</label>
                    <input value={cfg.workspace || ''} onChange={e => setForm({ ...form, action_config: { ...cfg, workspace: e.target.value } })} />
                  </div>
                </div>
                <div className="field">
                  <label className="field__label">标签</label>
                  <input value={(cfg.labels || []).join(', ')} onChange={e => setForm({ ...form, action_config: { ...cfg, labels: e.target.value.split(',').map(s => s.trim()).filter(Boolean) } })}
                    placeholder="tag1, tag2" />
                </div>
              </>
            )}
          </div>
          <div className="modal__foot">
            <button type="button" className="btn btn--ghost" onClick={onClose}>取消</button>
            <button type="submit" className="btn btn--accent" disabled={busy || !form.name.trim() || !form.cron_expr.trim()}>
              {busy ? '保存中…' : (editJob ? '保存修改' : '创建定时任务')}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
