import { useState, useEffect, useCallback, useMemo, useRef, lazy, Suspense } from 'react'
import { api } from './api'
import { authFetch, wsUrl } from './auth'
import { artifactOf, artifactBody, currentArtifact } from './stageDocs'
import { isNoiseTask } from './noise'
import { STATE_META, STAGE_META } from './constants'
import StageAdvanceDialog from './components/StageAdvanceDialog'
import DecisionInbox from './components/DecisionInbox'
import Rail from './components/Rail'
import MissionBar from './components/MissionBar'
import BoardView from './components/BoardView'
import ListView from './components/ListView'
import PlanView from './components/PlanView'
import FlowView from './components/FlowView'
import TopoView from './components/TopoView'
import GanttView from './components/GanttView'
import EmbedView from './components/EmbedView'
// 想法库依赖 marked/DOMPurify 做 Markdown 渲染，且不是默认视图 → 按需加载
const IdeasView = lazy(() => import('./components/IdeasView'))
import TemplatesView from './components/TemplatesView'
import WorkflowView from './components/WorkflowView'
import StatsView from './components/StatsView'
import ObservabilityView from './components/ObservabilityView'
import MemoryView from './components/MemoryView'
import MioRuntimeView from './components/MioRuntimeView'
import MaterialPanelView from './components/MaterialPanelView'
import SettingsView from './components/SettingsView'
import ScheduledJobsView from './components/ScheduledJobsView'
import CreateModal from './components/CreateModal'
import TaskDetail from './components/TaskDetail'
// DocPanel 依赖 highlight.js（体积大头），只在打开文档时才需要 → 按需加载
const DocPanel = lazy(() => import('./components/DocPanel'))
import ErrorBoundary from './components/ErrorBoundary'
import ErrorBar from './components/ErrorBar'
import ConnectionBanner from './components/ConnectionBanner'
import UpdateBanner from './components/UpdateBanner'

import CommandPalette from './components/CommandPalette'
import ConfirmDialog from './components/ConfirmDialog'
import InputDialog from './components/InputDialog'
import { confirm } from './confirm'

const VIEW_KEY = 'mio.view'
const CONTRAST_KEY = 'mio.contrast'
const THEME_KEY = 'mio.theme'
const SHOW_NOISE_KEY = 'mio.showNoise'

const ALERT_LABELS = {
  TaskQueueStale: '队列陈旧',
  NoTaskTransition: '状态无跃迁',
  NoConsumers: '无消费者在线',
  SchedulerStateLost: '调度状态丢失',
}

/** 视图级骨架：按需加载的视图（想法库/文档面板）首次打开时的 fallback，避免纯白屏 */
function ViewSkeleton({ label = '正在加载…' }) {
  return (
    <div className="view-skeleton" role="status" aria-busy="true" aria-label={label}>
      <div className="view-skeleton__rows" aria-hidden="true">
        {[0, 1, 2, 3].map(i => <div key={i} className="view-skeleton__row" />)}
      </div>
      <p className="view-skeleton__label">{label}</p>
    </div>
  )
}

export default function App() {
  const isEmbed = typeof window !== 'undefined' && window.location.hash === '#/embed'
  if (isEmbed) {
    return <EmbedView />
  }

  const [allTasks, setAllTasks] = useState([])  // 全量（含已取消/机器噪声），展示时按 showCancelled / showNoise 过滤
  const [showCancelled, setShowCancelled] = useState(false)
  // 机器噪声（创意引擎/洞察消费/自测任务）默认隐藏——E7 实验：1771 个任务里 88% 是这类，
  // 不隐藏则真实决策信息被稀释 9:1。默认值的读取要能容忍 localStorage 不可用。
  const [showNoise, setShowNoise] = useState(() => {
    try { return localStorage.getItem(SHOW_NOISE_KEY) === '1' } catch { return false }
  })
  const [ideas, setIdeas] = useState([])
  const [view, setViewState] = useState(() => {
    try {
      const q = new URLSearchParams(window.location.search).get('view')
      if (q) return q
      const v = localStorage.getItem(VIEW_KEY) || 'workflow'
      // 旧版视图（board/stage/flow）统一合并为 workflow
      return ['board', 'stage', 'flow'].includes(v) ? 'workflow' : v
    } catch { return 'workflow' }
  })
  const [contrast, setContrast] = useState(() => {
    try { return localStorage.getItem(CONTRAST_KEY) === 'high' } catch { return false }
  })
  const [theme, setTheme] = useState(() => {
    try {
      const q = new URLSearchParams(window.location.search).get('theme')
      if (q === 'light' || q === 'dark') return q
      return localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'
    } catch { return 'dark' }
  })
  const [alerts, setAlerts] = useState([])
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
  const [wsRetryIn, setWsRetryIn] = useState(null)
  const [cmdOpen, setCmdOpen] = useState(false)  // WS 重连倒计时（秒）
  const [advanceDlg, setAdvanceDlg] = useState(null) // 阶段推进对话框：{ task, next, spec, busy, error }

  const tasks = useMemo(() => {
    let out = allTasks
    if (!showCancelled) out = out.filter(t => t.state !== 'cancelled')
    if (!showNoise) out = out.filter(t => !isNoiseTask(t))
    return out
  }, [allTasks, showCancelled, showNoise])
  const cancelledCount = useMemo(
    () => allTasks.reduce((n, t) => n + (t.state === 'cancelled' ? 1 : 0), 0),
    [allTasks]
  )
  // 被隐藏的噪声条数（不含已取消的重复计数也无妨：这里只用于开关上的提示数字）
  const noiseCount = useMemo(
    () => allTasks.reduce((n, t) => n + (isNoiseTask(t) && t.state !== 'cancelled' ? 1 : 0), 0),
    [allTasks]
  )
  const toggleNoise = useCallback(() => {
    setShowNoise(v => {
      try { localStorage.setItem(SHOW_NOISE_KEY, v ? '0' : '1') } catch { /* ignore */ }
      return !v
    })
  }, [])

  const setView = useCallback((v) => {
    setViewState(v)
    try { localStorage.setItem(VIEW_KEY, v) } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    document.documentElement.dataset.contrast = contrast ? 'high' : 'standard'
    try { localStorage.setItem(CONTRAST_KEY, contrast ? 'high' : 'standard') } catch { /* ignore */ }
  }, [contrast])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try { localStorage.setItem(THEME_KEY, theme) } catch { /* ignore */ }
  }, [theme])

  useEffect(() => {
    let alive = true
    const load = () => {
      api.listAlerts()
        .then(d => { if (alive) setAlerts((d && d.alerts) || []) })
        .catch(() => { /* 告警拉取失败不阻塞主界面 */ })
    }
    load()
    const timer = setInterval(load, 30000)
    return () => { alive = false; clearInterval(timer) }
  }, [])

  const toggleContrast = useCallback(() => setContrast(c => !c), [])
  const toggleTheme = useCallback(() => setTheme(t => (t === 'light' ? 'dark' : 'light')), [])

  const loadTasks = useCallback(() => {
    api.listTasks({ cancelled: 'true' })
      .then(data => { setAllTasks(data); setError(null); setLastSync(new Date()) })
      .catch(e => setError({ message: '加载失败: ' + e.message, type: 'api', retry: loadTasks }))
      .finally(() => { setLoading(false); setRefreshing(false) })
  }, [])

  const loadIdeas = useCallback(() => {
    api.listIdeas()
      .then(data => { setIdeas(data?.ideas || []); setError(null) })
      .catch(() => { /* 想法加载失败静默（不影响主看板） */ })
  }, [])

  const refresh = useCallback(() => { setRefreshing(true); loadTasks() }, [loadTasks])

  // sticky：需要用户处理/确认的消息（提交被拦截、任务失败）不自动消失，
  // 必须手动关闭或点击跳转，否则 4 秒后无声消失等于没提示（Phase 2 C-5）。
  const addToast = useCallback((msg, type = 'info', opts = {}) => {
    const id = Date.now() + Math.random()
    const next = { id, msg, type, sticky: !!opts.sticky, taskId: opts.taskId || null }
    setToasts(prev => {
      // R2 C-5 截断策略修正：
      //  · sticky（提交被拦截 / 执行失败）**免驱逐** —— 旧实现 [...prev.slice(-4), next] 对
      //    sticky 一视同仁，2 条 sticky 在场时再来几条普通 toast 就把它们挤掉，
      //    「不自动消失、必须用户处理」的承诺当场失效；
      //  · 同 taskId 的 sticky 去重 —— 同一任务重复失败/重复被拦截只留最新一条。
      //  · 普通 toast 只保留最近 3 条（低价值流水不占屏幕）。
      const kept = prev.filter(t => !(next.sticky && t.sticky && next.taskId && t.taskId === next.taskId))
      const stickies = kept.filter(t => t.sticky)
      const normals = kept.filter(t => !t.sticky).slice(-3)
      return [...stickies, ...normals, next]
    })
    if (!opts.sticky) {
      setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000)
    }
  }, [])

  const dismissToast = useCallback((id) => setToasts(prev => prev.filter(t => t.id !== id)), [])

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
      try { s = new WebSocket(wsUrl('/ws')) } catch { schedule(); return }
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
            // 全部走 titleOf：以前 stage_advanced/cancelled/completed 显示 entity_id 裸哈希，
            // 而 failed/rejected 显示标题 —— 同类事件两种显示规则（R2 C-5）
            if (ev.type === 'task.stage_advanced') addToast(`「${titleOf(ev.entity_id)}」 ${p.from||'?'} → ${p.to||'?'}`, 'info')
            else if (ev.type === 'task.cancelled') addToast(`「${titleOf(ev.entity_id)}」 已取消`, 'warn')
            else if (ev.type === 'task.completed') addToast(`「${titleOf(ev.entity_id)}」 已完成`, 'ok')
            else if (ev.type === 'task.failed') {
              addToast(`「${titleOf(ev.entity_id)}」 执行失败`, 'danger', { sticky: true, taskId: ev.entity_id })
            }
            // 窄门禁（d468e633 FR-5）：提交被拦截 —— 打断式红色通知。
            // 后端发 entity="task"（events.py 的 MESSAGE_TYPES 不含 run），
            // 所以走 task_update 通道才能收到。
            else if (ev.type === 'run.result_rejected') {
              addToast(`「${titleOf(ev.entity_id)}」 提交被拦截：${p.message || '零改动'}`, 'danger', { sticky: true, taskId: ev.entity_id })
            }
          } else if (data.type === 'idea_update' && data.event) {
            addToast(`想法更新: ${data.event.entity_id}`, 'info')
          } else if (data.type === 'memory_update' && data.event) {
            // 实时把 memory 事件传给 MemoryView（独立 state 避免全量重渲染）
            setMemoryEvent(data)
          } else if (data.type === 'update_status') {
            setLastSync(new Date())   // 触发 UpdateBanner 立即刷新
            if (data.kind === 'update_available' && data.status && data.status.latest) {
              addToast(`发现新版本 v${data.status.latest}`, 'ok')
            }
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
      throw e   // 抛出让 CreateModal 在浮层内部显示，不再「点了没反应」
    }
  }

  /**
   * 取消任务（R2 C-1）：三个入口（看板 ×、详情、列表行内）共用，**唯一**确认点在这里。
   * 之前看板/详情零确认直接执行、列表页另有一套自建弹窗且显示裸 ID，
   * 三处文案与焦点策略不一致。现在统一走 promise 式 confirm()（已接 overlayStack，
   * 自动成为栈顶，Esc 只关它，默认焦点在「取消」键上）。
   * 返回值：true = 已取消，false = 用户放弃 / 失败 —— 调用方据此决定是否关面板。
   */
  const cancelTask = async (id) => {
    const t = allTasks.find(x => x.id === id)
    const ok = await confirm(
      `取消任务「${t?.title || id}」？任务将不再被 agent 领取，可在「已取消」中查看。`,
      { title: '取消任务', danger: true, okText: '取消任务' }
    )
    if (!ok) return false
    try {
      await api.advanceStage(id, { target_stage: 'cancelled' })
      loadTasks()
      // 取消后任务默认从列表消失（showCancelled 关着），必须给一条确认，否则像是点了个寂寞
      addToast(`「${t?.title || id}」 已取消，可在「已取消」中查看`, 'ok')
      return true
    } catch (e) {
      setError('取消失败: ' + e.message)
      return false
    }
  }

  const retryTask = async (id) => {
    try {
      const r = await api.retryTask(id)
      loadTasks()
      // 重试后**不关详情**（用户 2026-11-04）：任务从 failed 变成 queued/ready，
      // 详情里的「重试」按钮要消失、「推进阶段」要从 ready 起算——不刷新快照的话
      // 面板还停在 failed 的旧数据上，接着操作全是错的。
      const fresh = await api.getTask(id)
      setDetail(prev => (prev && prev.id === id ? fresh : prev))
      addToast(`「${fresh.title || id}」 已重新排队，阶段重置为 ${fresh.stage}`, 'ok')
      return true
    } catch (e) {
      setError('重试失败: ' + e.message)
      throw e
    }
  }

  // 返回 { ok, message }：让调用方（如阶段推进对话框）能把错误显示在浮层内部，
  // 而不是只落到 App 层 ErrorBar（会被浮层遮罩盖住，用户看到「点了没反应」）。
  const advanceStage = async (id, body) => {
    try {
      await api.advanceStage(id, body)
      loadTasks()
      return { ok: true }
    } catch (e) {
      const message = '推进阶段失败: ' + (e.message || '阶段不合法')
      setError(message)
      return { ok: false, message }
    }
  }

  const moveToStage = async (id, body) => {
    try { await api.moveToStage(id, body); loadTasks() }
    catch (e) { setError('移动阶段失败: ' + (e.message || '阶段不合法')) }
  }

  // 阶段推进对话框：替代 window.prompt（Phase 2 C-4）
  // toast 里显示任务标题而非裸 ID；WS 回调是长生命周期闭包，用 ref 取最新列表
  const tasksRef = useRef([])
  useEffect(() => { tasksRef.current = allTasks }, [allTasks])
  const titleOf = useCallback((id) => tasksRef.current.find(t => t.id === id)?.title || id, [])

  const confirmAdvance = useCallback(async (val, mode) => {
    const d = advanceDlg
    if (!d) return
    setAdvanceDlg(prev => prev ? { ...prev, busy: true, error: '' } : null)
    const body = { target_stage: d.next }
    Object.assign(body, artifactBody(d.next, val, mode))
    const r = await advanceStage(d.task.id, body)
    if (r && r.ok === false) {
      // 失败不外抛到 App 层 ErrorBar（会被浮层遮罩盖住），显示在对话框内部
      setAdvanceDlg(prev => prev ? { ...prev, busy: false, error: r.message } : null)
      return
    }
    setAdvanceDlg(null)
    addToast(`「${d.task.title}」 已推进到 ${STAGE_META[d.next]?.label || d.next}`, 'ok')
  }, [advanceDlg, advanceStage, addToast])

  /**
   * 阶段推进的唯一入口（R2 C-4 归一）：看板「推进」按钮、依赖图卡片按钮、
   * 依赖图拖拽落点、详情面板全部走这里，产出物统一由 StageAdvanceDialog 收集。
   * @param task        目标任务
   * @param targetStage 可选：显式目标阶段（依赖图拖到任意列 / 回溯时用）；
   *                    不传则按「下一阶段」推进（正向流程的唯一自然语义）。
   */
  const advanceTaskStage = async (task, targetStage) => {
    const next = targetStage || { brainstorming:'design', design:'planning', planning:'ready',
                   ready:'implementing', implementing:'review', review:'done' }[task.stage]
    if (!next || next === task.stage) return
    // 产出物要求查表（stageDocs.js，与后端一致）；无要求的阶段直接推进，不再弹空输入框
    const spec = artifactOf(next)
    if (!spec) {
      // 以前这条分支静默返回：点了/拖了没有任何反馈，用户无法判断是否成功（R2 C-4 补 toast）
      const r = await advanceStage(task.id, { target_stage: next })
      if (r && r.ok) addToast(`「${task.title}」 已推进到 ${STAGE_META[next]?.label || next}`, 'ok')
      return
    }
    setAdvanceDlg({ task, next, spec: { ...spec, initial: currentArtifact(task, next) }, busy: false, error: '' })
  }

  /**
   * 状态（state）**不可**由前端直接修改——这是被证实的后端约束，不是遗漏：
   *  · PATCH /tasks/{id} 的可编辑字段白名单里没有 state（mio_taskhub/api/tasks.py:381-390
   *    的 editable 列表只有 title/priority/project/doc_paths… 等业务字段）；
   *  · task.state 的唯一写入口是 state machine 的 apply_transition（mio_taskhub/workflow/
   *    transitions.py:62），调用方分别是 agent 领取（claim.py）、run 上报（api/runs.py:137）、
   *    阶段推进/取消/重试（api/task_stages.py:326/410/447）与后台调度（background.py:41）。
   * 也就是说：queued→claimed→running→completed 由 **agent 领取与执行** 驱动，
   * 没有「人手动置 state」的接口。
   *
   * 因此这里**绝不**做乐观的本地 state 写入：旧实现只 setAllTasks 不调 API，卡片会在
   * 5 秒轮询后无声弹回原泳道，用户误以为拖拽成功（Phase 2 A1 / C-1 「假成功」）。
   * 正确做法是承认约束 —— 看板「按状态」视图已禁止跨列拖拽（见 BoardView canDrag），
   * 本函数退化为兜底：只解释原因，不改数据。
   */
  const moveTask = useCallback((taskId, newState) => {
    const t = allTasks.find(x => x.id === taskId)
    const label = STATE_META[newState]?.label || newState
    addToast(`「${t?.title || taskId}」的状态由 agent 领取/执行驱动，无法手动改到「${label}」`, 'warn')
  }, [allTasks, addToast])


  const moveTaskStage = useCallback(async (taskId, newStage) => {
    setAllTasks(prev => prev.map(t => t.id === taskId ? { ...t, stage: newStage } : t))
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

  // 键盘快捷键（必须在 openTask 定义之后）
  useEffect(() => {
      const VIEW_IDS = ['workflow', 'list', 'plan', 'topo', 'gantt', 'ideas', 'templates', 'scheduled', 'stats', 'memory']
    const handler = (e) => {
      // 排雷（Phase 2 F2 / R2 C-2）：中文输入法组词中不响应任何单键快捷键 ——
      // 拼音候选词面板打开时按 1-9 是选字、按 n 是输入，不是切视图/新建。
      // keyCode 229 是 Safari/旧浏览器组词态的兜底信号（此时 key 常为 'Process'）。
      if (e.isComposing || e.keyCode === 229) return
      // 富文本场景（contenteditable）里打字同样不该触发全局快捷键。
      const tgt = e.target
      if (tgt?.isContentEditable || tgt?.closest?.('[contenteditable="true"]')) return

      if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault()
        setCmdOpen(v => !v)
        return
      }
      if (!e.ctrlKey && !e.metaKey && !e.altKey && !e.shiftKey) {
        const num = parseInt(e.key)
        if (num >= 1 && num <= 9) {
          const tag = e.target.tagName
          if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
          e.preventDefault()
          setView(VIEW_IDS[num - 1])
          return
        }
        if (e.key === 'n' || e.key === 'N') {
          const tag = e.target.tagName
          if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
          e.preventDefault()
          setModal(true)
          return
        }
        if (e.key === '/') {
          const tag = e.target.tagName
          if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return
          e.preventDefault()
          setCmdOpen(true)
          return
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [setView])

  const handleCmdNavigate = useCallback((viewId, taskId) => {
    setView(viewId)
    if (taskId) openTask({ id: taskId })
  }, [setView, openTask])

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

  const activeAlerts = alerts.filter(a => a.active)
  const alertNames = [...new Set(activeAlerts.map(a => ALERT_LABELS[a.name] || a.name))]
  const hasCritical = activeAlerts.some(a => a.severity === 'critical')

  return (
    <div className="shell">
      <div className="ambient" aria-hidden="true" />
      <Rail view={view} onChange={setView} wsLive={ws} contrast={contrast} onToggleContrast={toggleContrast} theme={theme} onToggleTheme={toggleTheme} />

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
          showCancelled={showCancelled}
          cancelledCount={cancelledCount}
          onToggleCancelled={() => setShowCancelled(s => !s)}
          showNoise={showNoise}
          noiseCount={noiseCount}
          onToggleNoise={toggleNoise}
        />

        {activeAlerts.length > 0 && (
          <div className={`alert-strip alert-strip--${hasCritical ? 'critical' : 'warning'}`} role="alert">
            <span className="alert-strip__dot" aria-hidden="true" />
            <strong className="alert-strip__title">{hasCritical ? '系统异常' : '系统警告'}</strong>
            <span className="alert-strip__msg">
              {activeAlerts.length} 条活跃告警：{alertNames.join(' · ')}
            </span>
            <button className="alert-strip__btn" onClick={() => setView('observability')}>
              查看观测台 →
            </button>
          </div>
        )}

        {/* 决策面板（E7 产品化）：待决策 / 待验收 / 失败待处置——只做入口，点行开详情 */}
        <DecisionInbox allTasks={allTasks} onOpenTask={openTask} />

        <ConnectionBanner wsLive={ws} lastSync={lastSync} retryIn={wsRetryIn} />
        <UpdateBanner eventTick={lastSync} onOpenSettings={() => setView('settings')} />

        {error && (
          <ErrorBar
            message={typeof error === 'string' ? error : error.message}
            errorType={typeof error === 'object' ? (error.type || 'unknown') : 'unknown'}
            onRetry={typeof error === 'object' && error.retry ? () => { error.retry(); setError(null) } : null}
            onDismiss={() => setError(null)}
          />
        )}

        {toasts.length > 0 && (
          // sticky 型（提交被拦截 / 执行失败）用 role=alert，不自动消失
          <div className="toast-container" aria-live="polite">
            {toasts.map(t => (
              <div key={t.id}
                   className={`toast toast--${t.type}${t.sticky ? ' toast--sticky' : ''}`}
                   role={t.sticky ? 'alert' : undefined}>
                <span className="toast__msg">{t.msg}</span>
                {t.sticky && (
                  <>
                    <button className="toast__link" onClick={() => { if (t.taskId) openTask({ id: t.taskId }); dismissToast(t.id) }}>
                      查看
                    </button>
                    <button className="toast__x" onClick={() => dismissToast(t.id)} aria-label="关闭提示">×</button>
                  </>
                )}
              </div>
            ))}
          </div>
        )}

        <main className="viewport">
          <div className="view-fade" key={view}>
            <ErrorBoundary onError={(e) => {
              console.error('[App ErrorBoundary]', e);
              try {
                authFetch('/api/v1/memory/observer/ingest', {
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
                onAdvanceTask={advanceTaskStage}
                loading={loading}
                focus={focus}
              />
            )}
            {view === 'list' && (
              <ListView
                tasks={filteredTasks}
                onCancel={cancelTask}
                onOpen={openTask}
                onOpenModal={() => setModal(true)}
                loading={loading}
              />
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
              <Suspense fallback={<ViewSkeleton label="正在加载想法库…" />}>
                <IdeasView ideas={filteredIdeas} onReload={loadIdeas}
                           onOpenTask={(id) => { setView('workflow'); openTask({ id }) }} />
              </Suspense>
            )}
            {view === 'templates' && (
              <TemplatesView />
            )}
            {view === 'scheduled' && (
              <ScheduledJobsView onNavigateToTask={(id) => { setView('workflow'); openTask({ id }) }} />
            )}
            {view === 'stats' && (
              <StatsView />
            )}
            {view === 'memory' && (
              <MemoryView liveEvent={memoryEvent} />
            )}
            {view === 'mio' && (
              <MioRuntimeView />
            )}
            {view === 'material' && (
              <MaterialPanelView />
            )}
            {view === 'settings' && (
              <SettingsView />
            )}
            {view === 'observability' && (
              <ObservabilityView onOpenTask={(id) => openTask({ id })} />
            )}
            </ErrorBoundary>
          </div>
        </main>
      </div>

      {modal && <CreateModal onClose={() => setModal(false)} onCreate={createTask} />}
      {advanceDlg && (
        <StageAdvanceDialog
          task={advanceDlg.task}
          nextStage={advanceDlg.next}
          spec={advanceDlg.spec}
          busy={advanceDlg.busy}
          error={advanceDlg.error}
          onCancel={() => setAdvanceDlg(null)}
          onConfirm={confirmAdvance}
        />
      )}
      {/* 详情与推进弹窗同为 .overlay（z-index:100），且详情在 DOM 里后渲染，
          于是推进弹窗被整层压住（用户实测「盖住弹窗」）。从详情发起推进时先收起详情：
          弹窗独占焦点，任务标题与目标阶段在弹窗里都有，背后那张详情没有信息增量。 */}
      {detail && (
        <TaskDetail
          task={detail}
          tasks={tasks}
          onClose={closeDetail}
          onCancel={cancelTask}
          onMove={moveTask}
          onToggleSubtask={toggleSubtask}
          onAdvance={(t, s) => { closeDetail(); advanceTaskStage(t, s) }}
          onOpenDocs={() => setDocTask(detail)}
          onOpenTask={openTask}
          onRetry={retryTask}
        />
      )}
      {docTask && (
        // 懒加载首次打开时 chunk 还在路上：给即时占位，否则 fallback={null} 让画面毫无反应
        // （本地 dev 快，生产冷加载可感知数秒）。复用 docpanel 背板样式，视觉上等同面板已开（R2 C-6）。
        <Suspense fallback={
          <div className="docpanel docpanel--loading" role="status" aria-busy="true" aria-label="正在加载文档面板">
            <span className="docpanel__loading-text">正在加载文档…</span>
          </div>
        }>
          <DocPanel task={docTask} onClose={() => setDocTask(null)} />
        </Suspense>
      )}
      <CommandPalette
        open={cmdOpen}
        onClose={() => setCmdOpen(false)}
        onNavigate={handleCmdNavigate}
        tasks={tasks}
      />
      <ConfirmDialog />
      <InputDialog />
    </div>
  )
}
