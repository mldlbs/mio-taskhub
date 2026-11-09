// 决策面板「待我处理」（E7 结论产品化，2026-11-04）
//
// E7 实验结论：用户不是不想决策，是没人告诉他有决策在等他。本组件把
// E7 手工清单固化成常驻入口——三个队列，每条都写明「你要做什么」：
//   A 待决策：依赖阻塞（前置取消/失败，任务卡死）+ open 决策（FR-03）
//   B 待你验收：completed + review 阶段（Run 做完等你采纳）
//   C 失败待处置：重试 / 拆小重做 / 关掉
//
// 时间分层（2026-11-04 二轮，解决「积压淹没新事 + 告警疲劳」）：
//  - 验收/失败队列按 7 天切两段：近 7 天直接展示；更早的折叠成
//    一行「历史积压 N 条」，点开才看——chip 只数近 7 天的活跃数，
//    陈旧积压归并成一个灰色「积压」chip，不再冒充今天的待办。
//  - 决策队列不切时间（决策拖 30 天也还是必须裁决的事）。
//
// 设计约束：
//  - 机器噪声恒过滤（isNoiseTask），不受看板「显示机器任务」开关影响。
//  - 折叠态只是一条 24px 的细条 + chips；不抢注意力。
//  - 任务行点开详情在详情面板处置；决策行点开裁决对话框本组件内闭环。
import { useState, useEffect, useMemo, useCallback } from 'react'
import { api } from '../api'
import { isNoiseTask } from '../noise'
import { fmtAgo } from '../constants'

const MAX_ROWS = 8
const STALE_DAYS = 7
// 依赖阻塞告警文本里的任务 id：「任务「xxx」（d0390a04）依赖阻塞…」
const TID_RE = /（([0-9a-f]{8})）/

function daysAgo(ts) {
  if (!ts) return Infinity
  const t = Date.parse(ts)
  if (Number.isNaN(t)) return Infinity
  return (Date.now() - t) / 86400000
}

export default function DecisionInbox({ allTasks, onOpenTask }) {
  const [open, setOpen] = useState(false)
  const [depAlerts, setDepAlerts] = useState([])
  const [decisions, setDecisions] = useState([])
  const [showStaleReview, setShowStaleReview] = useState(false)
  const [showStaleFailed, setShowStaleFailed] = useState(false)
  // 裁决对话框状态：当前决策 + 提交中/错误
  const [resolving, setResolving] = useState(null) // decision 对象
  const [choice, setChoice] = useState('')
  const [freeChoice, setFreeChoice] = useState('')
  const [rationale, setRationale] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [dlgError, setDlgError] = useState('')

  const loadDecisions = useCallback(() => api.listDecisions('open')
    .then(d => { if (d && Array.isArray(d.items)) setDecisions(d.items) })
    .catch(() => { /* 拉取失败不阻塞主界面 */ })
  , [])

  useEffect(() => {
    let alive = true
    const load = () => api.boardSummary()
      .then(d => { if (alive) setDepAlerts((d && d.alerts) || []) })
      .catch(() => { /* 拉取失败不阻塞主界面 */ })
    load()
    loadDecisions()
    const t = setInterval(() => { load(); loadDecisions() }, 30000)
    return () => { alive = false; clearInterval(t) }
  }, [loadDecisions])

  const biz = useMemo(() => (allTasks || []).filter(t => !isNoiseTask(t)), [allTasks])
  // B：待验收——按 7 天分层，层内最老的排前面
  const reviewSplit = useMemo(() => {
    const all = biz
      .filter(t => t.state === 'completed' && t.stage === 'review')
      .sort((a, b) => (a.created_at || '').localeCompare(b.created_at || ''))
    return {
      fresh: all.filter(t => daysAgo(t.created_at) <= STALE_DAYS),
      stale: all.filter(t => daysAgo(t.created_at) > STALE_DAYS),
    }
  }, [biz])
  // C：失败待处置——按 7 天分层，层内最新的排前面（还有救的先看到）
  const failedSplit = useMemo(() => {
    const all = biz
      .filter(t => t.state === 'failed')
      .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''))
    return {
      fresh: all.filter(t => daysAgo(t.created_at) <= STALE_DAYS),
      stale: all.filter(t => daysAgo(t.created_at) > STALE_DAYS),
    }
  }, [biz])
  // open 决策——最老的排前面（拖得最久的先看到）
  const openDecisions = useMemo(
    () => [...decisions].sort((a, b) => (a.created_at || '').localeCompare(b.created_at || '')),
    [decisions])

  const decideCount = depAlerts.length + openDecisions.length
  const freshReview = reviewSplit.fresh.length
  const freshFailed = failedSplit.fresh.length
  const staleCount = reviewSplit.stale.length + failedSplit.stale.length
  const activeTotal = decideCount + freshReview + freshFailed
  const total = activeTotal + staleCount
  if (total === 0) return null

  const openResolve = (d) => {
    setResolving(d)
    setChoice((d.options && d.options[0] && d.options[0].label) || '')
    setFreeChoice('')
    setRationale('')
    setDlgError('')
  }

  const closeResolve = () => { if (!submitting) setResolving(null) }

  const submitResolve = async () => {
    if (!resolving) return
    const finalChoice = (freeChoice || '').trim() || (choice || '').trim()
    if (!finalChoice) { setDlgError('选一个选项或自由作答，才能提交'); return }
    setSubmitting(true)
    setDlgError('')
    try {
      await api.resolveDecision(resolving.id, { choice: finalChoice, rationale: (rationale || '').trim() })
      setResolving(null)
      await loadDecisions()
    } catch (e) {
      setDlgError(e && (e.message || e.userMessage) || '提交失败，请重试')
    } finally {
      setSubmitting(false)
    }
  }

  const Row = ({ id, title, age, hint, onClick, onKeyDown, cls, children }) => (
    <li
      className={`inbox-strip__row${cls ? ' ' + cls : ''}`}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick || (() => id && onOpenTask({ id }))}
      onKeyDown={onKeyDown || (e => { if ((e.key === 'Enter' || e.key === ' ') && id) { e.preventDefault(); onOpenTask({ id }) } })}
    >
      <span className="inbox-strip__row-id mono">{id || '—'}</span>
      <span className="inbox-strip__row-title">{title}</span>
      {age && <span className="inbox-strip__row-age mono">{age}</span>}
      {hint && <span className="inbox-strip__row-hint">{hint}</span>}
      {children}
    </li>
  )

  // 折叠的陈旧段：一行可点的汇总行 + 展开后的列表
  const StaleGroup = ({ label, items, show, onToggle }) => (
    items.length > 0 && (
      <div className="inbox-strip__stale">
        <button
          className="inbox-strip__stale-toggle"
          onClick={onToggle}
          aria-expanded={show}
        >
          <span className="inbox-strip__chev">{show ? '▾' : '▸'}</span>
          {label} · {items.length} 条（{show ? '收起' : '展开查看'}）
        </button>
        {show && (
          <ul className="inbox-strip__list">
            {items.slice(0, MAX_ROWS).map(t => (
              <Row key={t.id} id={t.id} title={t.title} age={fmtAgo(t.created_at)} hint="打开详情处置" />
            ))}
            {items.length > MAX_ROWS && <li className="inbox-strip__more">…另 {items.length - MAX_ROWS} 项</li>}
          </ul>
        )}
      </div>
    )
  )

  return (
    <section className={`inbox-strip${open ? ' is-open' : ''}`} aria-label="待我处理">
      <button className="inbox-strip__bar" onClick={() => setOpen(v => !v)} aria-expanded={open}>
        <span className="inbox-strip__dot" aria-hidden="true" />
        <strong className="inbox-strip__title">待我处理</strong>
        <span className="inbox-strip__chips">
          {decideCount > 0 && <span className="inbox-strip__chip is-block">决策 {decideCount}</span>}
          {freshReview > 0 && <span className="inbox-strip__chip is-review">验收 {freshReview}</span>}
          {freshFailed > 0 && <span className="inbox-strip__chip is-failed">失败 {freshFailed}</span>}
          {staleCount > 0 && <span className="inbox-strip__chip is-stale">积压 {staleCount}</span>}
        </span>
        <span className="inbox-strip__chev">{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <>
          <div className="inbox-drawer__backdrop" onClick={() => setOpen(false)} />
          <div className="inbox-strip__panel" role="dialog" aria-label="待我处理">
            <div className="inbox-drawer__head">
              <span className="inbox-strip__dot" aria-hidden="true" />
              <span className="inbox-drawer__title">待我处理</span>
              <span className="inbox-strip__chips">
                {decideCount > 0 && <span className="inbox-strip__chip is-block">决策 {decideCount}</span>}
                {freshReview > 0 && <span className="inbox-strip__chip is-review">验收 {freshReview}</span>}
                {freshFailed > 0 && <span className="inbox-strip__chip is-failed">失败 {freshFailed}</span>}
                {staleCount > 0 && <span className="inbox-strip__chip is-stale">积压 {staleCount}</span>}
              </span>
              <button className="inbox-drawer__close" onClick={() => setOpen(false)} aria-label="关闭">×</button>
            </div>
            <div className="inbox-drawer__body">
              {decideCount > 0 && (
                <div className="inbox-strip__sec">
                  <div className="inbox-strip__sec-title">
                    待决策 · {decideCount}（最老的在前；解开才动得了）
                  </div>
                  <ul className="inbox-strip__list">
                    {openDecisions.slice(0, MAX_ROWS).map(d => (
                      <Row
                        key={d.id}
                        cls="is-decision"
                        id={(d.task_id || d.id).slice(0, 8)}
                        title={d.question}
                        age={fmtAgo(d.created_at)}
                        onClick={() => openResolve(d)}
                        onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openResolve(d) } }}
                      >
                        <span className="inbox-strip__row-badge">裁决</span>
                      </Row>
                    ))}
                    {depAlerts.slice(0, MAX_ROWS).map((a, i) => {
                      const m = TID_RE.exec(a.message || '')
                      const title = (a.message || '').replace(/^任务「/, '').split('」')[0] || (a.message || '').slice(0, 60)
                      return <Row key={`dep${i}`} id={m ? m[1] : ''} title={title} hint="重开前置 / 换前置 / 砍后置" />
                    })}
                    {decideCount > MAX_ROWS && <li className="inbox-strip__more">…另 {decideCount - MAX_ROWS} 项</li>}
                  </ul>
                </div>
              )}

              {freshReview > 0 && (
                <div className="inbox-strip__sec">
                  <div className="inbox-strip__sec-title">待你验收 · {freshReview}（近 7 天，最老的在前）</div>
                  <ul className="inbox-strip__list">
                    {reviewSplit.fresh.slice(0, MAX_ROWS).map(t => (
                      <Row key={t.id} id={t.id} title={t.title} age={fmtAgo(t.created_at)} hint="打开详情验收" />
                    ))}
                    {freshReview > MAX_ROWS && <li className="inbox-strip__more">…另 {freshReview - MAX_ROWS} 项</li>}
                  </ul>
                </div>
              )}

              <StaleGroup
                label="更早的待验收"
                items={reviewSplit.stale}
                show={showStaleReview}
                onToggle={() => setShowStaleReview(v => !v)}
              />

              {freshFailed > 0 && (
                <div className="inbox-strip__sec">
                  <div className="inbox-strip__sec-title">失败待处置 · {freshFailed}（近 7 天，最新的在前）</div>
                  <ul className="inbox-strip__list">
                    {failedSplit.fresh.slice(0, MAX_ROWS).map(t => (
                      <Row key={t.id} id={t.id} title={t.title} age={fmtAgo(t.created_at)} hint="打开详情处置" />
                    ))}
                    {freshFailed > MAX_ROWS && <li className="inbox-strip__more">…另 {freshFailed - MAX_ROWS} 项</li>}
                  </ul>
                </div>
              )}

              <StaleGroup
                label="更早的失败任务"
                items={failedSplit.stale}
                show={showStaleFailed}
                onToggle={() => setShowStaleFailed(v => !v)}
              />
            </div>
          </div>
        </>
      )}

      {resolving && (
        <div className="inbox-dlg__overlay" role="presentation" onClick={e => { if (e.target === e.currentTarget) closeResolve() }}>
          <div className="inbox-dlg" role="dialog" aria-modal="true" aria-label="裁决决策">
            <div className="inbox-dlg__head">
              <strong>待你决策</strong>
              {resolving.source && <span>· 来自 {resolving.source}</span>}
              <button className="inbox-dlg__close" onClick={closeResolve} disabled={submitting} aria-label="关闭">×</button>
            </div>
            <div className="inbox-dlg__q">{resolving.question}</div>
            {resolving.context && (
              <>
                <div className="inbox-dlg__label">背景</div>
                <div className="inbox-dlg__ctx">{resolving.context}</div>
              </>
            )}
            {resolving.recommendation && (
              <div className="inbox-dlg__rec">建议：{resolving.recommendation}</div>
            )}
            {resolving.deadline && (
              <div className="inbox-dlg__label">截止：{resolving.deadline}</div>
            )}
            {(resolving.options || []).length > 0 && (
              <>
                <div className="inbox-dlg__label">选一个方案</div>
                <div className="inbox-dlg__opts">
                  {(resolving.options || []).map((o, i) => (
                    <label key={i} className={`inbox-dlg__opt${choice === o.label ? ' is-on' : ''}`}>
                      <span className="inbox-dlg__opt-row">
                        <input
                          type="radio"
                          name="dlg-choice"
                          value={o.label}
                          checked={choice === o.label}
                          onChange={() => { setChoice(o.label); setFreeChoice('') }}
                        />
                        <span className="inbox-dlg__opt-label">{o.label}</span>
                      </span>
                      {o.note && <span className="inbox-dlg__opt-note">{o.note}</span>}
                    </label>
                  ))}
                </div>
              </>
            )}
            <input
              className="inbox-dlg__free"
              placeholder="其他决定？直接写在这里（选了上方选项则忽略）"
              value={freeChoice}
              onChange={e => { setFreeChoice(e.target.value); if (e.target.value) setChoice('') }}
              maxLength={512}
            />
            <textarea
              className="inbox-dlg__why"
              placeholder="为什么这么决定？（一句话即可，留档审计，可空）"
              value={rationale}
              onChange={e => setRationale(e.target.value)}
              rows={2}
              maxLength={2048}
            />
            {dlgError && <div className="inbox-dlg__err">{dlgError}</div>}
            <div className="inbox-dlg__foot">
              <button
                className="inbox-dlg__ghost"
                onClick={async () => {
                  if (submitting) return
                  setSubmitting(true)
                  try {
                    await api.supersedeDecision(resolving.id)
                    setResolving(null)
                    await loadDecisions()
                  } catch (e) {
                    setDlgError(e && (e.message || e.userMessage) || '作废失败')
                  } finally {
                    setSubmitting(false)
                  }
                }}
                disabled={submitting}
              >作废</button>
              <button
                className="inbox-dlg__ok"
                onClick={submitResolve}
                disabled={submitting}
              >{submitting ? '提交中…' : '落裁决'}</button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
