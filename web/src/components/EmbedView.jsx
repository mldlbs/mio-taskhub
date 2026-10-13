import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import { STATE_META, STAGE_META, prio, compositeLabel } from '../constants'
import FlowView from './FlowView'

const STAGE_FLOW = ['brainstorming', 'design', 'planning', 'ready', 'implementing', 'review', 'done']

export default function EmbedView() {
  const [tasks, setTasks] = useState([])
  const [err, setErr] = useState(null)
  const [updated, setUpdated] = useState(null)
  const [sel, setSel] = useState(null)

  const load = useCallback(() => {
    api.listTasks()
      .then(d => { setTasks(d); setErr(null); setUpdated(new Date()) })
      .catch(e => setErr('加载失败: ' + e.message))
  }, [])

  useEffect(() => {
    load()
    let s = null
    let closed = false
    const connect = () => {
      if (closed) return
      try { s = new WebSocket(`ws://${location.host}/ws`) } catch { setTimeout(connect, 3000); return }
      s.onmessage = () => load()
      s.onclose = () => { if (!closed) setTimeout(connect, 3000) }
      s.onerror = () => { try { s.close() } catch { /* ignore */ } }
    }
    connect()
    const t = setInterval(load, 5000)
    return () => { closed = true; if (s) s.close(); clearInterval(t) }
  }, [load])

  const openTask = useCallback(async (t) => {
    try { setSel(await api.getTask(t.id)) } catch (e) { setErr('加载详情失败: ' + e.message) }
  }, [])

  const advance = useCallback(async (id, body) => {
    try { await api.advanceStage(id, body); load() }
    catch (e) { setErr('推进失败: ' + e.message) }
  }, [load])

  const cancel = useCallback(async (id) => {
    try { await api.advanceStage(id, { target_stage: 'cancelled' }); load() }
    catch (e) { setErr('取消失败: ' + e.message) }
  }, [load])

  const closeSel = useCallback(() => setSel(null), [])

  const ready = tasks.filter(t => t.stage === 'ready').length
  const running = tasks.filter(t => t.stage === 'implementing').length

  const stageIdx = sel ? STAGE_FLOW.indexOf(sel.stage) : -1
  const stagePct = stageIdx >= 0 ? Math.round((stageIdx + 1) / STAGE_FLOW.length * 100) : 0
  const stageMeta = sel?.stage ? STAGE_META[sel.stage] : null
  const stateMeta = sel?.state ? STATE_META[sel.state] : null
  const prioMeta = sel ? prio(sel.priority) : null
  const compLabel = sel ? compositeLabel(sel.state, sel.stage || 'ready') : null
  const accLines = sel?.acceptance_criteria
    ? sel.acceptance_criteria.split(/\n+/).map(s => s.trim()).filter(Boolean)
    : []

  return (
    <div className="embed">
      <header className="embed__bar">
        <span className="embed__title">mio-taskhub · 流程看板</span>
        <span className="embed__stats">
          <b>待执行 {ready}</b>
          <b>执行中 {running}</b>
          <b>共 {tasks.length}</b>
        </span>
        {err
          ? <span className="embed__err">▲ {err}</span>
          : updated
            ? <span className="embed__ts">更新于 {updated.toLocaleTimeString()}</span>
            : null}
      </header>

      {tasks.length === 0 && !err ? (
        <div className="embed__empty">
          <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2" />
            <rect x="9" y="3" width="6" height="4" rx="1" />
            <path d="M9 14h6M9 18h4" />
          </svg>
          <p>暂无任务</p>
          <span>在任务中心创建任务后，这里会实时显示进度</span>
        </div>
      ) : (
        <FlowView tasks={tasks} onOpen={openTask} onAdvance={advance} onCancel={cancel} />
      )}

      {sel && (
        <div className="embed__detail" role="dialog" aria-label="任务详情">
          <div className="embed__detail-head">
            <h3>{sel.title}</h3>
            <button className="btn btn--ghost" onClick={closeSel}>×</button>
          </div>

          <div className="embed__detail-meta">
            {stateMeta && (
              <span className={`state-chip is-${stateMeta.tone}`}>
                <i></i>{stateMeta.label}
              </span>
            )}
            {stageMeta && (
              <span className={`chip chip--${stageMeta.tone}`}>{stageMeta.label}</span>
            )}
            {prioMeta && (
              <span className={`np-row__prio np-row__prio--p${prioMeta.p}`}>{prioMeta.label} · {prioMeta.text}</span>
            )}
            {compLabel && (
              <span className={`chip chip--composite chip--${compLabel.tone}`}>{compLabel.label}</span>
            )}
            {sel.target_agent_type && <span className="chip chip--agent">执行: {sel.target_agent_type}</span>}
          </div>

          {sel.stage && (
            <div className="stage-bar embed__stage-bar" title={`${stageIdx + 1}/${STAGE_FLOW.length} · ${stagePct}% · ${stageMeta?.label || sel.stage}`}>
              <div className="stage-bar__fill" style={{ width: stagePct + '%' }} />
            </div>
          )}

          {sel.description && (
            <div className="embed__card">
              <div className="embed__card-title">📋 描述</div>
              <p className="embed__detail-desc">{sel.description}</p>
            </div>
          )}

          {sel.acceptance_criteria && (
            <div className="embed__card">
              <div className="embed__card-title">✅ 验收标准</div>
              {accLines.length > 1 ? (
                <ol className="embed__acc-list">
                  {accLines.map((line, i) => <li key={i}>{line}</li>)}
                </ol>
              ) : (
                <p className="embed__detail-desc">{sel.acceptance_criteria}</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
