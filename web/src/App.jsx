import { useState, useEffect, useCallback } from 'react'
import { api } from './api'
import Rail from './components/Rail'
import MissionBar from './components/MissionBar'
import BoardView from './components/BoardView'
import ListView from './components/ListView'
import PlanView from './components/PlanView'
import FlowView from './components/FlowView'
import CreateModal from './components/CreateModal'
import TaskDetail from './components/TaskDetail'

const VIEW_KEY = 'mio.view'
const CONTRAST_KEY = 'mio.contrast'

export default function App() {
  const [tasks, setTasks] = useState([])
  const [view, setViewState] = useState(() => {
    try { return localStorage.getItem(VIEW_KEY) || 'board' } catch { return 'board' }
  })
  const [contrast, setContrast] = useState(() => {
    try { return localStorage.getItem(CONTRAST_KEY) === 'high' } catch { return false }
  })
  const [ws, setWs] = useState(false)
  const [error, setError] = useState(null)
  const [modal, setModal] = useState(false)
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastSync, setLastSync] = useState(null)
  const [focus, setFocus] = useState(null) // { id, t }

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
      .catch(e => setError('加载失败: ' + e.message))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [])

  const refresh = useCallback(() => { setRefreshing(true); loadTasks() }, [loadTasks])

  useEffect(() => {
    loadTasks()

    let socket = null
    let timer = null
    let retry = 0
    let closed = false

    const schedule = () => {
      if (timer) clearTimeout(timer)
      const delay = Math.min(1000 * 2 ** retry, 15000)
      retry += 1
      timer = setTimeout(connect, delay)
    }

    const connect = () => {
      if (closed) return
      let s
      try { s = new WebSocket(`ws://${location.host}/ws`) } catch { schedule(); return }
      socket = s
      s.onopen = () => { setWs(true); retry = 0 }
      s.onclose = () => { setWs(false); schedule() }
      s.onerror = () => { try { s.close() } catch { /* ignore */ } }
    }

    connect()
    const interval = setInterval(loadTasks, 5000)
    return () => {
      closed = true
      if (timer) clearTimeout(timer)
      clearInterval(interval)
      if (socket) socket.close()
    }
  }, [loadTasks])

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
      await api.cancelTask(id)
      loadTasks()
    } catch (e) {
      setError('取消失败: ' + e.message)
    }
  }

  const advanceStage = async (id, body) => {
    try { await api.advanceStage(id, body); loadTasks() }
    catch (e) { setError('推进阶段失败: ' + e.message) }
  }

  const advanceTaskStage = (task) => {
    const next = { brainstorming:'design', design:'planning', planning:'ready',
                   implementing:'review', review:'done' }[task.stage]
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
    setView('board')
    setFocus({ id, t: Date.now() })
  }, [setView])

  return (
    <div className="shell">
      <Rail view={view} onChange={setView} wsLive={ws} contrast={contrast} onToggleContrast={toggleContrast} />

      <div className="main">
        <MissionBar
          tasks={tasks}
          ws={ws}
          lastSync={lastSync}
          refreshing={refreshing}
          onRefresh={refresh}
          onOpenModal={() => setModal(true)}
          onFocusLane={focusLane}
        />

        {error && (
          <div className="errorbar" role="alert">
            <span>▲</span>
            <span>{error}</span>
            <button onClick={() => setError(null)} aria-label="关闭错误提示">×</button>
          </div>
        )}

        <main className="viewport">
          <div className="view-fade" key={view}>
            {view === 'board' && (
              <BoardView tasks={tasks} onMove={moveTask} onCancel={cancelTask} onOpen={openTask} loading={loading} focus={focus} />
            )}
            {view === 'list' && (
              <ListView tasks={tasks} onCancel={cancelTask} onOpen={openTask} />
            )}
            {view === 'plan' && (
              <PlanView tasks={tasks} />
            )}
            {view === 'flow' && (
              <FlowView tasks={tasks} onOpen={openTask} onCancel={cancelTask} onAdvance={advanceStage} />
            )}
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
        />
      )}
    </div>
  )
}
