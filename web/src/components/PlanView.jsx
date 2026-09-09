import { useState, useEffect, useCallback } from 'react'
import { fmtDur, prio } from '../constants'
import { api } from '../api'

const WINDOW_MIN = 540
const WINDOW_START_MIN = 22 * 60
const DEFAULT_START = '22:00'
const DEFAULT_END = '07:00'

const hm2min = (s) => {
  const [h, m] = s.split(':').map(Number)
  return h * 60 + m
}

const min2hm = (min) => {
  const h = Math.floor(min / 60) % 24
  const m = min % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

const HOURS = ['22:00', '23:00', '00:00', '01:00', '02:00', '03:00', '04:00', '05:00', '06:00', '07:00']

function parseNextCron(cronExpr) {
  try {
    const parts = cronExpr.trim().split(/\s+/)
    if (parts.length < 5) return null
    const [min, hour, dom, mon, dow] = parts
    const now = new Date()
    const thisYear = now.getFullYear()
    let iterations = 0
    const MAX_ITER = 100000
    for (let y = thisYear; y <= thisYear + 1; y++) {
      for (let m2 = 0; m2 < 12; m2++) {
        const month = y === thisYear ? m2 : m2
        const daysInMonth = new Date(y, month + 1, 0).getDate()
        for (let d = 1; d <= daysInMonth; d++) {
          for (let h2 = 0; h2 < 24; h2++) {
            for (let mi = 0; mi < 60; mi++) {
              if (++iterations > MAX_ITER) return null
              const date = new Date(y, month, d, h2, mi)
              if (date <= now) continue
              const dw = date.getDay()
              const matches =
                (min === '*' || parseInt(min) === mi) &&
                (hour === '*' || parseInt(hour) === h2) &&
                (dom === '*' || parseInt(dom) === d) &&
                (mon === '*' || parseInt(mon) === month + 1) &&
                (dow === '*' || parseInt(dow) === dw)
              if (matches) {
                return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
              }
            }
          }
        }
      }
    }
  } catch { return null }
  return null
}

function Section({ title, icon, badge, badgeClass, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="np-section">
      <div className="np-section__head" onClick={() => setOpen(o => !o)}>
        <span className="np-section__icon">{icon}</span>
        <span className="np-section__title">{title}</span>
        {badge != null && <span className={`np-section__badge${badgeClass ? ' ' + badgeClass : ''}`}>{badge}</span>}
        <span className={`np-section__toggle${open ? ' is-open' : ''}`}>▶</span>
      </div>
      {open && <div className="np-section__body">{children}</div>}
    </div>
  )
}

function ConfigPanel({ config, onSave, onToggleEnabled, enabled, saving, agents, onAgentsChange }) {
  const [winStart, setWinStart] = useState(config?.window_start || DEFAULT_START)
  const [winEnd, setWinEnd] = useState(config?.window_end || DEFAULT_END)
  const [draftAgents, setDraftAgents] = useState(agents || [])
  const [editingIdx, setEditingIdx] = useState(null)
  const [editForm, setEditForm] = useState({})
  const [showAdd, setShowAdd] = useState(false)
  const [addForm, setAddForm] = useState({ agent: '', agent_type: '', command: '', cwd: '' })

  useEffect(() => {
    setDraftAgents(agents || [])
  }, [agents])

  useEffect(() => {
    setWinStart(config?.window_start || DEFAULT_START)
    setWinEnd(config?.window_end || DEFAULT_END)
  }, [config?.window_start, config?.window_end])

  const save = () => {
    onSave({ window_start: winStart, window_end: winEnd, agents: draftAgents, enabled })
  }

  const startEdit = (i) => {
    setEditingIdx(i)
    setEditForm({ ...draftAgents[i] })
  }

  const cancelEdit = () => {
    setEditingIdx(null)
    setEditForm({})
  }

  const applyEdit = (i) => {
    const updated = [...draftAgents]
    updated[i] = { ...editForm }
    setDraftAgents(updated)
    setEditingIdx(null)
    setEditForm({})
  }

  const deleteAgent = (i) => {
    setDraftAgents(a => a.filter((_, idx) => idx !== i))
  }

  const addAgent = () => {
    if (!addForm.command) return
    setDraftAgents(a => [...a, { ...addForm }])
    setAddForm({ agent: '', agent_type: '', command: '', cwd: '' })
    setShowAdd(false)
  }

  return (
    <div className="np-cfg">
      <div className="np-cfg__row">
        <div className="np-cfg__field">
          <label className="np-cfg__label">窗口开始</label>
          <input className="np-cfg__input" value={winStart} onChange={e => setWinStart(e.target.value)} placeholder="HH:MM" />
        </div>
        <div className="np-cfg__field">
          <label className="np-cfg__label">窗口结束</label>
          <input className="np-cfg__input" value={winEnd} onChange={e => setWinEnd(e.target.value)} placeholder="HH:MM" />
        </div>
        <button
          className={`btn ${enabled ? 'btn--ok' : 'btn--ghost'} np__nr-toggle`}
          onClick={onToggleEnabled}
          title={enabled ? '夜班执行中' : '开启后按窗口自动执行'}
        >
          {enabled ? '● 夜班 ON' : '○ 夜班 OFF'}
        </button>
        <button className="btn btn--accent np-cfg__save" onClick={save} disabled={saving}>
          {saving ? '保存中…' : '✓ 保存配置'}
        </button>
      </div>

      <div className="np-agents">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
            Agents ({draftAgents.length})
          </span>
          <button className="btn btn--ghost btn--xs" onClick={() => setShowAdd(s => !s)}>
            {showAdd ? '取消' : '+ 添加 Agent'}
          </button>
        </div>

        {showAdd && (
          <div className="np-agent-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: '6px' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
              <input placeholder="名称 (agent)" value={addForm.agent} onChange={e => setAddForm(f => ({ ...f, agent: e.target.value }))} />
              <input placeholder="类型 (agent_type)" value={addForm.agent_type} onChange={e => setAddForm(f => ({ ...f, agent_type: e.target.value }))} />
            </div>
            <input placeholder="命令模板，支持 {url} {token} 占位符" value={addForm.command} onChange={e => setAddForm(f => ({ ...f, command: e.target.value }))} />
            <input placeholder="工作目录 cwd (可选)" value={addForm.cwd} onChange={e => setAddForm(f => ({ ...f, cwd: e.target.value }))} />
            <button className="btn btn--accent btn--xs" onClick={addAgent} disabled={!addForm.command}>确认添加</button>
          </div>
        )}

        {draftAgents.length === 0 && !showAdd && (
          <div style={{ padding: '12px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--warn)', border: '1px dashed var(--line)', borderRadius: '8px' }}>
            未配置任何 agent — 点击「添加 Agent」开始配置
          </div>
        )}

        {draftAgents.map((a, i) => (
          <div key={i} className="np-agent-row">
            {editingIdx === i ? (
              <>
                <input style={{ width: '80px' }} value={editForm.agent || ''} onChange={e => setEditForm(f => ({ ...f, agent: e.target.value }))} placeholder="名称" />
                <input style={{ width: '80px' }} value={editForm.agent_type || ''} onChange={e => setEditForm(f => ({ ...f, agent_type: e.target.value }))} placeholder="类型" />
                <input style={{ flex: 1, minWidth: '120px' }} value={editForm.command || ''} onChange={e => setEditForm(f => ({ ...f, command: e.target.value }))} placeholder="command" />
                <input style={{ width: '80px' }} value={editForm.cwd || ''} onChange={e => setEditForm(f => ({ ...f, cwd: e.target.value }))} placeholder="cwd" />
                <div className="np-agent-row__actions">
                  <button className="btn btn--accent btn--xs" onClick={() => applyEdit(i)}>✓</button>
                  <button className="btn btn--ghost btn--xs" onClick={cancelEdit}>✕</button>
                </div>
              </>
            ) : (
              <>
                <span className="np-agent-row__name">{a.agent || a.agent_type || '?'}</span>
                <span className="np-agent-row__type">{a.agent_type || '-'}</span>
                <span className="np-agent-row__cmd" title={a.command}>{a.command}</span>
                {a.cwd && <span className="np-agent-row__cwd" title={a.cwd}>{a.cwd}</span>}
                <div className="np-agent-row__actions">
                  <button className="btn btn--ghost btn--xs" onClick={() => startEdit(i)} title="编辑">✎</button>
                  <button className="btn btn--ghost btn--xs" onClick={() => deleteAgent(i)} title="删除" style={{ color: 'var(--warn)' }}>✕</button>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function StatusPanel({ status, config, onRefresh, refreshing, onStop, onSpawnNow }) {
  const running = status?.running_agents || {}
  const runningCount = Object.keys(running).length
  const inWindow = status?.in_window
  const windowDisplay = status?.window || `${config?.window_start || DEFAULT_START} – ${config?.window_end || DEFAULT_END}`

  return (
    <div className="np-status">
      <div className={`np-status__window${inWindow ? ' is-active' : ''}`}>
        <div className={`np-status__dot${inWindow ? ' is-live' : ''}`} />
        <div className="np-status__window-info">
          {inWindow ? '窗口内 — 自动执行已配置的 agent' : '窗口外 — 等待入窗'}
        </div>
        <div className="np-status__window-time">{windowDisplay}</div>
        <button className="btn btn--ghost btn--xs" onClick={onRefresh} disabled={refreshing} title="刷新状态" style={{ marginLeft: '8px' }}>
          {refreshing ? '⏳' : '🔄'}
        </button>
      </div>

      {runningCount > 0 && (
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--ink-faint)', marginBottom: '6px' }}>
            运行中进程 ({runningCount})
          </div>
          <div className="np-procs">
            {Object.entries(running).map(([name, pid]) => (
              <div key={name} className="np-proc">
                <span className="np-proc__name">{name}</span>
                <span className="np-proc__pid">PID {pid}</span>
                <span className="np-proc__uptime">
                  <span style={{ color: 'var(--ok, #3ecf8e)' }}>●</span> 运行中
                </span>
                <button className="btn btn--ghost btn--xs np-proc__kill" onClick={() => onStop && onStop(name)} title="停止此进程">■</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {runningCount === 0 && (
        <div style={{ padding: '12px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: '11px', color: 'var(--ink-faint)', border: '1px dashed var(--line)', borderRadius: '8px' }}>
          当前无运行中的 agent 进程
        </div>
      )}

      <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
        <button className="btn btn--ghost" onClick={onSpawnNow} disabled={!config?.agents?.length} title="立即按当前配置 spawn（测试用，不受窗口限制）">
          ⚡ 手动触发 Spawn
        </button>
        <button className="btn btn--ghost" onClick={onStop} disabled={runningCount === 0} title="停止所有运行中的 agent">
          ■ 全部停止
        </button>
      </div>
    </div>
  )
}

function CronTasksPanel({ tasks, loading, onRefresh }) {
  return (
    <div className="np-cron">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '6px' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '10px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--ink-faint)' }}>
          定时任务 ({tasks.length})
        </span>
        <button className="btn btn--ghost btn--xs" onClick={onRefresh}>{loading ? '⏳' : '🔄'}</button>
      </div>

      {tasks.length === 0 && !loading && (
        <div className="np-cron__empty">暂无定时任务 — 创建任务时设置 cron_expr 或 run_at</div>
      )}

      {tasks.map(t => {
        const nextRun = t.cron_expr ? parseNextCron(t.cron_expr) : (t.run_at ? new Date(t.run_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : null)
        return (
          <div key={t.id} className="np-cron-row">
            <span className="np-cron-row__cron" title={t.cron_expr || t.run_at}>
              {t.cron_expr || (t.run_at ? '⏰ 一次性' : '—')}
            </span>
            <span className="np-cron-row__title" title={t.title}>{t.title}</span>
            {nextRun && <span className="np-cron-row__next">下次: {nextRun}</span>}
            <span className={`np-cron-row__state np-cron-row__state--${t.state}`}>{t.state}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function PlanView({ onSchedule }) {
  const [plan, setPlan] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [projects, setProjects] = useState([])
  const [selectedProject, setSelectedProject] = useState('')
  const [savedAt, setSavedAt] = useState(null)
  const [loadBusy, setLoadBusy] = useState(false)
  const [nrEnabled, setNrEnabled] = useState(false)
  const [nrSaving, setNrSaving] = useState(false)
  const [nrStatus, setNrStatus] = useState(null)
  const [nrConfig, setNrConfig] = useState(null)
  const [refreshing, setRefreshing] = useState(false)
  const [cronTasks, setCronTasks] = useState([])
  const [cronLoading, setCronLoading] = useState(false)

  const loadNrStatus = useCallback(async () => {
    setRefreshing(true)
    try {
      const data = await api.nrLoadFull()
      setNrStatus(data)
      setNrConfig(data)
      setNrEnabled(!!data.enabled)
    } catch { /* silent */ }
    finally { setRefreshing(false) }
  }, [])

  const loadCronTasks = useCallback(async () => {
    setCronLoading(true)
    try {
      const tasks = await api.nrCronTasks()
      setCronTasks(tasks)
    } catch { /* silent */ }
    finally { setCronLoading(false) }
  }, [])

  useEffect(() => {
    api.listProjects().then(setProjects).catch(() => {})
    loadNrStatus()
    loadCronTasks()
    const interval = setInterval(() => { loadNrStatus(); loadCronTasks(); }, 30000)
    return () => clearInterval(interval)
  }, [loadNrStatus, loadCronTasks])

  const saveNrConfig = async (cfg) => {
    setNrSaving(true)
    try {
      await api.nrSaveConfig({ ...cfg, enabled: nrEnabled })
      await loadNrStatus()
    } catch { /* silent */ }
    finally { setNrSaving(false) }
  }

  const toggleNrEnabled = () => setNrEnabled(e => !e)

  const handleSpawnNow = async () => {
    try { await api.nrSpawnNow(); await loadNrStatus(); } catch { /* silent */ }
  }

  const handleStopAll = async () => {
    if (!window.confirm('确定停止所有运行中的 agent？')) return
    try { await api.nrStop(); await loadNrStatus(); } catch { /* silent */ }
  }

  const _render = (data) => {
    const items = (data.items ?? []).map((t) => {
      const off = hm2min(t.scheduled_start) - WINDOW_START_MIN
      return {
        ...t,
        id: t.task_id ?? t.id,
        start: off < 0 ? off + 1440 : off,
        dur: Math.min(WINDOW_MIN, Math.max(5, t.est_duration_min ?? 30)),
        agent: t.agent_type || '',
      }
    })
    const lastEnd = items.length ? Math.min(WINDOW_MIN, Math.max(...items.map((i) => i.start + i.dur))) : 0
    setPlan({ items, fitted: items.length, total: items.length, filled: lastEnd, overflow: data.has_overflow ? '⚠' : 0, parallel: data.max_parallel || 1, project: data.project })
    setSavedAt(data.generated_at || null)
    onSchedule && onSchedule({ items, fitted: items.length, total: items.length, filled: lastEnd, overflow: data.has_overflow ? '⚠' : 0 })
  }

  const generate = async () => {
    setLoading(true); setError(null)
    try {
      const data = await api.nightPlan(DEFAULT_START, DEFAULT_END, selectedProject || null)
      _render(data)
    } catch { setError('夜间计划接口调用失败，请稍后重试') }
    finally { setLoading(false) }
  }

  const loadSaved = async () => {
    setLoadBusy(true); setError(null)
    try { _render(await api.nightPlanSaved()) }
    catch (e) { setError('未找到已落盘的计划：' + (e.message || '404')) }
    finally { setLoadBusy(false) }
  }

  const occupancy = plan ? Math.round((plan.filled / WINDOW_MIN) * 100) : 0

  return (
    <div className="np">
      <div className="np__head">
        <div>
          <h2>夜间计划 <span className="np__accent">NIGHT SHIFT</span></h2>
          <p className="np__sub">
            {DEFAULT_START} – {DEFAULT_END} · 同 agent 串行 / 跨 agent 并行
            {plan?.parallel > 1 && <span> · 峰值并行 {plan.parallel}</span>}
            {plan?.project && <span> · <b style={{ color: 'var(--accent)' }}>{plan.project}</b></span>}
          </p>
        </div>
        <div className="np__actions">
          <select className="np__project-select" value={selectedProject} onChange={e => setSelectedProject(e.target.value)}>
            <option value="">全部项目</option>
            {projects.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <button className="btn btn--accent" onClick={generate} disabled={loading}>
            {loading ? '生成中…' : '⟳ 生成计划'}
          </button>
          <button className="btn btn--ghost" onClick={loadSaved} disabled={loadBusy} title="读取最近一次落盘的计划">
            {loadBusy ? '加载中…' : '⤓ 上次计划'}
          </button>
          {savedAt && (
            <span className="np__saved" title="已落盘到 ~/.mio_taskhub/night_plans/latest.json">
              ✓ {new Date(savedAt).toLocaleTimeString('zh-CN')}
            </span>
          )}
        </div>
      </div>

      {error && <div className="errorbar" role="alert"><span>▲</span><span>{error}</span></div>}

      <div className="np-panels">
        <Section
          title="执行器配置" icon="⚙" badge={nrEnabled ? 'ON' : 'OFF'} badgeClass={nrEnabled ? 'np-section__badge--ok' : ''} defaultOpen={true}
        >
          <ConfigPanel
            config={nrConfig}
            onSave={saveNrConfig}
            onToggleEnabled={toggleNrEnabled}
            enabled={nrEnabled}
            saving={nrSaving}
            agents={nrConfig?.agents}
          />
        </Section>

        <Section
          title="运行状态" icon="◉"
          badge={nrStatus ? (Object.keys(nrStatus.running_agents || {}).length > 0 ? `${Object.keys(nrStatus.running_agents).length} 进程` : (nrStatus.in_window ? '窗口内' : '空闲')) : null}
          badgeClass={nrStatus?.in_window ? 'np-section__badge--ok' : ''}
        >
          <StatusPanel
            status={nrStatus}
            config={nrConfig}
            onRefresh={loadNrStatus}
            refreshing={refreshing}
            onStop={handleStopAll}
            onSpawnNow={handleSpawnNow}
          />
        </Section>

        <Section
          title="定时任务" icon="⏰" badge={cronTasks.length || null}
        >
          <CronTasksPanel tasks={cronTasks} loading={cronLoading} onRefresh={loadCronTasks} />
        </Section>
      </div>

      {plan ? (
        <>
          <div className="np__stats">
            <div className="np-stat np-stat--primary">
              <div className="np-stat__icon">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M8 2v4M16 2v4M3 10h18"/></svg>
              </div>
              <div className="np-stat__num">{plan.fitted}<span className="np-stat__den"> / {plan.total}</span></div>
              <div className="np-stat__lbl">已排入</div>
            </div>
            <div className="np-stat">
              <div className="np-stat__bar"><div className="np-stat__bar-fill" style={{ width: `${occupancy}%` }} /></div>
              <div className="np-stat__num">{occupancy}<span className="np-stat__den">%</span></div>
              <div className="np-stat__lbl">窗口占用</div>
            </div>
            <div className={`np-stat ${plan.overflow ? 'np-stat--warn' : ''}`}>
              <div className="np-stat__num">{plan.overflow || '—'}</div>
              <div className="np-stat__lbl">溢出待排</div>
            </div>
            <div className="np-stat">
              <div className="np-stat__time">
                <span>{DEFAULT_START}</span>
                <span className="np-stat__sep">→</span>
                <span>{DEFAULT_END}</span>
              </div>
              <div className="np-stat__lbl">时间窗口</div>
            </div>
          </div>

          <div className="np-gantt">
            <div className="np-gantt__header">
              <div className="np-gantt__label-col">任务</div>
              <div className="np-gantt__time-col">
                {HOURS.map((h, i) => (
                  <span key={h} className="np-gantt__hour" style={{ left: `${(i / (HOURS.length - 1)) * 100}%` }}>{h}</span>
                ))}
                <div className="np-gantt__grid">
                  {HOURS.map((_, i) => (
                    <div key={i} className="np-gantt__gridline" style={{ left: `${(i / (HOURS.length - 1)) * 100}%` }} />
                  ))}
                </div>
              </div>
            </div>
            <div className="np-gantt__body">
              {plan.items.map((t, i) => {
                const p = prio(t.priority)
                const left = (t.start / WINDOW_MIN) * 100
                const width = (t.dur / WINDOW_MIN) * 100
                return (
                  <div key={t.id} className="np-row" style={{ '--i': i }}>
                    <div className="np-row__label">
                      <span className={`np-row__prio np-row__prio--p${p.p}`}>{p.label}</span>
                      <span className="np-row__title" title={t.title}>{t.title}</span>
                      {t.agent && <span className="np-row__agent">{t.agent}</span>}
                      {t.fallback_after != null && t.agent && (() => {
                        const elapsed = t.created_at ? (Date.now() - new Date(t.created_at).getTime()) / 1000 : 0
                        const expired = elapsed >= t.fallback_after
                        return (
                          <span className={`np-row__fb ${expired ? 'np-row__fb--open' : ''}`}
                            title={expired ? `已过 fallback 窗口 (${Math.floor(elapsed/3600)}h ≥ ${Math.floor(t.fallback_after/3600)}h)` : `fallback 等待中 (${Math.floor(elapsed/60)}m / ${Math.floor(t.fallback_after/60)}m)`}>
                            {expired ? '🔓' : '⏰'}
                          </span>
                        )
                      })()}
                    </div>
                    <div className="np-row__bar">
                      <div
                        className={`np-row__fill np-row__fill--p${p.p}`}
                        style={{ left: `${left}%`, width: `${Math.max(width, 2)}%` }}
                        title={`${t.title}\n${min2hm(t.start + WINDOW_START_MIN)} – ${min2hm(t.start + t.dur + WINDOW_START_MIN)} · ${fmtDur(t.dur)}`}
                      >
                        <span className="np-row__time">{fmtDur(t.dur)}</span>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="np__legend">
            {[{ p: 3, t: 'P3 紧急', c: 'accent' }, { p: 2, t: 'P2 高', c: 'warn' }, { p: 1, t: 'P1 中', c: 'dim' }, { p: 0, t: 'P0 低', c: 'faint' }].map(x => (
              <span key={x.p} className={`np-legend np-legend--${x.c}`}><i />{x.t}</span>
            ))}
          </div>
        </>
      ) : (
        <div className="np__empty">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <path d="M21 12.8A9 9 0 1111.2 3 7 7 0 0021 12.8z" />
          </svg>
          选择项目，点击「生成排期」预览今晚的执行序列
        </div>
      )}
    </div>
  )
}
