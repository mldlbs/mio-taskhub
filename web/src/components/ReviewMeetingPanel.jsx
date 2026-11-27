import { useState, useEffect, useCallback, useMemo } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api'
import { useOverlayStack } from '../lib/overlayStack'

const KIND_LABEL = {
  section_gap: '章节缺口', fr_contract: 'FR 契约', api_contract: 'API 契约',
  data_rule: '数据规则', risk_assumption: '风险假设', decision_point: '决策点',
  premise: '前提', nfr: '非功能', acceptance: '验收', error_contract: '错误契约',
  idempotency: '幂等', compat: '兼容', scope_boundary: '边界', functional: '功能', risk: '风险',
}
const RISK_LABEL = { high: '高', medium: '中', low: '低' }
const STATUS_LABEL = {
  candidate: '候选', shortlisted: '待评', discussing: '评审中',
  resolved: '已采纳', rejected: '已驳回', deferred: '已暂缓',
}
const TERMINAL = ['resolved', 'rejected', 'deferred']

// 把文档切片改写成"要判断什么"——用户看到的是问题，不是原文表格行。
function askFor(p) {
  const c = (p.claim || '').trim()
  const tail = c.length > 72 ? c.slice(0, 72) + '…' : c
  switch (p.kind) {
    case 'nfr': return `这条指标能作为验收标准吗？「${tail}」`
    case 'risk_assumption': return `这个风险假设成立吗？「${tail}」`
    case 'error_contract': return `错误码契约与调用方对齐了吗？「${tail}」`
    case 'fr_contract': return `这条需求的验收口径写清楚了吗？「${tail}」`
    case 'decision_point': return `这件事需要现在拍板吗？「${tail}」`
    case 'premise': return `这个前提已经验证过吗？「${tail}」`
    case 'api_contract': return `接口契约需要调整吗？「${tail}」`
    case 'acceptance': return `验收标准可执行吗？「${tail}」`
    case 'idempotency': return `幂等与重试边界清楚吗？「${tail}」`
    case 'compat': return `兼容性影响评估过了吗？「${tail}」`
    case 'data_rule': return `数据规则定义清楚了吗？「${tail}」`
    case 'section_gap': return `这处内容需要补充还是删掉？「${tail}」`
    default: return `这条内容你认可吗？「${tail}」`
  }
}

const CLOSE_PRESETS = [
  { label: '通过·全部闭环', decision: 'approve', summary: '评审会逐条裁决完成，关键评审点全部闭环，遗留项已转行动项。' },
  { label: '通过·有遗留', decision: 'approve', summary: '评审会通过：主体评审点闭环，少量遗留已列行动项跟踪。' },
  { label: '退回·补材料', decision: 'reject', summary: '评审不通过：关键评审点证据不足，需补充材料后重开。' },
]

function buildReview(points) {
  const decided = points.filter(p => TERMINAL.includes(p.status))
  const adopted = decided.filter(p => p.status === 'resolved')
  const rejected = decided.filter(p => p.status === 'rejected')
  const deferred = decided.filter(p => p.status === 'deferred')
  const due = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10)
  const short = (p) => `${p.id} ${p.claim}`.slice(0, 80)
  const decisions = adopted.slice(0, 8).map(p =>
    `${short(p)}｜${p.decision_id ? '转决策 ' + p.decision_id : ((p.tc_refs || []).length ? '转 TC' : '认可')}`)
  while (decisions.length < 2) decisions.push(decisions.length === 0
    ? '本轮明确结论不足 2 项，需补裁决后再关会。'
    : '遗留项已转行动项跟踪，下次评审复核。')
  const risks = rejected.concat(deferred).slice(0, 8).map(p =>
    `${p.status === 'rejected' ? '已驳回' : '暂缓'} ${short(p)}｜${p.reject_reason || '理由见评审点记录'}`)
  if (!risks.length) risks.push('未标记驳回/暂缓，风险由已认可点承接。')
  const actionItems = deferred.concat(adopted.filter(p => (p.tc_refs || []).length)).slice(0, 8)
    .map((p, i) => ({
      id: `AI-${i + 1}`,
      owner: p.status === 'deferred' ? 'product' : 'tech',
      action: p.status === 'deferred' ? `跟进暂缓点 ${short(p)}` : `落地转 TC ${short(p)}`,
      due, status: 'pending', task_id: null,
    }))
  if (!actionItems.length) actionItems.push({
    id: 'AI-1', owner: 'tech', action: `按评审结论推进 ${adopted.length} 项已认可内容`,
    due, status: 'pending', task_id: null,
  })
  return {
    risks,
    divergences: rejected.length || deferred.length
      ? `${rejected.length} 点已驳回、${deferred.length} 点暂缓，分歧与理由见各点记录。`
      : '无重大分歧：逐条结论一致，理由见评审点记录。',
    suggestions: `共裁决 ${decided.length} 点：认可 ${adopted.length}、驳回 ${rejected.length}、暂缓 ${deferred.length}；按结论推进文档与实现。`,
    decisions, action_items: actionItems,
  }
}

export default function ReviewMeetingPanel({ task, onClose }) {
  useOverlayStack(onClose)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [done, setDone] = useState(null)
  const [points, setPoints] = useState([])
  const [meeting, setMeeting] = useState(null)
  const [draft, setDraft] = useState({})
  const [submenu, setSubmenu] = useState(null)
  const [expanded, setExpanded] = useState({})
  const [view, setView] = useState('active')
  const [riskFilter, setRiskFilter] = useState('')
  const [preset, setPreset] = useState(0)
  const [closeSummary, setCloseSummary] = useState(CLOSE_PRESETS[0].summary)

  const load = useCallback(async () => {
    if (!task?.id) return
    setLoading(true)
    try {
      const all = []
      let page = 1
      for (;;) {
        const d = await api.reviewPoints(task.id, { page, size: 200, sort: 'risk_level', order: 'desc' })
        all.push(...(d.items || []))
        if (all.length >= (d.total || 0) || !(d.items || []).length || page >= 20) break
        page++
      }
      setPoints(all)
      if (all.length) setError(null)
      const ds = await api.listDiscussions('task', task.id)
      const active = (ds.discussions || []).find(d => d.mode === 'review' && d.status === 'open')
      setMeeting(active ? { id: active.id, point_count: all.filter(p => p.meeting_id === active.id).length } : null)
    } catch (e) {
      console.error('[ReviewMeetingPanel] load failed:', e)
      setError(e.message || '加载评审点失败')
    } finally { setLoading(false) }
  }, [task?.id])

  useEffect(() => { load() }, [load])

  const stats = useMemo(() => {
    const inMeeting = points.filter(p => meeting && p.meeting_id === meeting.id)
    const undecided = inMeeting.filter(p => !TERMINAL.includes(p.status))
    return {
      total: points.length,
      inMeeting: inMeeting.length,
      decided: inMeeting.filter(p => TERMINAL.includes(p.status)).length,
      undecided: undecided.length,
    }
  }, [points, meeting])

  const visible = useMemo(() => {
    return points.filter(p => {
      if (view === 'active' && p.status === 'candidate') return false
      if (view === 'todo' && TERMINAL.includes(p.status)) return false
      if (riskFilter && p.risk_level !== riskFilter) return false
      return true
    })
  }, [points, view, riskFilter])

  const extract = async () => {
    setBusy(true); setError(null)
    try {
      await api.reviewExtract(task.id, { include_llm: false })
      await load()
    } catch (e) {
      console.error('[ReviewMeetingPanel] extract failed:', e)
      setError(e.message || '提取失败')
    } finally { setBusy(false) }
  }

  const openMeeting = async () => {
    setBusy(true); setError(null)
    try {
      const d = await api.reviewMeetingOpen(task.id, ['product', 'tech'])
      setMeeting({ id: d.discussion_id, point_count: d.point_count })
      await load()
    } catch (e) {
      console.error('[ReviewMeetingPanel] open failed:', e)
      setError(e.message || '开会失败')
    } finally { setBusy(false) }
  }

  const decide = async (p, action, reasonOverride) => {
    const reason = (reasonOverride ?? (draft[p.id]?.reason ?? '')).trim()
    if ((action === 'reject' || action === 'defer') && reason.length < 8) {
      setError(`${action === 'reject' ? '驳回' : '暂缓'}需填至少 8 字理由`)
      return
    }
    const body = { action, reviewer: 'user' }
    if (reason) body.rationale = reason
    if (action === 'decide') {
      body.options = [{ label: (draft[p.id]?.decisionLabel || '').trim() || reason || '采纳建议方案', note: reason }]
    }
    setBusy(true); setError(null)
    try {
      await api.reviewDecide(p.id, body)
      setDraft(x => ({ ...x, [p.id]: {} }))
      setSubmenu(null)
      await load()
    } catch (e) {
      console.error('[ReviewMeetingPanel] decide failed:', e)
      setError(e.message || '裁决失败')
    } finally { setBusy(false) }
  }

  // 批量认可：一次把本会未裁决点全部标记认可，省去逐条点击。
  const acceptAll = async () => {
    const todo = points.filter(p => meeting && p.meeting_id === meeting.id && !TERMINAL.includes(p.status))
    if (!todo.length) return
    setBusy(true); setError(null)
    let ok = 0
    for (const p of todo) {
      try {
        await api.reviewDecide(p.id, { action: 'confirm', reviewer: 'user' })
        ok++
      } catch (e) {
        console.error('[ReviewMeetingPanel] batch confirm failed:', p.id, e)
      }
    }
    setBusy(false)
    setDone(`已批量认可 ${ok} 项${ok < todo.length ? `，${todo.length - ok} 项失败` : ''}`)
    await load()
  }

  const closeMeeting = async () => {
    setBusy(true); setError(null)
    try {
      const pendingIds = points
        .filter(p => meeting && p.meeting_id === meeting.id && !TERMINAL.includes(p.status))
        .map(p => p.id)
      for (const pid of pendingIds) {
        await api.reviewDecide(pid, {
          action: 'defer', reviewer: 'user', rationale: '本轮未逐条评审，暂缓至下次评审',
        })
      }
      const reviewed = points.map(p => pendingIds.includes(p.id)
        ? { ...p, status: 'deferred', reject_reason: '本轮未逐条评审，暂缓至下次评审' } : p)
      const p = CLOSE_PRESETS[preset]
      await api.reviewMeetingClose(meeting.id, {
        decision: p.decision, summary: closeSummary,
        reviewer: 'user', review: buildReview(reviewed),
      })
      setDone(`评审会已关闭：${p.label}。纪要、行动项与决策已写入任务。`)
      setMeeting(null)
      await load()
      // 关会后留 1.6s 让用户看到结果，再回到任务详情，
      // 避免面板停在"开始评审会"阶段卡片 + 一堆已裁决点造成困惑。
      setTimeout(onClose, 1600)
    } catch (e) {
      console.error('[ReviewMeetingPanel] close failed:', e)
      setError(e.message || '关会失败')
    } finally { setBusy(false) }
  }

  // 流程：没有点就提取 → 有候选但没会就开会 → 有会就裁决/关会
  const noPoints = !loading && !points.length
  const hasCandidates = points.length > 0 && !meeting && stats.inMeeting === 0

  return createPortal(
    <div className="overlay rm-overlay" role="dialog" aria-modal="true" aria-label="研发评审会" onClick={e => e.stopPropagation()}>
      <div className="rm-panel">
        <header className="rm-head">
          <div className="rm-head__lead">
            <span className="rm-head__mark">评审</span>
            <div className="rm-head__titles">
              <h3>研发评审会</h3>
              <p className="rm-head__sub" title={task.title}>{task.title}</p>
            </div>
          </div>
          <div className="rm-head__stats">
            <span className="rm-stat"><b>{stats.inMeeting}</b>入会</span>
            <span className="rm-stat"><b>{stats.decided}</b>已决</span>
            <span className="rm-stat is-pending"><b>{stats.undecided}</b>未决</span>
          </div>
          <button className="rm-close" onClick={onClose} aria-label="关闭">×</button>
        </header>

        <div className="rm-flow">
          <span className={`rm-flow__step${meeting ? ' is-done' : ''}`}>1 提取</span>
          <span className="rm-flow__line" />
          <span className={`rm-flow__step${meeting ? ' is-done' : ''}`}>2 开会</span>
          <span className="rm-flow__line" />
          <span className={`rm-flow__step${meeting ? ' is-current' : ''}`}>3 逐条裁决</span>
          <span className="rm-flow__line" />
          <span className="rm-flow__step">4 关会写纪要</span>
          <span className="rm-flow__hint">认可 / 要改 / 跳过，最后一键关会</span>
        </div>

        {done && <div className="rm-done" role="status">{done}</div>}
        {error && <div className="rm-error" role="alert">{error}</div>}

        {noPoints && (
          <div className="rm-stage">
            <p className="rm-stage__title">先从文档提取评审点</p>
            <p className="rm-stage__desc">按 requirement / spec / api 逐条铺点，高风险自动入会。</p>
            <button className="btn btn--primary" disabled={busy} onClick={extract}>
              {busy ? '提取中…' : '提取评审点'}
            </button>
          </div>
        )}

        {hasCandidates && (
          <div className="rm-stage">
            <p className="rm-stage__title">已提取 {points.length} 个候选点</p>
            <p className="rm-stage__desc">自动选出高风险点入会；开会后每条用「认可 / 要改 / 跳过」处理。</p>
            <button className="btn btn--primary" disabled={busy} onClick={openMeeting}>
              {busy ? '开会中…' : '开始评审会'}
            </button>
            <button className="btn btn--ghost" disabled={busy} onClick={extract}>重新提取</button>
          </div>
        )}

        {meeting && (
          <>
            <div className="rm-batch">
              <span className="rm-batch__progress">
                裁决进度 <b>{stats.decided}/{stats.inMeeting}</b>
                <span className="rm-progress__bar"><i style={{ width: `${stats.inMeeting ? Math.round(stats.decided / stats.inMeeting * 100) : 0}%` }} /></span>
              </span>
              <button className="btn btn--primary" disabled={busy || !stats.undecided} onClick={acceptAll}>
                {busy ? '处理中…' : `全部认可 · ${stats.undecided}`}
              </button>
              <span className="rm-batch__id">会议 <code>{meeting.id}</code></span>
            </div>

            <div className="rm-filters">
              <div className="rm-filters__views">
                <button className={`rm-filter-tab${view === 'todo' ? ' is-active' : ''}`} onClick={() => setView('todo')}>待处理 {stats.undecided}</button>
                <button className={`rm-filter-tab${view === 'active' ? ' is-active' : ''}`} onClick={() => setView('active')}>入会 {stats.inMeeting}</button>
                <button className={`rm-filter-tab${view === 'all' ? ' is-active' : ''}`} onClick={() => setView('all')}>全部 {stats.total}</button>
              </div>
              <select className="rm-filters__risk" value={riskFilter} onChange={e => setRiskFilter(e.target.value)} aria-label="按风险筛选">
                <option value="">全部风险</option>
                <option value="high">高风险</option>
                <option value="medium">中风险</option>
                <option value="low">低风险</option>
              </select>
              <span className="rm-filters__count">{visible.length} 条</span>
            </div>
          </>
        )}

        {meeting && visible.length > 0 && (
          <div className="rm-closebar">
            <span className="rm-closebar__label">关会结论</span>
            <div className="rm-presets">
              {CLOSE_PRESETS.map((p, i) => (
                <button key={i} className={`rm-preset${preset === i ? ' is-active' : ''}`} onClick={() => { setPreset(i); setCloseSummary(p.summary) }}>{p.label}</button>
              ))}
            </div>
            <input className="rm-closebar__input" value={closeSummary} onChange={e => setCloseSummary(e.target.value)} aria-label="关会摘要" />
            <button className="btn btn--primary" disabled={busy} onClick={closeMeeting}>
              {busy ? '关会中…' : '一键关会'}
            </button>
            {stats.undecided > 0 && <span className="rm-closebar__warn">剩 {stats.undecided} 点未处理，关会时自动暂缓并转行动项</span>}
          </div>
        )}

        <div className="rm-list">
          {loading && <div className="rm-empty">加载中…</div>}
          {!loading && points.length > 0 && !visible.length && <div className="rm-empty">当前筛选下没有评审点。</div>}
          {visible.map((p, idx) => {
            const terminal = TERMINAL.includes(p.status)
            const active = meeting && !terminal
            const isSub = submenu?.id === p.id
            return (
              <div key={p.id} className={`rm-point is-${p.status}${expanded[p.id] ? ' is-open' : ''}`}>
                <div className="rm-point__seq mono">{String(idx + 1).padStart(2, '0')}</div>
                <div className="rm-point__main">
                  <div className="rm-point__top">
                    <span className="rm-tag rm-tag--kind">{KIND_LABEL[p.kind] || p.kind}</span>
                    <span className={`rm-tag rm-tag--risk-${p.risk_level}`}>风险 {RISK_LABEL[p.risk_level] || p.risk_level}</span>
                    <span className={`rm-tag rm-tag--status is-${p.status}`}>{STATUS_LABEL[p.status] || p.status}</span>
                    <span className="rm-point__src">{p.source_doc?.kind} · {p.anchor}</span>
                  </div>
                  <div className="rm-point__ask">{askFor(p)}</div>
                  <button className="rm-point__fold" onClick={() => setExpanded(x => ({ ...x, [p.id]: !x[p.id] }))} aria-expanded={!!expanded[p.id]}>
                    {expanded[p.id] ? '收起原文' : '查看原文'}
                  </button>
                  {expanded[p.id] && <div className="rm-point__quote">{p.quote}</div>}

                  {active && !isSub && (
                    <div className="rm-acts">
                      <button className="rm-act rm-act--ok" disabled={busy} onClick={() => decide(p, 'confirm')}>认可</button>
                      <button className="rm-act rm-act--accent" disabled={busy} onClick={() => setSubmenu({ id: p.id })}>要改 ▾</button>
                      <button className="rm-act rm-act--warn" disabled={busy} onClick={() => decide(p, 'defer', '本轮跳过，留待下次评审')}>跳过</button>
                    </div>
                  )}
                  {active && isSub && (
                    <div className="rm-sub">
                      <div className="rm-sub__row">
                        <button className="rm-act rm-act--info" disabled={busy} onClick={() => decide(p, 'amend')}>补充说明</button>
                        <button className="rm-act rm-act--accent" disabled={busy} onClick={() => decide(p, 'decide')}>转决策</button>
                        <button className="rm-act rm-act--info" disabled={busy} onClick={() => decide(p, 'test')}>转测试项</button>
                        <button className="rm-act rm-act--danger" disabled={busy} onClick={() => decide(p, 'reject')}>驳回</button>
                        <button className="rm-act rm-act--plain" onClick={() => setSubmenu(null)}>收起</button>
                      </div>
                      <input className="rm-sub__reason" placeholder="理由（驳回至少 8 字）" value={draft[p.id]?.reason || ''}
                        onChange={e => setDraft(x => ({ ...x, [p.id]: { ...(x[p.id] || {}), reason: e.target.value } }))} />
                      {draft[p.id]?.reason && <span className="rm-sub__hint">填好理由再点上方动作</span>}
                    </div>
                  )}
                  {terminal && (
                    <div className="rm-verdict">
                      <span className={`rm-verdict__badge is-${p.status}`}>{STATUS_LABEL[p.status]}</span>
                      {p.reject_reason && <span className="rm-verdict__reason" title={p.reject_reason}>{p.reject_reason}</span>}
                      {p.decision_id && <code className="rm-verdict__decision">{p.decision_id}</code>}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      </div>
    </div>,
    document.body
  )
}
