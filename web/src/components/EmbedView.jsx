import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import FlowView from './FlowView'

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

      <FlowView tasks={tasks} onOpen={openTask} onAdvance={advance} onCancel={cancel} />

      {sel && (
        <div className="embed__detail" role="dialog" aria-label="任务详情">
          <div className="embed__detail-head">
            <h3>{sel.title}</h3>
            <button className="btn btn--ghost" onClick={closeSel}>×</button>
          </div>
          <div className="embed__detail-meta">
            <span>阶段: {sel.stage}</span>
            <span>状态: {sel.state}</span>
            <span>优先级: {sel.priority}</span>
            {sel.target_agent_type && <span>执行: {sel.target_agent_type}</span>}
          </div>
          {sel.description && <p className="embed__detail-desc">{sel.description}</p>}
          {sel.acceptance_criteria && (
            <p className="embed__detail-desc"><b>验收:</b> {sel.acceptance_criteria}</p>
          )}
        </div>
      )}
    </div>
  )
}