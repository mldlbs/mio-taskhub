import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import { useOverlayStack } from '../lib/overlayStack'

const ACTIONS = [
  { id: 'confirm', label: '采纳', needsReason: false },
  { id: 'amend', label: '修改', needsReason: false },
  { id: 'reject', label: '驳回', needsReason: true },
  { id: 'decide', label: '转决策', needsReason: false },
  { id: 'test', label: '转 TC', needsReason: false },
  { id: 'defer', label: '暂缓', needsReason: true },
]

const KIND_LABEL = {
  section_gap: '章节缺口', fr_contract: 'FR 契约', api_contract: 'API 契约',
  data_rule: '数据规则', risk_assumption: '风险假设', decision_point: '决策点',
  premise: '前提', nfr: '非功能', acceptance: '验收', error_contract: '错误契约',
  idempotency: '幂等', compat: '兼容', scope_boundary: '边界', functional: '功能', risk: '风险',
}

const CLOSE_PRESETS = [
  { label: '通过·逐条裁决完成', decision: 'approve', summary: '评审会逐条裁决完成，关键评审点全部闭环，遗留项已转行动项。' },
  { label: '通过·有遗留待补', decision: 'approve', summary: '评审会通过：主体评审点闭环，少量遗留已列行动项跟踪。' },
  { label: '退回·需补充材料', decision: 'reject', summary: '评审不通过：关键评审点证据不足，需补充材料后重开。' },
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
  const [draft, setDraft] = useState({})
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
      for (const p of d.items || []) {
        if (p.status !== 'candidate') sel[p.id] = true
      }
      setSelected(sel)
      if (d.items?.length) setError(null)
    } catch (e) {
      console.error('[ReviewMeetingPanel] load failed:', e)
      setError(e.message || '加载评审点失败')
    } finally {
      setLoading(false)
    }
  }, [task?.id])

  useEffect(() => { load() }, [load])

  const extract = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.reviewExtract(task.id, { include_llm: false })
      await load()
    } catch (e) {
      console.error('[ReviewMeetingPanel] extract failed:', e)
      setError(e.message || '提取失败')
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (p) => {
    const next = !selected[p.id]
    setSelected(s => ({ ...s, [p.id]: next }))
    if (p.status === 'candidate' || p.status === 'shortlisted') {
      try {
        await api.reviewShortlist(p.id, next)
      } catch (e) {
        console.error('[ReviewMeetingPanel] shortlist failed:', e)
        setSelected(s => ({ ...s, [p.id]: !next }))
      }
    }
  }

  const openMeeting = async () => {
    const ids = points.filter(p => selected[p.id]).map(p => p.id)
    if (!ids.length) { setError('先勾选至少 1 个评审点'); return }
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
    const needsReason = ACTIONS.find(a => a.id === action)?.needsReason
    if (needsReason && !d.reason?.trim()) {
      setError(`${ACTIONS.find(a => a.id === action).label} 需填写理由（至少 8 字）`)
      return
    }
    setBusy(true); setError(null)
    const body = { action, reviewer: 'user' }
    if (needsReason) body.rationale = d.reason.trim()
    if (action === 'decide') {
      body.options = [{ label: d.decisionLabel?.trim() || '采纳建议方案', note: d.reason || '' }]
    }
    try {
      await api.reviewDecide(p.id, body)
      setDraft(x => ({ ...x, [p.id]: {} }))
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
        decision: p.decision,
        summary: closeSummary,
        review: REVIEW_TPL,
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

  const picked = points.filter(p => selected[p.id]).length
  const undecided = points.filter(p => (p.status === 'shortlisted' || p.status === 'discussing') && selected[p.id]).length

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="研发评审会">
      <div className="review-meeting">
        <div className="review-meeting__head">
          <div>
            <h3>研发评审会</h3>
            <p className="review-meeting__sub">{task.title}｜{points.length} 个候选点｜勾选 {picked} 点{version && <>｜版本 <code>{version.slice(7, 15)}</code></>}</p>
          </div>
          <button className="btn btn--ghost" onClick={onClose} aria-label="关闭">✕</button>
        </div>

        {error && <div className="review-meeting__error" role="alert">{error}</div>}

        <div className="review-meeting__bar">
          {!points.length && (
            <button className="btn btn--primary" disabled={busy} onClick={extract}>
              {busy ? '提取中…' : '从文档提取评审点'}
            </button>
          )}
          {points.length > 0 && !meeting && (
            <button className="btn btn--primary" disabled={busy || !picked} onClick={openMeeting}>
              {busy ? '开会中…' : `开会（已勾 ${picked} 点）`}
            </button>
          )}
          {meeting && (
            <>
              <span className="review-meeting__meeting">会议 {meeting.id}｜未决 {undecided} 点</span>
              <select value={preset} onChange={e => { const i = +e.target.value; setPreset(i); setCloseSummary(CLOSE_PRESETS[i].summary) }}>
                {CLOSE_PRESETS.map((p, i) => <option key={i} value={i}>{p.label}</option>)}
              </select>
              <input value={closeSummary} onChange={e => setCloseSummary(e.target.value)} aria-label="关会摘要" />
              <button className="btn btn--primary" disabled={busy || undecided > 0} onClick={closeMeeting}>
                {busy ? '关会中…' : '关会'}
              </button>
            </>
          )}
          {points.length > 0 && <button className="btn btn--ghost" disabled={busy} onClick={extract}>重新提取</button>}
        </div>

        {loading ? <div className="review-meeting__empty">加载中…</div> : (
          <div className="review-meeting__list">
            {points.length === 0 && <div className="review-meeting__empty">尚无评审点。点「从文档提取评审点」开始。</div>}
            {points.map(p => (
              <div key={p.id} className={`review-point review-point--${p.status}`}>
                <label className="review-point__pick">
                  <input type="checkbox" checked={!!selected[p.id]} disabled={p.status === 'resolved' || p.status === 'rejected' || p.status === 'deferred'} onChange={() => toggle(p)} />
                  <span className="review-point__id mono">{p.id}</span>
                </label>
                <div className="review-point__body">
                  <div className="review-point__meta">
                    <span className="review-point__kind">{KIND_LABEL[p.kind] || p.kind}</span>
                    <span className={`review-point__risk review-point__risk--${p.risk_level}`}>{p.risk_level}</span>
                    <span className="review-point__status">{p.status}</span>
                    <span className="review-point__src">{p.source_doc?.kind}</span>
                  </div>
                  <div className="review-point__claim">{p.claim}</div>
                  <div className="review-point__anchor">{p.anchor}</div>
                  <div className="review-point__quote">{p.quote}</div>
                  {meeting && (p.status === 'discussing' || p.status === 'shortlisted') && (
                    <div className="review-point__actions">
                      {ACTIONS.map(a => (
                        <button key={a.id} className="btn btn--tiny" disabled={busy} onClick={() => decide(p, a.id)}>{a.label}</button>
                      ))}
                      <input
                        placeholder="理由（驳回/暂缓必填 ≥8 字）"
                        value={draft[p.id]?.reason || ''}
                        onChange={e => setDraft(x => ({ ...x, [p.id]: { ...(x[p.id] || {}), reason: e.target.value } }))}
                      />
                      <input
                        placeholder="转决策时的方案名"
                        value={draft[p.id]?.decisionLabel || ''}
                        onChange={e => setDraft(x => ({ ...x, [p.id]: { ...(x[p.id] || {}), decisionLabel: e.target.value } }))}
                      />
                    </div>
                  )}
                  {p.decision_id && <div className="review-point__decision">已转决策 <code>{p.decision_id}</code></div>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
