import { useState } from 'react'
import { PRIORITY } from '../constants'

const EMPTY = { title: '', description: '', priority: 0, est_duration_min: 30, target_agent_type: '' }

export default function CreateModal({ onClose, onCreate }) {
  const [form, setForm] = useState(EMPTY)
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    if (!form.title.trim()) return
    setBusy(true)
    try {
      await onCreate({
        title: form.title.trim(),
        description: form.description.trim(),
        priority: form.priority,
        est_duration_min: Math.max(5, form.est_duration_min || 30),
        target_agent_type: form.target_agent_type.trim() || undefined,
      })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label="创建新任务" onClick={e => e.stopPropagation()}>
        <div className="modal__head">
          <h3>创建任务</h3>
          <button className="modal__close" onClick={onClose} aria-label="关闭">×</button>
        </div>

        <form onSubmit={submit}>
          <div className="field">
            <label className="field__label">任务标题 <b>*</b></label>
            <input
              autoFocus
              value={form.title}
              onChange={e => setForm({ ...form, title: e.target.value })}
              placeholder="例如：数据清洗脚本"
            />
          </div>

          <div className="field">
            <label className="field__label">描述</label>
            <textarea
              value={form.description}
              onChange={e => setForm({ ...form, description: e.target.value })}
              placeholder="任务详细说明…"
              rows={3}
            />
          </div>

          <div className="field field--row">
            <div>
              <label className="field__label">优先级</label>
              <div className="prio">
                {PRIORITY.map(({ p, label, text }) => (
                  <button
                    key={p}
                    type="button"
                    className={form.priority === p ? 'is-on' : ''}
                    onClick={() => setForm({ ...form, priority: p })}
                    title={text}
                  >{label}</button>
                ))}
              </div>
            </div>
            <div>
              <label className="field__label">预估时长（分）</label>
              <input
                type="number"
                min={5}
                step={5}
                value={form.est_duration_min}
                onChange={e => setForm({ ...form, est_duration_min: +e.target.value })}
              />
            </div>
          </div>

          <div className="field">
            <label className="field__label">目标 Agent 类型</label>
            <input
              value={form.target_agent_type}
              onChange={e => setForm({ ...form, target_agent_type: e.target.value })}
              placeholder="留空表示任意 agent 可领取"
            />
          </div>

          <div className="modal__foot">
            <button type="button" className="btn btn--ghost" onClick={onClose}>取消</button>
            <button type="submit" className="btn btn--accent" disabled={busy}>
              {busy ? '提交中…' : '创建任务'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
