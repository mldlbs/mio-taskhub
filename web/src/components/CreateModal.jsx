import { useState, useEffect } from 'react'
import { PRIORITY } from '../constants'
import { api } from '../api'

const EMPTY = {
  title: '', description: '', priority: 0, est_duration_min: 30, target_agent_type: '',
  acceptance_criteria: '', due_at: '', labels: '', project: '', workspace: '',
  files: '', deliverables: '',
}

export default function CreateModal({ onClose, onCreate }) {
  const [form, setForm] = useState(EMPTY)
  const [busy, setBusy] = useState(false)
  const [templates, setTemplates] = useState([])
  const [showTpl, setShowTpl] = useState(false)
  const [tplLoading, setTplLoading] = useState(false)
  const [tplFilter, setTplFilter] = useState('')

  useEffect(() => {
    if (showTpl) {
      setTplLoading(true)
      api.listTemplates().then(d => { setTemplates(d); setTplLoading(false) }).catch(() => setTplLoading(false))
    }
  }, [showTpl])

  const applyTemplate = (tpl) => {
    setForm({
      title: tpl.title || '',
      description: tpl.description || '',
      priority: tpl.priority || 0,
      est_duration_min: tpl.est_duration_min || 30,
      target_agent_type: tpl.target_agent_type || '',
      acceptance_criteria: tpl.acceptance_criteria || '',
      due_at: '',
      labels: (tpl.labels || []).join(', '),
      project: '',
      workspace: '',
      files: (tpl.files_template || []).join(', '),
      deliverables: (tpl.deliverables_template || []).join(', '),
    })
    setShowTpl(false)
  }

  const filtered = tplFilter
    ? templates.filter(t => t.title.includes(tplFilter) || t.category.includes(tplFilter) || (t.tags || []).some(x => x.includes(tplFilter)))
    : templates

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
        acceptance_criteria: form.acceptance_criteria.trim(),
        due_at: form.due_at || null,
        labels: form.labels ? form.labels.split(',').map(s => s.trim()).filter(Boolean) : [],
        project: form.project.trim(),
        workspace: form.workspace.trim(),
        files: form.files ? form.files.split(',').map(s => s.trim()).filter(Boolean) : [],
        deliverables: form.deliverables ? form.deliverables.split(',').map(s => s.trim()).filter(Boolean) : [],
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
          <div className="tpl-bar">
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => setShowTpl(s => !s)}>
              📋 从模板创建
            </button>
            {form.title && (
              <span className="tpl-hint">已填写表单（手动）</span>
            )}
          </div>

          {showTpl && (
            <div className="tpl-panel">
              <input
                className="tpl-search"
                placeholder="搜索模板…"
                value={tplFilter}
                onChange={e => setTplFilter(e.target.value)}
                autoFocus
              />
              {tplLoading ? (
                <div className="tpl-loading">加载中…</div>
              ) : filtered.length === 0 ? (
                <div className="tpl-empty">暂无模板，可从任务创建</div>
              ) : (
                <ul className="tpl-list">
                  {filtered.map(t => (
                    <li key={t.id} className="tpl-item" onClick={() => applyTemplate(t)}>
                      <span className="tpl-item__title">{t.title}</span>
                      {t.category && <span className="tpl-item__cat">{t.category}</span>}
                      {t.target_agent_type && <span className="tpl-item__agent">{t.target_agent_type}</span>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

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

          <div className="field">
            <label className="field__label">验收标准</label>
            <textarea
              value={form.acceptance_criteria}
              onChange={e => setForm({ ...form, acceptance_criteria: e.target.value })}
              placeholder="完成定义 / 如何验收…"
              rows={2}
            />
          </div>

          <div className="field field--row">
            <div>
              <label className="field__label">截止时间</label>
              <input
                type="datetime-local"
                value={form.due_at}
                onChange={e => setForm({ ...form, due_at: e.target.value })}
              />
            </div>
            <div>
              <label className="field__label">项目</label>
              <input
                value={form.project}
                onChange={e => setForm({ ...form, project: e.target.value })}
                placeholder="例如：数据平台"
              />
            </div>
          </div>

          <div className="field field--row">
            <div>
              <label className="field__label">工作区路径</label>
              <input
                value={form.workspace}
                onChange={e => setForm({ ...form, workspace: e.target.value })}
                placeholder="例如：/repo/agent-dev"
              />
            </div>
            <div>
              <label className="field__label">标签</label>
              <input
                value={form.labels}
                onChange={e => setForm({ ...form, labels: e.target.value })}
                placeholder="blocked,waiting-review"
              />
            </div>
          </div>

          <div className="field">
            <label className="field__label">文件路径</label>
            <input
              value={form.files}
              onChange={e => setForm({ ...form, files: e.target.value })}
              placeholder="src/a.py,src/b.py"
            />
          </div>

          <div className="field">
            <label className="field__label">产出物</label>
            <input
              value={form.deliverables}
              onChange={e => setForm({ ...form, deliverables: e.target.value })}
              placeholder="report.md"
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
