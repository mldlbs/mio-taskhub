import { useState, useEffect, useCallback, useRef } from 'react'
import { api } from './api'
import Rail from './components/Rail'
import MissionBar from './components/MissionBar'
import BoardView from './components/BoardView'
import ListView from './components/ListView'
import PlanView from './components/PlanView'
import FlowView from './components/FlowView'
import TopoView from './components/TopoView'
import GanttView from './components/GanttView'
import EmbedView from './components/EmbedView'
import IdeasView from './components/IdeasView'
import TemplatesView from './components/TemplatesView'
import WorkflowView from './components/WorkflowView'
import StatsView from './components/StatsView'
import MemoryView from './components/MemoryView'
import CreateModal from './components/CreateModal'
import TaskDetail from './components/TaskDetail'
import DocPanel from './components/DocPanel'
import ErrorBoundary from './components/ErrorBoundary'
import ErrorBar from './components/ErrorBar'
import ConnectionBanner from './components/ConnectionBanner'

const VIEW_KEY = 'mio.view'
const CONTRAST_KEY = 'mio.contrast'

export default function App() {
  const isEmbed = typeof window !== 'undefined' && window.location.hash === '#/embed'
  if (isEmbed) {
    return <EmbedView />
  }

  const [tasks, setTasks] = useState([])
  const [ideas, setIdeas] = useState([])
  const [view, setViewState] = useState(() => {
    try {
      const v = localStorage.getItem(VIEW_KEY) || 'workflow'
      // 旧版视图（board/stage/flow）统一合并为 workflow
      return ['board', 'stage', 'flow'].includes(v) ? 'workflow' : v
    } catch { return 'workflow' }
  })
  const [contrast, setContrast] = useState(() => {
    try { return localStorage.getItem(CONTRAST_KEY) === 'high' } catch { return false }
  })
  const [ws, setWs] = useState(false)
  const [error, setError] = useState(null)  // { message, type, retry? }
  const [modal, setModal] = useState(false)
  const [detail, setDetail] = useState(null)
  const [docTask, setDocTask] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastSync, setLastSync] = useState(null)
  const [focus, setFocus] = useState(null) // { id, t }
  const [filter, setFilter] = useState({ project: '', workspace: '' })
  const [toasts, setToasts] = useState([])
  const [memoryEvent, setMemoryEvent] = useState(null)  // 最新 memory 事件 (for MemoryView)
  const [wsRetryIn, setWsRetryIn] = useState(null)  // WS 重连倒计时（秒）

  const setView = useCallback((v) => {
    setViewState(v)
    try { localStorage.setItem(VIEW_KEY, v) } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    document.documentElement.dataset.contrast = contrast ? 'high' : 'standard'
    try { localStorage.setItem(CONTRAST_KEY, contrast ? 'high' : 'standard') } catch { /* ignore */ }
  }, [contrast])

  const toggleContrast = useCallback(() => setContrast(c => !c), [])

  const loadTasks = useCallback(() => {
    api.listTasks()
      .then(data => { setTasks(data); setError(null); setLastSync(new Date()) })
      .catch(e => setError({ message: '加载失败: ' + e.message, type: 'api', retry: loadTasks }))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [])

  const loadIdeas = useCallback(() => {
    api.listIdeas()
      .then(data => { setIdeas(data?.ideas || []); setError(null) })
      .catch(() => { /* 想法加载失败静默（不影响主看板） */ })
  }, [])

  const refresh = useCallback(() => { setRefreshing(true); loadTasks() }, [loadTasks])

  const addToast = useCallback((msg, type = 'info') => {
    const id = Date.now() + Math.random()
    setToasts(prev => [...prev.slice(-4), { id, msg, type }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000)
  }, [])

  useEffect(() => {
    loadTasks()
    loadIdeas()

    let socket = null
    let timer = null
    let retry = 0
    let closed = false
    let nextRetryAt = null  // timestamp when next retry fires
    const setNextRetry = () => {
      if (retry === 0) {
        nextRetryAt = null
        return
      }
      const delay = Math.min(1000 * 2 ** (retry - 1), 15000)
      nextRetryAt = Date.now() + delay * 1000
    }
    const tick = () => {
      if (!nextRetryAt) {
        setWsRetryIn(null)
        return
      }
      const remain = Math.max(0, Math.ceil((nextRetryAt - Date.now()) / 1000))
      setWsRetryIn(remain)
    }
    const tickId = setInterval(tick, 250)
    tick()

    const schedule = () => {
      if (timer) clearTimeout(timer)
      const delay = Math.min(1000 * 2 ** retry, 15000)
      retry += 1
      setNextRetry()
      tick()
      timer = setTimeout(connect, delay)
    }

    const connect = () => {
      if (closed) return
      let s
      try { s = new WebSocket(`ws://${location.host}/ws`) } catch { schedule(); return }
      socket = s
      s.onopen = () => { setWs(true); retry = 0; nextRetryAt = null; setWsRetryIn(null) }
      s.onclose = () => { setWs(false); schedule() }
      s.onerror = () => { try { s.close() } catch { /* ignore */ } }
      s.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data)
          if (data.type === 'task_update' && data.event) {
            const ev = data.event
            const p = ev.payload || {}
            if (ev.type === 'task.stage_advanced') addToast(`「${ev.entity_id}」 ${p.from||'?'} → ${p.to||'?'}`, 'info')
            else if (ev.type === 'task.cancelled') addToast(`「${ev.entity_id}」 已取消`, 'warn')
            else if (ev.type === 'task.completed') addToast(`「${ev.entity_id}」 已完成`, 'ok')
            else if (ev.type === 'task.failed') addToast(`「${ev.entity_id}」 失败`, 'danger')
          } else if (data.type === 'idea_update' && data.event) {
            addToast(`想法更新: ${data.event.entity_id}`, 'info')
          } else if (data.type === 'memory_update' && data.event) {
            // 实时把 memory 事件传给 MemoryView（独立 state 避免全量重渲染）
            setMemoryEvent(data)
          }
        } catch { /* ignore parse errors */ }
      }
    }

    connect()
    const interval = setInterval(() => { loadTasks(); loadIdeas() }, 5000)
    return () => {
      closed = true
      if (timer) clearTimeout(timer)
      clearInterval(interval)
      clearInterval(tickId)
      if (socket) socket.close()
    }
  }, [loadTasks, loadIdeas])

  const createTask = async (payload) => {
    try {
      await api.createTask(payload)
      setModal(false)
      loadTasks()
    } catch (e) {
      setError('创建失败: ' + e.message)
    }
  }

  const cancelTask = async (id) => {
    try {
      await api.advanceStage(id, { target_stage: 'cancelled' })
      loadTasks()
    } catch (e) {
      setError('取消失败: ' + e.message)
    }
  }

  const retryTask = async (id) => {
    try {
      await api.retryTask(id)
      loadTasks()
    } catch (e) {
      setError('重试失败: ' + e.message)
      throw e
    }
  }

  const advanceStage = async (id, body) => {
    try { await api.advanceStage(id, body); loadTasks() }
    catch (e) { setError('推进阶段失败: ' + e.message) }
  }

  const moveToStage = async (id, body) => {
    try { await api.moveToStage(id, body); loadTasks() }
    catch (e) { setError('移动阶段失败: ' + (e.message || '阶段不合法')) }
  }

  const advanceTaskStage = (task) => {
    const next = { brainstorming:'design', design:'planning', planning:'ready',
                   ready:'implementing', implementing:'review', review:'done' }[task.stage]
    if (!next) return
    const msg = next === 'design' ? 'Spec 路径: ' :
                next === 'planning' ? 'Plan 路径: ' :
                next === 'done' ? '审查结论: ' : ''
    const val = window.prompt(msg, '')
    if (val === null) return
    const body = { target_stage: next }
    if (next === 'design') body.spec_path = val
    else if (next === 'planning') body.plan_path = val
    else if (next === 'done') body.review_result = val
    advanceStage(task.id, body)
  }

  const moveTask = useCallback((taskId, newState) => {
    setTasks(prev => prev.map(t => t.id === taskId ? { ...t, state: newState } : t))
    setDetail(prev => prev && prev.id === taskId ? { ...prev, state: newState } : prev)
  }, [])

  const moveTaskStage = useCallback(async (taskId, newStage) => {
    setTasks(prev => prev.map(t => t.id === taskId ? { ...t, stage: newStage } : t))
    setDetail(prev => prev && prev.id === taskId ? { ...prev, stage: newStage } : prev)
    try {
      await api.moveToStage(taskId, { target_stage: newStage })
      loadTasks()
    } catch (e) {
      setError('移动阶段失败: ' + (e.message || '阶段不合法'))
      loadTasks()
    }
  }, [loadTasks])

  const closeDetail = useCallback(() => setDetail(null), [])

  const openTask = useCallback(async (t) => {
    try {
      setDetail(await api.getTask(t.id))
      setError(null)
    } catch (e) {
      setDetail(null)
      setError('加载详情失败: ' + e.message)
    }
  }, [])

  const refreshDetail = useCallback(async () => {
    if (detail) {
      try { setDetail(await api.getTask(detail.id)) } catch (e) { /* 静默刷新失败 */ }
    }
  }, [detail])

  const toggleSubtask = useCallback(async (sid, done) => {
    if (!detail) return
    try {
      await api.updateSubtask(detail.id, sid, { status: done ? 'done' : 'pending' })
      await refreshDetail()
    } catch (e) {
      setError('更新子任务失败: ' + e.message)
    }
  }, [detail, refreshDetail])

  const focusLane = useCallback((id) => {
    setView('workflow')
    setFocus({ id, t: Date.now() })
  }, [setView])

  const projectOptions = [...new Set([
    ...tasks.map(t => t.project).filter(Boolean),
    ...ideas.map(i => i.project).filter(Boolean),
  ])].sort()
  const workspaceOptions = [...new Set(tasks.map(t => t.workspace).filter(Boolean))].sort()

  const filteredTasks = filter.project
    ? tasks.filter(t => t.project === filter.project)
    : filter.workspace
      ? tasks.filter(t => t.workspace === filter.workspace)
      : tasks
  const filteredIdeas = filter.project
    ? ideas.filter(i => i.project === filter.project)
    : ideas

  return (
    <div className="shell">
      <Rail view={view} onChange={setView} wsLive={ws} contrast={contrast} onToggleContrast={toggleContrast} />

      <div className="main">
        <MissionBar
          tasks={filteredTasks}
          ws={ws}
          lastSync={lastSync}
          refreshing={refreshing}
          onRefresh={refresh}
          onOpenModal={() => setModal(true)}
          filter={filter}
          onFilterChange={setFilter}
          projectOptions={projectOptions}
          workspaceOptions={workspaceOptions}
        />

        <ConnectionBanner wsLive={ws} lastSync={lastSync} retryIn={wsRetryIn} />

        {error && (
          <ErrorBar
            message={typeof error === 'string' ? error : error.message}
            errorType={typeof error === 'object' ? (error.type || 'unknown') : 'unknown'}
            onRetry={typeof error === 'object' && error.retry ? () => { error.retry(); setError(null) } : null}
            onDismiss={() => setError(null)}
          />
        )}

        {toasts.length > 0 && (
          <div className="toast-container" aria-live="polite">
            {toasts.map(t => (
              <div key={t.id} className={`toast toast--${t.type}`}>{t.msg}</div>
            ))}
          </div>
        )}

        <main className="viewport">
          <div className="view-fade" key={view}>
            <ErrorBoundary onError={(e) => {
              console.error('[App ErrorBoundary]', e);
              try {
                fetch('/api/memory/observer/ingest', {
                  method: 'POST', headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({
                    trace_id: 'web-ui-' + Date.now(),
                    event_type: 'react_render_error',
                    payload: { name: e?.name, message: e?.message?.slice(0, 200) },
                    outcome: 'failure',
                  }),
                }).catch(() => {});
              } catch {}
            }}>
            {view === 'workflow' && (
              <WorkflowView
                tasks={filteredTasks}
                onMoveState={moveTask}
                onMoveStage={moveTaskStage}
                onCancel={cancelTask}
                onOpen={openTask}
                onAdvance={advanceStage}
                loading={loading}
                focus={focus}
              />
            )}
            {view === 'list' && (
              <ListView tasks={filteredTasks} onCancel={cancelTask} onOpen={openTask} />
            )}
            {view === 'plan' && (
              <PlanView tasks={filteredTasks} />
            )}
            {view === 'topo' && (
              <TopoView tasks={filteredTasks} onOpen={openTask} />
            )}
            {view === 'gantt' && (
              <GanttView tasks={filteredTasks} onOpen={openTask} />
            )}
            {view === 'ideas' && (
              <IdeasView ideas={filteredIdeas} onReload={loadIdeas} />
            )}
            {view === 'templates' && (
              <TemplatesView />
            )}
            {view === 'stats' && (
              <StatsView />
            )}
            {view === 'memory' && (
              <MemoryView liveEvent={memoryEvent} />
            )}
            </ErrorBoundary>
          </div>
        </main>
      </div>

      {modal && <CreateModal onClose={() => setModal(false)} onCreate={createTask} />}
      {detail && (
        <TaskDetail
          task={detail}
          tasks={tasks}
          onClose={closeDetail}
          onCancel={cancelTask}
          onMove={moveTask}
          onToggleSubtask={toggleSubtask}
          onAdvance={advanceTaskStage}
          onOpenDocs={() => setDocTask(detail)}
          onOpenTask={openTask}
          onRetry={retryTask}
        />
      )}
      {docTask && (
        <DocPanel task={docTask} onClose={() => setDocTask(null)} />
      )}
    </div>
  )
}
