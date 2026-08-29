import { useState, useCallback, useEffect } from 'react'
import { marked } from 'marked'
import { api } from '../api'
import { fmtAgo, fmtDate } from '../constants'

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
const ROLE_LABEL = { user: '你', agent: 'agent', ask: 'agent 提问' }
const KIND_LABEL = { review: '评审', status: '状态流转', discussion: '讨论', operation: '操作' }

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

  const fail = useCallback((e) => setErr(e.message || '操作失败'), [])

  const openDetail = useCallback(async (id) => {
    setBreaking(false)
    setBreakRows([{ ref: 't1', title: '', deps: '' }])
    setSubmitting(false)
    setShowHistory(false)
    setEditing(false)
    try {
      const [d, h] = await Promise.all([api.getIdea(id), api.ideaHistory(id)])
      setDetail(d); setHist(h); setErr(null)
    } catch (e) { fail(e) }
  }, [fail])

  const reloadDetail = useCallback(async () => {
    if (!detail) return
    try {
      const [d, h] = await Promise.all([api.getIdea(detail.id), api.ideaHistory(detail.id)])
      setDetail(d); setHist(h)
    } catch (e) { /* 静默 */ }
  }, [detail])

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
              <p className="idea-detail__desc">{detail.description || '（暂无描述）'}</p>

              <div className="idea-detail__history">
                <button className="btn btn--ghost" onClick={() => setShowHistory(s => !s)}>
                  变更历史（{detail.changes?.length || 0}）{showHistory ? '▾' : '▸'}
                </button>
                {showHistory && (
                  <div className="idea-detail__changes">
                    {(detail.changes || []).map(ch => (
                      <div key={ch.id} className="change-row">
                        <span className="tag tag--version">v{ch.version}</span>
                        <span className="change-row__at">{new Date(ch.created_at).toLocaleString()}</span>
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