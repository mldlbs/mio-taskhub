import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../api'
import { useOverlayStack } from '../lib/overlayStack'

const ACTIONS = [
  { id: 'confirm', label: '采纳', tone: 'ok' },
  { id: 'amend', label: '修改', tone: 'info' },
  { id: 'decide', label: '转决策', tone: 'accent', needInput: true },
  { id: 'test', label: '转 TC', tone: 'info' },
  { id: 'reject', label: '驳回', tone: 'danger', needInput: true },
  { id: 'defer', label: '暂缓', tone: 'warn', needInput: true },
]

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

const CLOSE_PRESETS = [
  { label: '通过·全部闭环', decision: 'approve', summary: '评审会逐条裁决完成，关键评审点全部闭环，遗留项已转行动项。' },
  { label: '通过·有遗留', decision: 'approve', summary: '评审会通过：主体评审点闭环，少量遗留已列行动项跟踪。' },
  { label: '退回·补材料', decision: 'reject', summary: '评审不通过：关键评审点证据不足，需补充材料后重开。' },
]

const REVIEW_TPL = {
  risks: ['评审点待补充证据'],
  divergences: '无重大分歧，逐条结论见评审点记录。',
  suggestions: '按逐条裁决结论推进。',
  decisions: ['采纳关键评审点', '遗留项转决策或行动项'],
  action_items: [{ id: 'AI-1', owner: 'tech', action: '补齐遗留评审点证据', due: new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10), status: 'pending', task_id: null }],
}

export default function ReviewMeetingPanel({ task, onClose }) {
  useOverlayStack(onClose)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [points, setPoints] = useState([])
  const [version, setVersion] = useState('')
  const [selected, setSelected] = useState({})
  const [meeting, setMeeting] = useState(null)
  const [draft, setDraft] = useState({})       // { [pointId]: { reason, decisionLabel } }
  const [pending, setPending] = useState(null)  // { id, action }
  const [expanded, setExpanded] = useState({})
  const [preset, setPreset] = useState(0)
  const [closeSummary, setCloseSummary] = useState(CLOSE_PRESETS[0].summary)

  const load = useCallback(async () => {
    if (!task?.id) return
    setLoading(true)
    try {
      const d = await api.reviewPoints(task.id, { size: 200 })
      setPoints(d.items || [])
      setVersion(d.items?.[0]?.source_doc?.version || '')
      const sel = {}
      for (const p of d.items || []) if (p.status !== 'candidate') sel[p.id] = true
      setSelected(sel)
      if (d.items?.length) setError(null)
    } catch (e) {
      console.error('[ReviewMeetingPanel] load failed:', e)
      setError(e.message || '加载评审点失败')
    } finally { setLoading(false) }
  }, [task?.id])

  useEffect(() => { load() }, [load])

  const stats = useMemo(() => {
    const pickedN = points.filter(p => selected[p.id]).length
    const undecidedN = points.filter(p => (p.status === 'shortlisted' || p.status === 'discussing') && selected[p.id]).length
    const decidedN = points.filter(p => TERMINAL.includes(p.status)).length
    return { picked: pickedN, undecided: undecidedN, decided: decidedN }
  }, [points, selected])

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

  const toggle = async (p) => {
    if (TERMINAL.includes(p.status)) return
    const next = !selected[p.id]
    setSelected(s => ({ ...s, [p.id]: next }))
    try {
      await api.reviewShortlist(p.id, next)
    } catch (e) {
      console.error('[ReviewMeetingPanel] shortlist failed:', e)
      setSelected(s => ({ ...s, [p.id]: !next }))
    }
  }

  const openMeeting = async () => {
    if (!stats.picked) { setError('先勾选至少 1 个评审点'); return }
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

  const decide = async (p, action) => {
    const d = draft[p.id] || {}
    const act = ACTIONS.find(a => a.id === action)
    const reason = (d.reason || '').trim()
    if (act.needInput && action !== 'decide' && reason.length < 8) {
      setError(`${act.label} 需填写理由（至少 8 字）`)
      return
    }
    const body = { action, reviewer: 'user' }
    if (reason) body.rationale = reason
    if (action === 'decide') {
      body.options = [{ label: (d.decisionLabel || '').trim() || reason || '采纳建议方案', note: reason }]
    }
    setBusy(true); setError(null)
    try {
      await api.reviewDecide(p.id, body)
      setDraft(x => ({ ...x, [p.id]: {} }))
      setPending(null)
      await load()
    } catch (e) {
      console.error('[ReviewMeetingPanel] decide failed:', e)
      setError(e.message || '裁决失败')
    } finally { setBusy(false) }
  }

  const closeMeeting = async () => {
    setBusy(true); setError(null)
    try {
      const p = CLOSE_PRESETS[preset]
      await api.reviewMeetingClose(meeting.id, {
        decision: p.decision, summary: closeSummary, review: REVIEW_TPL,
      })
      const tr = await api.reviewTrace(meeting.id)
      console.info('[ReviewMeetingPanel] trace:', tr)
      setMeeting(null)
      await load()
    } catch (e) {
      console.error('[ReviewMeetingPanel] close failed:', e)
      setError(e.message || '关会失败')
    } finally { setBusy(false) }
  }

  const pickPreset = (i) => { setPreset(i); setCloseSummary(CLOSE_PRESETS[i].summary) }

  return (
    <div className="overlay rm-overlay" role="dialog" aria-modal="true" aria-label="研发评审会">
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
            <span className="rm-stat"><b>{points.length}</b>候选</span>
            <span className="rm-stat"><b>{stats.picked}</b>入会</span>
            <span className="rm-stat is-pending"><b>{stats.undecided}</b>未决</span>
          </div>
          <button className="rm-close" onClick={onClose} aria-label="关闭">×</button>
        </header>

        {version && (
          <div className="rm-meta">
            <span>文档版本</span><code>{version}</code>
            <span className="rm-meta__spacer" />
            <span>逐条裁决后由人推进文档状态</span>
          </div>
        )}

        {error && <div className="rm-error" role="alert">{error}</div>}

        <div className="rm-toolbar">
          {!points.length && (
            <button className="btn btn--primary" disabled={busy} onClick={extract}>
              {busy ? '提取中…' : '从文档提取评审点'}
            </button>
          )}
          {points.length > 0 && !meeting && (
            <>
              <button className="btn btn--primary" disabled={busy || !stats.picked} onClick={openMeeting}>
                {busy ? '开会中…' : `开会 · 已勾 ${stats.picked} 点`}
              </button>
              <button className="btn btn--ghost" disabled={busy} onClick={extract}>重新提取</button>
              <span className="rm-toolbar__hint">高风险点已自动入会，勾选要评的点</span>
            </>
          )}
          {meeting && (
            <>
              <span className="rm-toolbar__meeting">会议 <code>{meeting.id}</code></span>
              <span className="rm-progress">
                裁决进度 <b>{stats.decided}/{stats.picked}</b>
                <span className="rm-progress__bar"><i style={{ width: `${stats.picked ? Math.round(stats.decided / stats.picked * 100) : 0}%` }} /></span>
              </span>
            </>
          )}
        </div>

        {meeting && (
          <div className="rm-closebar">
            <span className="rm-closebar__label">关会结论</span>
            <div className="rm-presets">
              {CLOSE_PRESETS.map((p, i) => (
                <button key={i} className={`rm-preset${preset === i ? ' is-active' : ''}`} onClick={() => pickPreset(i)}>{p.label}</button>
              ))}
            </div>
            <input className="rm-closebar__input" value={closeSummary} onChange={e => setCloseSummary(e.target.value)} aria-label="关会摘要" />
            <button className="btn btn--primary" disabled={busy || stats.undecided > 0} onClick={closeMeeting}>
              {busy ? '关会中…' : '关会'}
            </button>
            {stats.undecided > 0 && <span className="rm-closebar__warn">还有 {stats.undecided} 点未裁决，关会需全部终态</span>}
          </div>
        )}

        <div className="rm-list">
          {loading && <div className="rm-empty">加载中…</div>}
          {!loading && !points.length && <div className="rm-empty">尚无评审点。点「从文档提取评审点」开始。</div>}
          {points.map((p, idx) => {
            const terminal = TERMINAL.includes(p.status)
            const active = meeting && !terminal
            const isPending = pending?.id === p.id
            return (
              <div key={p.id} className={`rm-point is-${p.status}${expanded[p.id] ? ' is-open' : ''}`}>
                <div className="rm-point__side">
                  <input type="checkbox" checked={!!selected[p.id]} disabled={terminal || busy} onChange={() => toggle(p)} aria-label={`选择 ${p.id}`} />
                  <span className="rm-point__seq mono">{String(idx + 1).padStart(2, '0')}</span>
                </div>
                <div className="rm-point__main">
                  <div className="rm-point__top">
                    <span className="rm-tag rm-tag--kind">{KIND_LABEL[p.kind] || p.kind}</span>
                    <span className={`rm-tag rm-tag--risk-${p.risk_level}`}>风险 {RISK_LABEL[p.risk_level] || p.risk_level}</span>
                    <span className={`rm-tag rm-tag--status is-${p.status}`}>{STATUS_LABEL[p.status] || p.status}</span>
                    <span className="rm-point__src">{p.source_doc?.kind}</span>
                    <span className="rm-point__id mono">{p.id}</span>
                  </div>
                  <div className="rm-point__claim">{p.claim}</div>
                  <button className="rm-point__fold" onClick={() => setExpanded(x => ({ ...x, [p.id]: !x[p.id] }))} aria-expanded={!!expanded[p.id]}>
                    {expanded[p.id] ? '收起原文' : '查看原文'} <span className="rm-point__anchor">{p.anchor}</span>
                  </button>
                  {expanded[p.id] && <div className="rm-point__quote">{p.quote}</div>}

                  {active && !isPending && (
                    <div className="rm-acts">
                      {ACTIONS.map(a => (
                        <button key={a.id} className={`rm-act rm-act--${a.tone}`} disabled={busy} onClick={() => (
                          a.needInput ? setPending({ id: p.id, action: a.id }) : decide(p, a.id)
                        )}>{a.label}</button>
                      ))}
                    </div>
                  )}
                  {active && isPending && (
                    <div className="rm-pending">
                      <span className="rm-pending__label">{ACTIONS.find(a => a.id === pending.action)?.label}</span>
                      {pending.action === 'decide' && (
                        <input placeholder="方案名" value={draft[p.id]?.decisionLabel || ''}
                          onChange={e => setDraft(x => ({ ...x, [p.id]: { ...(x[p.id] || {}), decisionLabel: e.target.value } }))} />
                      )}
                      <input className="rm-pending__reason" placeholder="理由（驳回/暂缓至少 8 字）" value={draft[p.id]?.reason || ''}
                        onChange={e => setDraft(x => ({ ...x, [p.id]: { ...(x[p.id] || {}), reason: e.target.value } }))} />
                      <button className="btn btn--sm btn--primary" disabled={busy} onClick={() => decide(p, pending.action)}>确认</button>
                      <button className="btn btn--sm btn--ghost" onClick={() => setPending(null)}>取消</button>
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
    </div>
  )
}
