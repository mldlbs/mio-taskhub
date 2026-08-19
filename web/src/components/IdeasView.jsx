import { useState, useCallback, useEffect } from 'react'
import { api } from '../api'
import { fmtAgo, fmtDate } from '../constants'

const IDEA_META = {
  new:        { label: '记录中', tone: 'dim' },
  fermenting: { label: '发酵中', tone: 'live' },
  formed:     { label: '已成形', tone: 'ok' },
  broken_down:{ label: '已拆解', tone: 'ok' },
  archived:   { label: '已归档', tone: 'dim' },
  cancelled:  { label: '已取消', tone: 'danger' },
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
  const [breakRows, setBreakRows] = useState([{ ref: 't1', title: '', deps: '' }])
  const [submitting, setSubmitting] = useState(false)
  const [showHistory, setShowHistory] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editForm, setEditForm] = useState({ title: '', description: '', reason: '' })
  const [hist, setHist] = useState(null)

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
    setBreakRows(r => [...r, { ref: `t${r.length + 1}`, title: '', deps: '' }])

  const submitBreakdown = async () => {
    if (submitting) return
    const rows = breakRows.filter(r => r.title.trim())
    if (!rows.length) return
    setSubmitting(true)
    const tasks = rows.map(r => ({
      title: r.title.trim(),
      ref: r.ref || undefined,
      depends_on: r.deps.split(',').map(s => s.trim()).filter(Boolean),
    }))
    try {
      await api.breakdownIdea(detail.id, { tasks })
      setBreaking(false)
      setBreakRows([{ ref: 't1', title: '', deps: '' }])
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

  useEffect(() => {
    if (!detail) return
    const t = setInterval(reloadDetail, 5000)
    return () => clearInterval(t)
  }, [detail, reloadDetail])

  const next = NEXT_STATUS[detail?.status]
  const nextMeta = next ? IDEA_META[next] : null

  return (
    <div className="ideas">
      {err && <div className="errorbar" role="alert"><span>▲</span><span>{err}</span><button onClick={() => setErr(null)} aria-label="关闭">×</button></div>}

      <div className="ideas__top">
        <h2 className="ideas__title">想法与需求</h2>
        <button className="btn btn--primary" onClick={() => setCreating(c => !c)}>{creating ? '取消' : '+ 记个想法'}</button>
      </div>

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
          {ideas.length === 0 && <div className="ideas__empty">还没有想法。点右上角「记个想法」，随手把需求、点子、改进记下来。</div>}
          {ideas.map(i => {
            const m = IDEA_META[i.status] || IDEA_META.new
            return (
              <button key={i.id} className={`idea-card${detail?.id === i.id ? ' is-active' : ''}`} onClick={() => openDetail(i.id)}>
                <div className="idea-card__head">
                  <span className="idea-card__title">{i.title}</span>
                  {i.version > 1 && <span className="tag tag--version">v{i.version}</span>}
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
                {detail.status === 'formed' && (
                  <button className="btn btn--accent" onClick={() => setBreaking(b => !b)}>
                    {breaking ? '取消拆解' : '→ 拆解为任务'}
                  </button>
                )}
                {(detail.status === 'new' || detail.status === 'fermenting' || detail.status === 'formed') && (
                  <button className="btn btn--ghost" onClick={() => advance('archived')}>归档</button>
                )}
                <button className="btn btn--ghost" onClick={() => {
                  setEditForm({ title: detail.title, description: detail.description, reason: '' })
                  setEditing(true)
                }}>编辑</button>
              </div>

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
            </>
          )}
        </div>
      </div>
    </div>
  )
}