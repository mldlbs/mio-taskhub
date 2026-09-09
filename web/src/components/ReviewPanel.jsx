import { useState, useEffect } from 'react'
import { fmtDate } from '../constants'
import { api } from '../api'

const CHECKLIST_ITEMS = [
  { key: '功能完整', label: '功能完整', desc: '实现了需求描述的所有功能' },
  { key: '代码质量', label: '代码质量', desc: '代码结构清晰、命名规范、无明显坏味道' },
  { key: '测试覆盖', label: '测试覆盖', desc: '关键路径有测试覆盖' },
  { key: '文档完整', label: '文档完整', desc: '必要文档已更新（README/注释/变更记录）' },
  { key: '性能合理', label: '性能合理', desc: '无明显性能问题' },
  { key: '安全合规', label: '安全合规', desc: '无明显安全风险' },
]

const DECISION_META = {
  approve: { label: '通过', icon: '✓', cls: 'rp-decision--approve' },
  reject:  { label: '驳回', icon: '✕', cls: 'rp-decision--reject' },
  comment: { label: '仅评论', icon: '💬', cls: 'rp-decision--comment' },
}

function ReviewHistory({ reviews }) {
  if (!reviews || reviews.length === 0) return null
  return (
    <div className="rp-history">
      <h4 className="rp-history__title">审阅历史 ({reviews.length})</h4>
      {reviews.map(r => {
        const dm = DECISION_META[r.decision] || DECISION_META.comment
        return (
          <div key={r.id} className={`rp-history__item rp-history__item--${r.decision}`}>
            <div className="rp-history__head">
              <span className={`rp-history__badge ${dm.cls}`}>{dm.icon} {dm.label}</span>
              <span className="rp-history__reviewer">{r.reviewer || '匿名'}</span>
              <span className="rp-history__time mono">{fmtDate(r.created_at)}</span>
              {r.review_duration_sec != null && (
                <span className="rp-history__duration mono">
                  耗时 {r.review_duration_sec < 60 ? `${r.review_duration_sec}s` : `${Math.floor(r.review_duration_sec / 60)}m${r.review_duration_sec % 60}s`}
                </span>
              )}
            </div>
            {r.checklist && (
              <div className="rp-history__checklist">
                {Object.entries(r.checklist).map(([k, v]) => (
                  <span key={k} className={`rp-check ${v ? 'rp-check--pass' : 'rp-check--fail'}`}>
                    {v ? '✓' : '✕'} {k}
                  </span>
                ))}
              </div>
            )}
            {r.summary && <div className="rp-history__summary">{r.summary}</div>}
            {r.comments && <div className="rp-history__comments">{r.comments}</div>}
          </div>
        )
      })}
    </div>
  )
}

export default function ReviewPanel({ task, onSubmit }) {
  const [checklist, setChecklist] = useState({})
  const [summary, setSummary] = useState('')
  const [comments, setComments] = useState('')
  const [reviewer, setReviewer] = useState('')
  const [reviews, setReviews] = useState([])
  const [loading, setLoading] = useState(false)
  const [loadBusy, setLoadBusy] = useState(true)

  useEffect(() => {
    if (!task?.id) return
    setLoadBusy(true)
    api.listReviews(task.id)
      .then(data => { setReviews(data); setLoadBusy(false) })
      .catch(() => setLoadBusy(false))
  }, [task?.id])

  const toggleCheck = (key) => {
    setChecklist(prev => ({ ...prev, [key]: !prev[key] }))
  }

  const submit = async (decision) => {
    if (decision === 'approve') {
      const passCount = Object.values(checklist).filter(Boolean).length
      if (passCount < 3) {
        if (!window.confirm(`仅 ${passCount}/${CHECKLIST_ITEMS.length} 项通过，确定要批准？`)) return
      }
    }
    setLoading(true)
    try {
      await api.submitReview(task.id, {
        decision,
        checklist,
        summary: summary.trim(),
        comments: comments.trim(),
        reviewer: reviewer.trim() || '用户',
      })
      // 重新加载审阅历史
      const data = await api.listReviews(task.id)
      setReviews(data)
      setChecklist({})
      setSummary('')
      setComments('')
      onSubmit && onSubmit(task.id, decision)
    } catch (e) {
      alert('提交失败: ' + e.message)
    } finally {
      setLoading(false)
    }
  }

  const passCount = Object.values(checklist).filter(Boolean).length
  const totalChecklist = CHECKLIST_ITEMS.length

  return (
    <div className="rp">
      <div className="rp__header">
        <h3>审阅</h3>
        <span className="rp__task-title" title={task.title}>{task.title}</span>
        {task.review_started_at && (
          <span className="rp__sla mono">
            审阅中 · {Math.floor((Date.now() - new Date(task.review_started_at).getTime()) / 60000)} 分钟
          </span>
        )}
      </div>

      <div className="rp__checklist">
        <div className="rp__checklist-head">
          <span>检查清单</span>
          <span className={`rp__checklist-score${passCount === totalChecklist ? ' rp__checklist-score--all' : ''}`}>
            {passCount}/{totalChecklist}
          </span>
        </div>
        {CHECKLIST_ITEMS.map(item => (
          <label key={item.key} className={`rp__check-item${checklist[item.key] ? ' is-checked' : ''}`}>
            <input
              type="checkbox"
              checked={!!checklist[item.key]}
              onChange={() => toggleCheck(item.key)}
            />
            <span className="rp__check-label">{item.label}</span>
            <span className="rp__check-desc">{item.desc}</span>
          </label>
        ))}
      </div>

      <div className="rp__fields">
        <div className="field">
          <label className="field__label">审阅摘要</label>
          <input
            className="rp__input"
            value={summary}
            onChange={e => setSummary(e.target.value)}
            placeholder="一句话总结审阅结论…"
          />
        </div>
        <div className="field">
          <label className="field__label">详细批注</label>
          <textarea
            className="rp__textarea"
            value={comments}
            onChange={e => setComments(e.target.value)}
            placeholder="对实现细节的评论、建议、问题…"
            rows={4}
          />
        </div>
        <div className="field">
          <label className="field__label">审阅人</label>
          <input
            className="rp__input"
            value={reviewer}
            onChange={e => setReviewer(e.target.value)}
            placeholder="你的名字或 agent ID"
          />
        </div>
      </div>

      <div className="rp__actions">
        <button
          className="btn btn--primary rp__btn rp__btn--approve"
          onClick={() => submit('approve')}
          disabled={loading}
        >
          {loading ? '提交中…' : '✓ 通过'}
        </button>
        <button
          className="btn btn--ghost rp__btn rp__btn--reject"
          onClick={() => submit('reject')}
          disabled={loading}
        >
          {loading ? '提交中…' : '✕ 驳回'}
        </button>
        <button
          className="btn btn--ghost rp__btn rp__btn--comment"
          onClick={() => submit('comment')}
          disabled={loading}
        >
          {loading ? '提交中…' : '💬 仅评论'}
        </button>
      </div>

      {loadBusy ? (
        <div className="rp__loading">加载审阅历史…</div>
      ) : (
        <ReviewHistory reviews={reviews} />
      )}
    </div>
  )
}
