import { useState, useCallback, useEffect, useMemo } from 'react'
import { marked } from 'marked'
import { api } from '../api'
import { confirm } from '../confirm'
import { fmtAgo, fmtDate, parseUtc } from '../constants'

// 解析 mio.idea.generate 生成的 description，提取结构化字段
function parseIdeaDescription(desc = '') {
  const result = { goal: '', strategy: '', context: '', constraints: [], relatedMemory: [], raw: desc }
  if (!desc) return result
  
  // 提取 Goal
  const goalMatch = desc.match(/Goal:\s*([\s\S]*?)(?=\n\nStrategy:|$)/)
  if (goalMatch) result.goal = goalMatch[1].trim()
  
  // 提取 Strategy
  const strategyMatch = desc.match(/Strategy:\s*([^\n]+)/)
  if (strategyMatch) result.strategy = strategyMatch[1].trim()
  
  // 提取 Context
  const contextMatch = desc.match(/Context:\s*([\s\S]*?)(?=\n\nConstraints:|$)/)
  if (contextMatch) result.context = contextMatch[1].trim()
  
  // 提取 Constraints
  const constraintsMatch = desc.match(/Constraints:\s*([\s\S]*?)(?=\n\nStrategy prompt:|$)/)
  if (constraintsMatch) {
    const lines = constraintsMatch[1].trim().split('\n')
    result.constraints = lines.map(l => l.replace(/^-\s*/, '').trim()).filter(Boolean)
  }
  
  // 提取 Related Mio memory
  const memoryMatch = desc.match(/Related Mio memory:\s*([\s\S]*?)(?=\n\nContext:|$)/)
  if (memoryMatch) {
    const lines = memoryMatch[1].trim().split('\n')
    result.relatedMemory = lines
      .map(l => l.replace(/^-\s*\[.*?\]\s*/, '').trim())
      .filter(Boolean)
  }
  
  return result
}

const STRATEGY_ICON = {
  'SCAMPER': '🔀',
  'Analogy': '🔗',
  'First-Principles': '🧱',
  'Morphological': '📐',
  'Brainwriting': '✍️',
  'Six Hats': '🎩',
  'default': '💡'
}

const IDEA_META = {
  new:        { label: '记录中', tone: 'dim' },
  fermenting: { label: '发酵中', tone: 'live' },
  formed:     { label: '已成形', tone: 'ok' },
  broken_down:{ label: '已拆解', tone: 'ok' },
  archived:   { label: '已归档', tone: 'dim' },
  cancelled:  { label: '已取消', tone: 'danger' },
  // ADR 状态
  proposed:   { label: 'ADR 提案', tone: 'live' },
  accepted:   { label: 'ADR 已接受', tone: 'ok' },
  rejected:   { label: 'ADR 被拒绝', tone: 'dim' },
  deprecated: { label: 'ADR 已废弃', tone: 'dim' },
  superseded: { label: 'ADR 被取代', tone: 'dim' },
}
const ADR_STATUS_META = {
  proposed:   { label: '提议中', tone: 'live' },
  accepted:   { label: '已接受', tone: 'ok' },
  rejected:   { label: '已拒绝', tone: 'dim' },
  deprecated: { label: '已废弃', tone: 'dim' },
  superseded: { label: '已取代', tone: 'dim' },
}
const NEXT_STATUS = { new: 'fermenting', fermenting: 'formed', formed: 'broken_down' }
// Mio creativity 假设状态（发酵由 Mio 管，生命周期由 taskhub 管）
const MIO_HYP_META = {
  draft:     { label: '草稿' },
  active:    { label: '发酵中' },
  validated: { label: '已验证' },
  rejected:  { label: '已否决' },
}
const ROLE_LABEL = { user: '你', agent: 'agent', ask: 'agent 提问' }
const KIND_LABEL = { review: '评审', status: '状态流转', discussion: '讨论', operation: '操作' }

// 驾驶舱分节（FR-5）：下一步动作置顶 + 7 区块，顺序即渲染顺序
const COCKPIT_SECTIONS = [
  ['goal', '🎯 目标与成功标准'],
  ['hypotheses', '🔬 关键假设与验证'],
  ['mvp', '🗺️ MVP 范围'],
  ['tasks', '📋 任务图'],
  ['risks', '⚠️ 风险与依赖'],
  ['approvals', '🔔 审批点'],
  ['retrospective', '🕳️ 复盘'],
]

function renderCockpitBody(key, data) {
  switch (key) {
    case 'goal': {
      const fields = [
        ['目标', data.goal, '给【谁】解决【什么问题】，因为【为什么现在】'],
        ['成功标准', data.success_metric, '【指标】从【现状】到【目标】，在【期限】内'],
        ['约束', data.constraints, '时间 / 预算 / 人手 / 合规底线'],
        ['不做什么', data.out_of_scope, '明确边界，防范围蔓延'],
      ]
      return fields.map(([label, val, hint]) => (
        <div key={label} className={`cockpit-field${val ? '' : ' cockpit-field--empty'}`}>
          <span className="cockpit-field__label">{label}</span>
          <span className="cockpit-field__value">{val || hint}</span>
        </div>
      ))
    }
    case 'hypotheses':
      return (data.items || []).length ? (
        <ul className="cockpit-list">
          {data.items.map((it, i) => (
            <li key={it.hid || i}>{it.text || it.title || JSON.stringify(it)}</li>
          ))}
        </ul>
      ) : <div className="cockpit-empty">暂无关联假设（接入 Mio 发酵后显示分数与状态）</div>
    case 'mvp':
      return data.mvp_scope
        ? <p className="cockpit-text">{data.mvp_scope}</p>
        : <div className="cockpit-empty">还没圈定 MVP 范围——写下最小可用的交付边界</div>
    case 'tasks': {
      const items = data.items || []
      if (!items.length) {
        return <div className="cockpit-empty">暂无关联任务。用「行为拆解」拆成任务后这里展示任务图</div>
      }
      const tree = (() => {
        if (data.has_cycle) return null // FR-9：有环不做拓扑
        const edges = data.graph?.edges || []
        const hasIn = new Set(edges.map(e => e.to))
        const kids = {}
        edges.forEach(e => { (kids[e.from] = kids[e.from] || []).push(e.to) })
        const byId = Object.fromEntries(items.map(t => [t.id, t]))
        const roots = items.filter(t => !hasIn.has(t.id))
        if (!roots.length) return null
        const seen = new Set()
        const node = (t) => {
          if (!t || seen.has(t.id)) return null
          seen.add(t.id)
          const children = (kids[t.id] || []).map(id => node(byId[id])).filter(Boolean)
          return (
            <li key={t.id} className={`cockpit-task${t.blocked ? ' cockpit-task--blocked' : ''}${t.downstream ? ' cockpit-task--down' : ''}`}>
              <span className="cockpit-task__title">{t.title}</span>
              <span className="tag">{t.stage}</span>
              {t.blocked && <span className="tag">blocked</span>}
              {children.length > 0 && <ul>{children}</ul>}
            </li>
          )
        }
        const out = roots.map(node).filter(Boolean)
        const flat = items.filter(t => !seen.has(t.id)).map(t => node(t)).filter(Boolean)
        return out.length ? [...out, ...flat] : null
      })()
      const list = (
        <ul className="cockpit-list cockpit-tasks">
          {items.map(t => (
            <li key={t.id} className={`cockpit-task${t.blocked ? ' cockpit-task--blocked' : ''}`}>
              <span className="cockpit-task__title">{t.title}</span>
              <span className="tag">{t.stage}</span>
              {t.downstream && <span className="cockpit-task__down">下游</span>}
              {t.blocked && <span className="tag">blocked</span>}
            </li>
          ))}
        </ul>
      )
      const body = (
        <>
          {data.warning && <div className="cockpit-degraded">⚠ {data.warning}</div>}
          {tree
            ? <ul className="cockpit-list cockpit-tasks">{tree}</ul>
            : list}
        </>
      )
      if (data.folded) {
        return (
          <details className="cockpit-fold">
            <summary>共 {data.total} 个任务，展开查看{data.has_cycle ? '（已降级列表）' : '任务图'}</summary>
            {body}
          </details>
        )
      }
      return body
    }
    case 'risks':
      return (data.items || []).length ? (
        <ul className="cockpit-list cockpit-list--risks">
          {data.items.map((r, i) => (
            <li key={i}>
              <span>{r.text || String(r)}</span>
              {r.level && <span className="tag">{r.level}</span>}
              {r.mitigation && <span className="cockpit-mit">→ {r.mitigation}</span>}
            </li>
          ))}
        </ul>
      ) : <div className="cockpit-empty">暂无登记的风险与依赖</div>
    case 'approvals':
      return (data.items || []).length ? (
        <ul className="cockpit-list">
          {data.items.map((a, i) => <li key={i}>{a.title || a.kind || JSON.stringify(a)}</li>)}
        </ul>
      ) : <div className="cockpit-empty">暂无待办审批</div>
    case 'retrospective':
      return (data.items || []).length ? (
        <ul className="cockpit-list">
          {data.items.map((r, i) => <li key={i}>{r.title || r.summary || JSON.stringify(r)}</li>)}
        </ul>
      ) : <div className="cockpit-empty">还没有复盘记录</div>
    default:
      return null
  }
}

export default function IdeasView({ ideas, onReload }) {
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ title: '', description: '', project: '' })
  const [detail, setDetail] = useState(null)
  const [discTopic, setDiscTopic] = useState('')
  const [msgDraft, setMsgDraft] = useState({})
  const [err, setErr] = useState(null)
  const [breaking, setBreaking] = useState(false)
  const [breakRows, setBreakRows] = useState([{ ref: 't1', title: '', deps: '', desc: '', acceptance_criteria: '' }])
  const [submitting, setSubmitting] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editForm, setEditForm] = useState({ title: '', description: '', reason: '' })
  const [hist, setHist] = useState(null)
  // 列表类型过滤：all / idea / adr
  const [typeFilter, setTypeFilter] = useState('all')
  // 是否显示已取消的想法
  const [showCancelled, setShowCancelled] = useState(false)
  // ADR 相关状态
  const [showEvolveModal, setShowEvolveModal] = useState(false)
  const [adrForm, setAdrForm] = useState({ madr_context: '', madr_decision: '', madr_consequences: '', reason: '' })
  const [showAdrAction, setShowAdrAction] = useState(false)
  const [adrAction, setAdrAction] = useState({ action: '', reason: '', replacement_id: '' })
  const [adrList, setAdrList] = useState([])
  // ADR 文档查看
  const [adrMd, setAdrMd] = useState(null)
  const [mdLoading, setMdLoading] = useState(false)
  const [suggesting, setSuggesting] = useState(false)
  // Mio 发酵映射（只读；同步/推进均需显式点击确认）
  const [ferment, setFerment] = useState(null)
  const [fermOpen, setFermOpen] = useState(null)
  const [fermRunning, setFermRunning] = useState(false)
  const [fermNote, setFermNote] = useState('')
  const [cockpit, setCockpit] = useState(null)
  // 结构化字段编辑（FR-1：8 字段一次定型，P0 前端可写；此前仅 API 可改）
  const [fieldEdit, setFieldEdit] = useState(false)
  const [fieldForm, setFieldForm] = useState(null)
  const [fieldSaving, setFieldSaving] = useState(false)

  const fail = useCallback((e) => setErr(e.message || '操作失败'), [])

  const openDetail = useCallback(async (id) => {
    setBreaking(false)
    setBreakRows([{ ref: 't1', title: '', deps: '' }])
    setSubmitting(false)
    setShowHistory(false)
    setEditing(false)
    setFieldEdit(false)
    setFieldForm(null)
    setCockpit(null)
    try {
      const [d, h] = await Promise.all([api.getIdea(id), api.ideaHistory(id)])
      setDetail(d); setHist(h); setErr(null)
    } catch (e) { fail(e) }
    try { setCockpit(await api.getIdeaCockpit(id)) } catch (e) { setCockpit(null) }
  }, [fail])

  const reloadDetail = useCallback(async () => {
    if (!detail) return
    try {
      const [d, h] = await Promise.all([api.getIdea(detail.id), api.ideaHistory(detail.id)])
      setDetail(d); setHist(h)
    } catch (e) { /* 静默 */ }
    try { setCockpit(await api.getIdeaCockpit(detail.id)) } catch (e) { /* 静默：驾驶舱降级不阻塞详情 */ }
  }, [detail])

  const dismissNextAction = async () => {
    if (!detail || !cockpit?.next_action) return
    try {
      await api.dismissIdeaNextAction(detail.id, cockpit.next_action.rule_id)
      setCockpit(await api.getIdeaCockpit(detail.id))
    } catch (e) { fail(e) }
  }

  const openFieldEdit = () => {
    const d = detail || {}
    const toLines = (v) => Array.isArray(v)
      ? v.map(x => (x && typeof x === 'object' ? (x.text || x.title || JSON.stringify(x)) : String(x))).join('\n')
      : ''
    setFieldForm({
      goal: d.goal || '',
      success_metric: d.success_metric || '',
      constraints: d.constraints || '',
      out_of_scope: d.out_of_scope || '',
      mvp_scope: d.mvp_scope || '',
      tags: (Array.isArray(d.tags) ? d.tags : []).join(', '),
      assumptions: toLines(d.assumptions),
      risks: JSON.stringify(Array.isArray(d.risks) ? d.risks : [], null, 2),
    })
    setFieldEdit(true)
  }

  const saveFieldEdit = async () => {
    if (!detail || !fieldForm) return
    let risks
    try { risks = JSON.parse(fieldForm.risks || '[]') } catch (e) { setErr(`风险 JSON 解析失败：${e.message}`); return }
    if (!Array.isArray(risks)) { setErr('风险必须是 JSON 数组，形如 [{"text":"...","level":"low","mitigation":"..."}]'); return }
    // 假设按行编辑：与原条目按下标合并，保留 hid 等既有属性（FR-2 单条写回依赖 hid）
    const orig = Array.isArray(detail.assumptions) ? detail.assumptions : []
    const rows = fieldForm.assumptions.split('\n').map(s => s.trim()).filter(Boolean)
    const assumptions = rows.map((text, idx) => {
      const old = orig[idx]
      if (old && typeof old === 'object') return { ...old, text }
      return typeof old === 'string' ? text : { text }
    })
    const tags = fieldForm.tags.split(',').map(s => s.trim()).filter(Boolean)
    setFieldSaving(true)
    try {
      await api.updateIdea(detail.id, {
        goal: fieldForm.goal,
        success_metric: fieldForm.success_metric,
        constraints: fieldForm.constraints,
        out_of_scope: fieldForm.out_of_scope,
        mvp_scope: fieldForm.mvp_scope,
        tags,
        assumptions,
        risks,
      })
      setFieldEdit(false); setFieldForm(null)
      await reloadDetail()
      onReload()
      setErr(null)
    } catch (e) { fail(e) } finally { setFieldSaving(false) }
  }

  const submitIdea = async () => {
    const title = form.title.trim()
    if (!title) return
    try {
      await api.createIdea({ title, description: form.description, project: form.project })
      setCreating(false); setForm({ title: '', description: '', project: '' })
      onReload(); setErr(null)
    } catch (e) { fail(e) }
  }

  const advance = async (status) => {
    try { await api.advanceIdea(detail.id, status); await reloadDetail(); onReload() }
    catch (e) { fail(e) }
  }

  const loadFerment = useCallback(async () => {
    try { setFerment(await api.mioFerment()) } catch (e) { setFerment(null) }
  }, [])

  useEffect(() => { loadFerment() }, [loadFerment])

  const syncHyp = async (hid) => {
    try { await api.mioFermentSync(hid); await loadFerment(); onReload(); setErr(null) }
    catch (e) { fail(e) }
  }

  const advanceFromFerm = async (h) => {
    try { await api.advanceIdea(h.action.idea_id, h.action.to); await loadFerment(); onReload() }
    catch (e) { fail(e) }
  }

  // 跑一次 Mio 发酵：调 LLM 复审 active 假设（10-60s、消耗额度），成功后刷新映射
  const runFerment = async () => {
    if (fermRunning) return
    const ok = await confirm('跑一次发酵会调用 LLM（约 10-60 秒、消耗额度），可能晋升/打回假设。继续？',
                             { title: '跑一次发酵', okText: '开始发酵' })
    if (!ok) return
    setFermRunning(true); setFermNote('')
    try {
      const res = await api.mioFermentRun(5)
      if (res.error) { fail(new Error(res.error)) } else {
        const rs = Array.isArray(res.results) ? res.results : []
        const promote = rs.filter(r => r.verdict === 'promote').length
        setFermNote(` · 刚发酵 ${res.fermented ?? rs.length} 条${promote ? ` · ${promote} 晋升` : ''}`)
        await loadFerment()
      }
    } catch (e) { fail(e) }
    finally { setFermRunning(false) }
  }

  const handleSuggest = async () => {
    if (!detail) return
    setSuggesting(true)
    try {
      const res = await api.suggestTasks(detail.id, {})
      if (res.suggestions && res.suggestions.length > 0) {
        setBreakRows(res.suggestions.map(s => ({
          ref: s.ref,
          title: s.title,
          deps: (s.depends_on || []).join(', '),
          desc: s.description || '',
          acceptance_criteria: s.acceptance_criteria || '',
        })))
        setBreaking(true)
      } else {
        setErr(res.message || '未能从描述中提取任务草案，请补充描述或开启讨论后再试')
      }
    } catch (e) { fail(e) }
    finally { setSuggesting(false) }
  }

  const newDiscussion = async () => {
    const topic = discTopic.trim()
    if (!topic || !detail) return
    try {
      await api.openDiscussion({ idea_id: detail.id, topic, agent: 'me', stage: 'brainstorming' })
      setDiscTopic(''); await reloadDetail()
    } catch (e) { fail(e) }
  }

  const reply = async (did) => {
    const content = (msgDraft[did] || '').trim()
    if (!content) return
    try {
      await api.replyDiscussion(did, { content, role: 'user', author: 'me' })
      setMsgDraft({ ...msgDraft, [did]: '' })
      await reloadDetail()
    } catch (e) { fail(e) }
  }

  const closeDisc = async (d) => {
    const conclusions = window.prompt(`关闭讨论「${d.topic}」——写下结论：`, d.conclusions || '')
    if (conclusions === null) return
    try {
      await api.closeDiscussion(d.id, { conclusions, summary: d.summary })
      await reloadDetail()
    } catch (e) { fail(e) }
  }

  const addBreakRow = () =>
    setBreakRows(r => [...r, { ref: `t${r.length + 1}`, title: '', deps: '', desc: '', acceptance_criteria: '' }])

  const submitBreakdown = async () => {
    if (submitting) return
    const rows = breakRows.filter(r => r.title.trim())
    if (!rows.length) return
    setSubmitting(true)
    const tasks = rows.map(r => ({
      title: r.title.trim(),
      ref: r.ref || undefined,
      description: r.desc || '',
      acceptance_criteria: r.acceptance_criteria || '',
      depends_on: r.deps.split(',').map(s => s.trim()).filter(Boolean),
    }))
    try {
      await api.breakdownIdea(detail.id, { tasks })
      setBreaking(false)
      setBreakRows([{ ref: 't1', title: '', deps: '', desc: '', acceptance_criteria: '' }])
      await reloadDetail()
      onReload()
    } catch (e) { fail(e) }
    finally { setSubmitting(false) }
  }

  const submitEdit = async () => {
    try {
      await api.updateIdea(detail.id, {
        title: editForm.title.trim(),
        description: editForm.description,
        change_reason: editForm.reason.trim(),
      })
      setEditing(false)
      await reloadDetail()
      onReload()
    } catch (e) { fail(e) }
  }

  // ADR 演化为 ADR
  const evolveToAdr = async () => {
    if (!detail) return
    try {
      await api.evolveToAdr(detail.id, {
        madr_context: adrForm.madr_context,
        madr_decision: adrForm.madr_decision,
        madr_consequences: adrForm.madr_consequences,
        reason: adrForm.reason,
      })
      setShowEvolveModal(false)
      setAdrForm({ madr_context: '', madr_decision: '', madr_consequences: '', reason: '' })
      await reloadDetail()
      onReload()
    } catch (e) { fail(e) }
  }

  // ADR 状态操作
  const executeAdrAction = async () => {
    if (!detail || !adrAction.action) return
    try {
      const body = { action: adrAction.action, reason: adrAction.reason }
      if (adrAction.action === 'supersede') {
        body.replacement_id = adrAction.replacement_id
      }
      await api.adrAction(detail.id, body)
      setShowAdrAction(false)
      setAdrAction({ action: '', reason: '', replacement_id: '' })
      await reloadDetail()
      onReload()
    } catch (e) { fail(e) }
  }

  // 加载 ADR 列表用于取代选择
  const loadAdrList = async () => {
    try {
      const res = await api.listIdeas({ idea_type: 'adr', adr_status: 'accepted' })
      setAdrList(res.ideas || [])
    } catch (e) { /* 静默 */ }
  }

  // 查看 ADR 原始 Markdown
  const openAdrMd = async () => {
    if (!detail) return
    setMdLoading(true)
    try {
      const res = await api.adrMarkdown(detail.id)
      setAdrMd(res)
    } catch (e) { fail(e) }
    finally { setMdLoading(false) }
  }

  useEffect(() => {
    if (!detail) return
    const t = setInterval(reloadDetail, 5000)
    return () => clearInterval(t)
  }, [detail, reloadDetail])

  const next = NEXT_STATUS[detail?.status]
  const nextMeta = next ? IDEA_META[next] : null

  const filtered = typeFilter === 'all' ? ideas : ideas.filter(i => i.idea_type === typeFilter)
  const visible = showCancelled ? filtered : filtered.filter(i => i.status !== 'cancelled')
  const adrCount = ideas.filter(i => i.idea_type === 'adr').length
  const cancelledCount = ideas.filter(i => i.status === 'cancelled').length

  return (
    <div className="ideas">
      {err && <div className="errorbar" role="alert"><span>▲</span><span>{err}</span><button onClick={() => setErr(null)} aria-label="关闭">×</button></div>}

      <div className="ideas__top">
        <h2 className="ideas__title">想法与需求</h2>
        <button className="btn btn--primary" onClick={() => setCreating(c => !c)}>{creating ? '取消' : '+ 记个想法'}</button>
      </div>

      <div className="ideas__filter">
        {[
          { k: 'all', label: `全部 ${ideas.length}` },
          { k: 'idea', label: `想法 ${ideas.length - adrCount}` },
          { k: 'adr', label: `ADR ${adrCount}` },
        ].map(f => (
          <button key={f.k} className={`ideas__filter-btn${typeFilter === f.k ? ' is-on' : ''}`}
            onClick={() => setTypeFilter(f.k)} aria-pressed={typeFilter === f.k}>
            {f.label}
          </button>
        ))}
      </div>

      {cancelledCount > 0 && (
        <div className="ideas__cancel-toggle">
          <button className="btn btn--ghost btn--sm" onClick={() => setShowCancelled(s => !s)}>
            {showCancelled ? '▾ 隐藏已取消' : `▸ 显示已取消（${cancelledCount}）`}
          </button>
        </div>
      )}

      {creating && (
        <div className="ideas__create">
          <input className="inp" placeholder="标题（一句话想法）" value={form.title}
                 onChange={e => setForm({ ...form, title: e.target.value })} autoFocus />
          <textarea className="inp" placeholder="描述 / 背景 / 想达成什么（可选）" rows={3} value={form.description}
                    onChange={e => setForm({ ...form, description: e.target.value })} />
          <div className="ideas__create-row">
            <input className="inp" placeholder="项目（可选）" value={form.project}
                   onChange={e => setForm({ ...form, project: e.target.value })} />
            <button className="btn btn--primary" onClick={submitIdea} disabled={!form.title.trim()}>保存</button>
          </div>
        </div>
      )}

      {ferment && ferment.available && ferment.items.length > 0 && (
        <div className="ideas__mio">
          <div className="ideas__mio-head">
            <span className="ideas__mio-h">Mio 发酵</span>
            <span className="ideas__mio-hint">
              假设状态 → taskhub 生命周期（只读映射；{ferment.counts?.linked ?? 0}/{ferment.counts?.total ?? 0} 已关联
              {ferment.counts?.pending_actions ? ` · ${ferment.counts.pending_actions} 个可推进` : ''}）
              {fermNote}
            </span>
            <button className="btn btn--ghost btn--sm" onClick={runFerment}
                    disabled={fermRunning}
                    title="调用 LLM 复审 active 假设：重打分、晋升/打回（约 10-60 秒）">
              {fermRunning ? '发酵中…' : '跑一次发酵'}
            </button>
          </div>
          <div className="ideas__mio-list">
            {[...ferment.items]
              .sort((a, b) => (b.detail?.createdAt || 0) - (a.detail?.createdAt || 0))
              .map(h => {
              const mm = MIO_HYP_META[h.status] || { label: h.status || '?' }
              const sm = h.suggested_status && (IDEA_META[h.suggested_status] || { label: h.suggested_status })
              return (
                <div key={h.id} className={`ideas__mio-item${fermOpen === h.id ? ' is-open' : ''}`}>
                  <span className={`ideas__mio-chip ideas__mio-chip--${h.status}`}>{mm.label}</span>
                  <span className="ideas__mio-name ideas__mio-name--click" title={h.title}
                        onClick={() => setFermOpen(fermOpen === h.id ? null : h.id)}>
                    {fermOpen === h.id ? '▾' : '▸'} {h.title}
                  </span>
                  <span className="ideas__mio-score mono" title="novelty / feasibility / impact / score">
                    N{h.novelty} F{h.feasibility} I{h.impact} Σ{h.score}
                  </span>
                  <span className="ideas__mio-map">→ {sm ? sm.label : '—'}</span>
                  {h.linked ? (
                    <span className="ideas__mio-idea">
                      <span className="ideas__mio-idea-name" title={h.idea.title}>{h.idea.title}</span>
                      <span className="ideas__mio-idea-status">{(IDEA_META[h.idea.status] || {}).label || h.idea.status}</span>
                      {h.action && (
                        <button className="btn btn--ghost btn--sm"
                          onClick={() => advanceFromFerm(h)}>
                          推进 → {(IDEA_META[h.action.to] || {}).label || h.action.to}
                        </button>
                      )}
                    </span>
                  ) : (
                    <button className="btn btn--ghost btn--sm" onClick={() => syncHyp(h.id)}>同步为想法</button>
                  )}
                  {fermOpen === h.id && (
                    <div className="ideas__mio-detail">
                      {h.detail?.idea && <p className="ideas__mio-detail-p"><b>假设正文</b>{h.detail.idea}</p>}
                      {h.detail?.expectedBenefit && <p className="ideas__mio-detail-p"><b>预期收益</b>{h.detail.expectedBenefit}</p>}
                      {h.detail?.risk && <p className="ideas__mio-detail-p"><b>风险</b>{h.detail.risk}</p>}
                      {h.detail?.rejectionReason && <p className="ideas__mio-detail-p"><b>被拒原因</b>{h.detail.rejectionReason}</p>}
                      <div className="ideas__mio-detail-meta mono">
                        {h.detail?.strategy && <span>strategy: {h.detail.strategy}</span>}
                        {h.detail?.createdAt && <span>createdAt: {new Date(h.detail.createdAt).toLocaleString()}</span>}
                        {h.detail?.sourceLabels?.length > 0 && <span>sources: {h.detail.sourceLabels.join(' | ')}</span>}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      <div className="ideas__cols">
        <div className="ideas__list">
          {visible.length === 0 && <div className="ideas__empty">{typeFilter === 'adr' ? '还没有 ADR。把想法推进到「已成形」后可演化为 ADR。' : '还没有想法。点右上角「记个想法」，随手把需求、点子、改进记下来。'}</div>}
          {visible.map(i => {
            const isAdr = i.idea_type === 'adr'
            const m = isAdr
              ? (ADR_STATUS_META[i.adr_status] || ADR_STATUS_META.proposed)
              : (IDEA_META[i.status] || IDEA_META.new)
            return (
              <button key={i.id} className={`idea-card${detail?.id === i.id ? ' is-active' : ''}${isAdr ? ' idea-card--adr' : ''}`} onClick={() => openDetail(i.id)}>
                <div className="idea-card__head">
                  {isAdr && i.adr_number != null && <span className="tag tag--version adr-num">ADR-{String(i.adr_number).padStart(3, '0')}</span>}
                  <span className="idea-card__title">{i.title}</span>
                  {!isAdr && i.version > 1 && <span className="tag tag--version">v{i.version}</span>}
                  <span className={`badge badge--${m.tone}`}>{m.label}</span>
                </div>
                {i.project && <div className="idea-card__project">{i.project}</div>}
                <div className="idea-card__foot">
                  <span>{fmtAgo(i.updated_at)}</span>
                  {i.labels?.map(l => <span key={l} className="tag">{l}</span>)}
                </div>
              </button>
            )
          })}
        </div>

        <div className="ideas__detail">
          {!detail && <div className="ideas__empty">选中左侧一个想法，查看详情、开讨论会。</div>}
          {detail && (
            <>
              <div className="idea-detail__head">
                {detail.idea_type === 'adr' && detail.adr_number != null && (
                  <span className="tag tag--version adr-num">ADR-{String(detail.adr_number).padStart(3, '0')}</span>
                )}
                <h3>{detail.title}</h3>
                <span className="tag tag--version">v{detail.version}</span>
                <span className={`badge badge--${(IDEA_META[detail.status] || IDEA_META.new).tone}`}>
                  {(IDEA_META[detail.status] || IDEA_META.new).label}
                </span>
              </div>
              {detail.project && <div className="idea-card__project">项目：{detail.project}</div>}
              {detail.last_reviewed_at && (
                <div className="idea-card__project">上次评审：{fmtDate(detail.last_reviewed_at)}</div>
              )}

              {cockpit && (
                <div className="idea-cockpit">
                  <div className="cockpit-block cockpit-block--next">
                    <div className="cockpit-block__h">⏭️ 下一步动作</div>
                    {cockpit.next_action
                      ? (
                        <div className="cockpit-next__body">
                          <span title={cockpit.next_action.reason || ''}>{cockpit.next_action.action}</span>
                          <button className="btn btn--ghost cockpit-next__dismiss"
                                  onClick={dismissNextAction}
                                  title="忽略 7 天；条件变化后自动重现">忽略</button>
                        </div>
                      )
                      : <div className="cockpit-empty">暂无建议的下一步动作</div>}
                  </div>
                  <div className="cockpit-block cockpit-block--fields">
                    <div className="cockpit-block__h">
                      ✏️ 结构化字段
                      {!fieldEdit && (
                        <button className="btn btn--ghost cockpit-fields__btn" onClick={openFieldEdit}>编辑</button>
                      )}
                    </div>
                    {fieldEdit && fieldForm ? (
                      <div className="cockpit-fields">
                        {[
                          ['goal', '目标', '给【谁】解决【什么问题】，因为【为什么现在】'],
                          ['success_metric', '成功标准', '【指标】从【现状】到【目标】，在【期限】内'],
                          ['constraints', '约束', '时间 / 预算 / 人手 / 合规底线'],
                          ['out_of_scope', '不做什么', '明确边界，防范围蔓延'],
                          ['mvp_scope', 'MVP 范围', '最小可用的交付边界'],
                        ].map(([k, label, hint]) => (
                          <label key={k} className="cockpit-fields__item">
                            <span>{label}</span>
                            <textarea className="cockpit-fields__ta" placeholder={hint}
                                      value={fieldForm[k]}
                                      onChange={e => setFieldForm(f => ({ ...f, [k]: e.target.value }))} />
                          </label>
                        ))}
                        <label className="cockpit-fields__item">
                          <span>标签（逗号分隔；命中高风险词表会标 ⚠）</span>
                          <input className="cockpit-fields__in" placeholder="如：高风险, 合规"
                                 value={fieldForm.tags}
                                 onChange={e => setFieldForm(f => ({ ...f, tags: e.target.value }))} />
                        </label>
                        <label className="cockpit-fields__item">
                          <span>关键假设（一行一条，原条目属性保留）</span>
                          <textarea className="cockpit-fields__ta" placeholder="用户会每天看驾驶舱"
                                    value={fieldForm.assumptions}
                                    onChange={e => setFieldForm(f => ({ ...f, assumptions: e.target.value }))} />
                        </label>
                        <label className="cockpit-fields__item">
                          <span>风险（JSON 数组）</span>
                          <textarea className="cockpit-fields__ta cockpit-fields__ta--json" spellCheck={false}
                                    placeholder='[{"text":"字段为填而填","level":"low","mitigation":"门控只做结构校验"}]'
                                    value={fieldForm.risks}
                                    onChange={e => setFieldForm(f => ({ ...f, risks: e.target.value }))} />
                        </label>
                        <div className="cockpit-fields__actions">
                          <button className="btn btn--primary" disabled={fieldSaving} onClick={saveFieldEdit}>
                            {fieldSaving ? '保存中…' : '保存'}
                          </button>
                          <button className="btn btn--ghost" onClick={() => { setFieldEdit(false); setFieldForm(null) }}>
                            取消
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="cockpit-empty">目标、成功标准等 8 个结构化字段——点「编辑」补全后，下一步动作会随之更新</div>
                    )}
                  </div>
                  {COCKPIT_SECTIONS.map(([key, title]) => {
                    const sec = cockpit.sections?.[key]
                    if (!sec) return null
                    const degraded = sec.status === 'degraded'
                    return (
                      <div key={key} className={`cockpit-block${degraded ? ' cockpit-block--degraded' : ''}`}>
                        <div className="cockpit-block__h">{title}</div>
                        {degraded
                          ? <div className="cockpit-degraded">区块暂不可用（{sec.reason || '未知原因'}），其余内容不受影响</div>
                          : renderCockpitBody(key, sec.data || {})}
                      </div>
                    )
                  })}
                </div>
              )}

              <div className="idea-detail__desc">
                {(() => {
                  const parsed = parseIdeaDescription(detail.description)
                  const isMioIdea = detail.labels?.includes('mio-intelligence')
                  if (!isMioIdea || (!parsed.goal && !parsed.strategy && !parsed.context)) {
                    return <p>{detail.description || '（暂无描述）'}</p>
                  }
                  return (
                    <div className="mio-idea-struct">
                      <div className="mio-idea__meta">
                        <span className="mio-idea__source">🧠 mio-intelligence 生成</span>
                        {parsed.strategy && (
                          <span className="mio-idea__strategy">
                            {STRATEGY_ICON[parsed.strategy] || STRATEGY_ICON.default} {parsed.strategy}
                          </span>
                        )}
                        {detail.labels?.map(l => (
                          <span key={l} className="mio-idea__label">{l}</span>
                        ))}
                      </div>
                      {parsed.goal && (
                        <div className="mio-idea__field">
                          <span className="mio-idea__field-label">目标</span>
                          <p className="mio-idea__field-value">{parsed.goal}</p>
                        </div>
                      )}
                      {parsed.context && (
                        <div className="mio-idea__field">
                          <span className="mio-idea__field-label">背景</span>
                          <p className="mio-idea__field-value">{parsed.context}</p>
                        </div>
                      )}
                      {parsed.constraints.length > 0 && (
                        <div className="mio-idea__field">
                          <span className="mio-idea__field-label">约束</span>
                          <ul className="mio-idea__constraints">
                            {parsed.constraints.map((c, i) => (
                              <li key={i}>{c}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {parsed.relatedMemory.length > 0 && (
                        <details className="mio-idea__field mio-idea__memories">
                          <summary>🧩 关联记忆 ({parsed.relatedMemory.length})</summary>
                          <ul>
                            {parsed.relatedMemory.map((m, i) => (
                              <li key={i} className="mono">{m}</li>
                            ))}
                          </ul>
                        </details>
                      )}
                    </div>
                  )
                })()}
              </div>

              <div className="idea-detail__history">
                <button className="btn btn--ghost" onClick={() => setShowHistory(s => !s)}>
                  变更历史（{detail.changes?.length || 0}）{showHistory ? '▾' : '▸'}
                </button>
                {showHistory && (
                  <div className="idea-detail__changes">
                    {(detail.changes || []).map(ch => (
                      <div key={ch.id} className="change-row">
                        <span className="tag tag--version">v{ch.version}</span>
                        <span className="change-row__at">{parseUtc(ch.created_at).toLocaleString()}</span>
                        {ch.reason && <span className="change-row__reason">{ch.reason}</span>}
                        <span className="change-row__fields">{Object.keys(ch.diff || {}).join(', ')}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="idea-detail__actions">
                {next && nextMeta && (
                  <button className="btn" onClick={() => advance(next)}>→ 推进为「{nextMeta.label}」</button>
                )}
                {detail.status === 'formed' && detail.idea_type === 'idea' && (
                  <button className="btn btn--accent" onClick={() => setShowEvolveModal(true)}>
                    → 演化为 ADR
                  </button>
                )}
                {detail.status === 'formed' && detail.idea_type === 'idea' && (
                  <button className="btn btn--accent" onClick={() => setBreaking(b => !b)}>
                    {breaking ? '取消拆解' : '→ 拆解为任务'}
                  </button>
                )}
                {detail.status === 'formed' && detail.idea_type === 'idea' && (
                  <button className="btn btn--accent" onClick={handleSuggest} disabled={suggesting}>
                    {suggesting ? '拆解中…' : '智能拆解'}
                  </button>
                )}
                {/* ADR 状态操作按钮 */}
                {detail.idea_type === 'adr' && detail.adr_status === 'proposed' && (
                  <>
                    <button className="btn btn--ok" onClick={() => { setAdrAction({ action: 'accept', reason: '' }); setShowAdrAction(true) }}>
                      接受
                    </button>
                    <button className="btn btn--danger" onClick={() => { setAdrAction({ action: 'reject', reason: '' }); setShowAdrAction(true) }}>
                      拒绝
                    </button>
                  </>
                )}
                {detail.idea_type === 'adr' && detail.adr_status === 'accepted' && (
                  <>
                    <button className="btn btn--warning" onClick={() => { setAdrAction({ action: 'deprecate', reason: '' }); setShowAdrAction(true) }}>
                      废弃
                    </button>
                    <button className="btn btn--accent" onClick={() => { setAdrAction({ action: 'supersede', reason: '', replacement_id: '' }); loadAdrList(); setShowAdrAction(true) }}>
                      取代
                    </button>
                  </>
                )}
                {(detail.status === 'new' || detail.status === 'fermenting' || detail.status === 'formed') && (
                  <button className="btn btn--ghost" onClick={() => advance('archived')}>归档</button>
                )}
                <button className="btn btn--ghost" onClick={() => {
                  setEditForm({ title: detail.title, description: detail.description, reason: '' })
                  setEditing(true)
                }}>编辑</button>
              </div>

              {/* ADR 信息展示 */}
              {detail.idea_type === 'adr' && (
                <div className="idea-detail__adr">
                  <div className="idea-detail__adr-head">
                    <span>ADR 信息</span>
                    <span style={{ display: 'inline-flex', gap: 8, alignItems: 'center' }}>
                      {detail.adr_number != null && (
                        <button className="btn btn--ghost" onClick={openAdrMd} disabled={mdLoading}>
                          {mdLoading ? '加载中…' : '📄 查看文档'}
                        </button>
                      )}
                      <span className={`badge badge--${(ADR_STATUS_META[detail.adr_status] || ADR_STATUS_META.proposed).tone}`}>
                        {(ADR_STATUS_META[detail.adr_status] || ADR_STATUS_META.proposed).label}
                      </span>
                    </span>
                  </div>
                  {detail.madr_context && (
                    <div className="adr-section">
                      <h4>背景</h4>
                      <p>{detail.madr_context}</p>
                    </div>
                  )}
                  {detail.madr_decision && (
                    <div className="adr-section">
                      <h4>决策</h4>
                      <p>{detail.madr_decision}</p>
                    </div>
                  )}
                  {detail.madr_consequences && (
                    <div className="adr-section">
                      <h4>后果</h4>
                      <p>{detail.madr_consequences}</p>
                    </div>
                  )}
                  {detail.superseded_by && (
                    <div className="adr-section">
                      <h4>被取代</h4>
                      <p>被 {detail.superseded_by} 取代</p>
                    </div>
                  )}
                </div>
              )}

              <div className="idea-detail__disc">
                <div className="idea-detail__disc-head">
                  <span>讨论会话（{detail.discussions?.length || 0}）</span>
                </div>
                <div className="idea-detail__newdisc">
                  <input className="inp" placeholder="开个会：讨论主题（如「这个想法怎么落地」）" value={discTopic}
                         onChange={e => setDiscTopic(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') newDiscussion() }} />
                  <button className="btn btn--primary" onClick={newDiscussion} disabled={!discTopic.trim()}>开会</button>
                </div>

                {(detail.discussions || []).map(d => (
                  <div key={d.id} className={`disc${d.status === 'closed' ? ' is-closed' : ''}`}>
                    <div className="disc__head">
                      <strong>{d.topic}</strong>
                      <span className="tag">{d.status === 'closed' ? '已结束' : '进行中'}</span>
                    </div>
                    {d.agent && <div className="disc__sub">发起：{d.agent} · {d.stage}</div>}
                    <div className="disc__msgs">
                      {(d.messages || []).map((m, idx) => (
                        <div key={idx} className={`msg msg--${m.role}`}>
                          <div className="msg__meta">{ROLE_LABEL[m.role] || m.author || m.role}</div>
                          <div className="msg__bubble">{m.content}</div>
                        </div>
                      ))}
                      {(!d.messages || d.messages.length === 0) && <div className="disc__empty">还没有消息。</div>}
                    </div>
                    {d.status !== 'closed' && (
                      <div className="disc__reply">
                        <input className="inp" placeholder="回复这个讨论…（agent 也会看到并参与）" value={msgDraft[d.id] || ''}
                               onChange={e => setMsgDraft({ ...msgDraft, [d.id]: e.target.value })}
                               onKeyDown={e => { if (e.key === 'Enter') reply(d.id) }} />
                        <button className="btn btn--primary" onClick={() => reply(d.id)} disabled={!(msgDraft[d.id] || '').trim()}>发送</button>
                      </div>
                    )}
                    {d.conclusions && <div className="disc__concl">结论：{d.conclusions}</div>}
                    {d.status !== 'closed' && (
                      <button className="btn btn--ghost" onClick={() => closeDisc(d)}>结束讨论</button>
                    )}
                  </div>
                ))}
                {(!detail.discussions || detail.discussions.length === 0) && (
                  <div className="ideas__empty">还没有讨论。开一个会，把想法拉上 agent 一起头脑风暴。</div>
                )}
              </div>

              <div className="idea-detail__hist">
                <div className="idea-detail__disc-head"><span>轨迹（{hist?.count || 0}）</span></div>
                {(hist?.items || []).length === 0 && <div className="ideas__empty">还没有轨迹记录。</div>}
                {hist && hist.items && hist.items.length > 0 && (
                  <div className="drawer__timeline">
                    {hist.items.map(h => (
                      <div key={h.id} className="hist-row">
                        <span className="hist-row__dot" aria-hidden="true" />
                        <div className="hist-row__body">
                          <div className="hist-row__head">
                            <b>{KIND_LABEL[h.kind] || h.kind}</b>
                            <span className="hist-row__at mono">{fmtDate(h.at)}</span>
                            {h.actor && <span className="tag">{h.actor}</span>}
                          </div>
                          <div className="hist-row__content">{h.content}</div>
                          {h.reasoning && <pre className="hist-row__payload mono">{h.reasoning}</pre>}
                        </div>
                      </div>
                    ))}
                    {hist.count > hist.items.length && (
                      <div className="hist-row__more">… 更早的记录请用 MCP taskhub_idea_history 查询</div>
                    )}
                  </div>
                )}
              </div>

              {breaking && (
                <div className="idea-detail__break">
                  <div className="idea-detail__disc-head"><span>拆解为任务（可填依赖 ref）</span></div>
                  {breakRows.map((r, idx) => (
                    <div key={idx} className="break-row">
                      <input className="inp break-row__ref" placeholder="ref" value={r.ref} readOnly />
                      <input className="inp" placeholder="任务标题" value={r.title}
                             onChange={e => setBreakRows(rows => rows.map((x, i) => i === idx ? { ...x, title: e.target.value } : x))} />
                      <input className="inp break-row__deps" placeholder="依赖(逗号分隔)" value={r.deps}
                             onChange={e => setBreakRows(rows => rows.map((x, i) => i === idx ? { ...x, deps: e.target.value } : x))} />
                    </div>
                  ))}
                  <div className="break-actions">
                    <button className="btn btn--ghost" onClick={addBreakRow}>+ 加一行</button>
                    <button className="btn btn--primary" onClick={submitBreakdown}
                            disabled={submitting || !breakRows.some(r => r.title.trim())}>
                      {submitting ? '拆解中…' : '提交拆解'}
                    </button>
                  </div>
                </div>
              )}

              {editing && (
                <div className="idea-detail__edit">
                  <div className="idea-detail__disc-head"><span>编辑需求</span></div>
                  <input className="inp" placeholder="标题" value={editForm.title}
                         onChange={e => setEditForm({ ...editForm, title: e.target.value })} />
                  <textarea className="inp" rows={3} placeholder="描述" value={editForm.description}
                            onChange={e => setEditForm({ ...editForm, description: e.target.value })} />
                  <input className="inp" placeholder="变更原因（建议填写）" value={editForm.reason}
                         onChange={e => setEditForm({ ...editForm, reason: e.target.value })} />
                  <div className="break-actions">
                    <button className="btn btn--ghost" onClick={() => setEditing(false)}>取消</button>
                    <button className="btn btn--primary" onClick={submitEdit}
                            disabled={!editForm.title.trim()}>保存</button>
                  </div>
                </div>
              )}

              {/* 演化为 ADR 模态框 */}
              {showEvolveModal && (
                <div className="idea-detail__edit">
                  <div className="idea-detail__disc-head"><span>演化为 ADR</span></div>
                  <textarea className="inp" rows={3} placeholder="背景/上下文" value={adrForm.madr_context}
                            onChange={e => setAdrForm({ ...adrForm, madr_context: e.target.value })} />
                  <textarea className="inp" rows={3} placeholder="决策内容" value={adrForm.madr_decision}
                            onChange={e => setAdrForm({ ...adrForm, madr_decision: e.target.value })} />
                  <textarea className="inp" rows={3} placeholder="后果（正面/负面）" value={adrForm.madr_consequences}
                            onChange={e => setAdrForm({ ...adrForm, madr_consequences: e.target.value })} />
                  <input className="inp" placeholder="演化原因" value={adrForm.reason}
                         onChange={e => setAdrForm({ ...adrForm, reason: e.target.value })} />
                  <div className="break-actions">
                    <button className="btn btn--ghost" onClick={() => setShowEvolveModal(false)}>取消</button>
                    <button className="btn btn--primary" onClick={evolveToAdr}>确认演化</button>
                  </div>
                </div>
              )}

              {/* ADR 状态操作模态框 */}
              {showAdrAction && (
                <div className="idea-detail__edit">
                  <div className="idea-detail__disc-head">
                    <span>
                      {adrAction.action === 'accept' && '接受 ADR'}
                      {adrAction.action === 'reject' && '拒绝 ADR'}
                      {adrAction.action === 'deprecate' && '废弃 ADR'}
                      {adrAction.action === 'supersede' && '取代 ADR'}
                    </span>
                  </div>
                  {adrAction.action === 'supersede' && (
                    <select className="inp" value={adrAction.replacement_id}
                            onChange={e => setAdrAction({ ...adrAction, replacement_id: e.target.value })}>
                      <option value="">选择新的 ADR</option>
                      {adrList.filter(a => a.id !== detail?.id).map(a => (
                        <option key={a.id} value={a.id}>{a.title} ({a.id})</option>
                      ))}
                    </select>
                  )}
                  <input className="inp" placeholder="原因" value={adrAction.reason}
                         onChange={e => setAdrAction({ ...adrAction, reason: e.target.value })} />
                  <div className="break-actions">
                    <button className="btn btn--ghost" onClick={() => setShowAdrAction(false)}>取消</button>
                    <button className="btn btn--primary" onClick={executeAdrAction}
                            disabled={adrAction.action === 'supersede' && !adrAction.replacement_id}>确认</button>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* ADR 原始 Markdown 弹层 */}
      {adrMd && (
        <div className="overlay" onClick={() => setAdrMd(null)}>
          <div className="modal adr-md-modal" role="dialog" aria-modal="true" aria-label="ADR 文档" onClick={e => e.stopPropagation()}>
            <div className="modal__head">
              <h3>ADR 文档 {adrMd.source === 'inline' && <span className="tag">未同步 · 即时渲染</span>}</h3>
              <button className="modal__close" onClick={() => setAdrMd(null)} aria-label="关闭">×</button>
            </div>
            {adrMd.path && <p className="adr-md-path">{adrMd.path}</p>}
            <div className="md adr-md-body" dangerouslySetInnerHTML={{ __html: marked(adrMd.content) }} />
          </div>
        </div>
      )}
    </div>
  )
}