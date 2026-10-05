import { useState, useEffect, useCallback } from 'react'
import { api } from '../api'
import { fmtAgo } from '../constants'

function emptyForm() {
  return { title: '', description: '', category: '', priority: 0, est_duration_min: 30, target_agent_type: '', acceptance_criteria: '', labels: '', tags: '' }
}

function TemplateForm({ initial, submitLabel, busy, onSubmit, onCancel }) {
  const [form, setForm] = useState(() => ({
    title: initial?.title || '',
    description: initial?.description || '',
    category: initial?.category || '',
    priority: initial?.priority || 0,
    est_duration_min: initial?.est_duration_min || 30,
    target_agent_type: initial?.target_agent_type || '',
    acceptance_criteria: initial?.acceptance_criteria || '',
    labels: (initial?.labels || []).join(', '),
    tags: (initial?.tags || []).join(', '),
  }))
  const submit = async (e) => {
    e.preventDefault()
    if (!form.title.trim()) return
    const payload = {
      title: form.title.trim(),
      description: form.description.trim(),
      category: form.category.trim(),
      priority: form.priority,
      est_duration_min: form.est_duration_min,
      target_agent_type: form.target_agent_type.trim() || undefined,
      acceptance_criteria: form.acceptance_criteria.trim(),
      labels: form.labels ? form.labels.split(',').map(s => s.trim()).filter(Boolean) : [],
      tags: form.tags ? form.tags.split(',').map(s => s.trim()).filter(Boolean) : [],
    }
    await onSubmit(payload)
  }
  return (
    <form onSubmit={submit}>
      <div className="field"><label className="field__label">模板标题 <b>*</b></label><input value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} placeholder="例如：数据清洗脚本" /></div>
      <div className="field"><label className="field__label">描述</label><textarea value={form.description} onChange={e => setForm({ ...form, description: e.target.value })} rows={2} placeholder="模板说明…" /></div>
      <div className="field field--row">
        <div><label className="field__label">分类</label><input value={form.category} onChange={e => setForm({ ...form, category: e.target.value })} placeholder="data, backend, frontend" /></div>
        <div><label className="field__label">预估时长（分）</label><input type="number" min={5} step={5} value={form.est_duration_min} onChange={e => setForm({ ...form, est_duration_min: +e.target.value })} /></div>
      </div>
      <div className="field"><label className="field__label">目标 Agent</label><input value={form.target_agent_type} onChange={e => setForm({ ...form, target_agent_type: e.target.value })} placeholder="留空=任意" /></div>
      <div className="field"><label className="field__label">验收标准</label><textarea value={form.acceptance_criteria} onChange={e => setForm({ ...form, acceptance_criteria: e.target.value })} rows={2} placeholder="完成定义…" /></div>
      <div className="field field--row">
        <div><label className="field__label">标签</label><input value={form.labels} onChange={e => setForm({ ...form, labels: e.target.value })} placeholder="bug,urgent" /></div>
        <div><label className="field__label">Tags</label><input value={form.tags} onChange={e => setForm({ ...form, tags: e.target.value })} placeholder="tag1,tag2" /></div>
      </div>
      <div className="modal__foot">
        <button type="button" className="btn btn--ghost" onClick={onCancel}>取消</button>
        <button type="submit" className="btn btn--accent" disabled={busy}>{busy ? '处理中…' : submitLabel}</button>
      </div>
    </form>
  )
}

export default function TemplatesView() {
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState(null) // 'create' | 'edit' | null
  const [editing, setEditing] = useState(null)
  const [busy, setBusy] = useState(false)
  const [filter, setFilter] = useState('')
  const [detail, setDetail] = useState(null)
  const [err, setErr] = useState(null)
  const [useTpl, setUseTpl] = useState(null) // 直接创建任务时的模板
  const [taskTitle, setTaskTitle] = useState('')
  const [taskProject, setTaskProject] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try { setTemplates(await api.listTemplates()) } catch (e) { setErr(e.message) }
    setLoading(false)
  }, [])

  useEffect(() => { load() }, [load])

  const submitCreate = async (payload) => {
    setBusy(true)
    try {
      await api.createTemplate(payload)
      setMode(null)
      load()
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }

  const submitEdit = async (payload) => {
    setBusy(true)
    try {
      await api.updateTemplate(editing.id, payload)
      setMode(null)
      setEditing(null)
      load()
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }

  const remove = async (id) => {
    if (!confirm('确认删除此模板？')) return
    try { await api.deleteTemplate(id); load() } catch (e) { setErr(e.message) }
  }

  const openUse = (t) => {
    setUseTpl(t)
    setTaskTitle(t.title)
    setTaskProject('')
  }

  const confirmUse = async () => {
    if (!taskTitle.trim()) return
    setBusy(true)
    try {
      await api.createTaskFromTemplate(useTpl.id, { title: taskTitle.trim(), project: taskProject.trim() })
      setUseTpl(null)
      setTaskTitle('')
      setTaskProject('')
    } catch (e) { setErr(e.message) }
    setBusy(false)
  }

  const filtered = filter
    ? templates.filter(t => t.title.includes(filter) || t.category.includes(filter))
    : templates

  const [versions, setVersions] = useState(null)   // {tplId, list}
  const [verBusy, setVerBusy] = useState(false)

  const toggleVersions = async (t) => {
    if (versions?.tplId === t.id) { setVersions(null); return }
    setVerBusy(true)
    try {
      const list = await api.listTemplateVersions(t.id)
      setVersions({ tplId: t.id, list })
    } catch (e) { setErr(e.message) }
    setVerBusy(false)
  }

  const restore = async (t, version) => {
    if (!confirm(`确认回滚到 v${version}？当前内容将作为新版本保存。`)) return
    setVerBusy(true)
    try {
      await api.restoreTemplateVersion(t.id, version)
      setVersions(null)
      load()
    } catch (e) { setErr(e.message) }
    setVerBusy(false)
  }

  return (
    <div className="templates-view">
      <div className="tpl-header">
        <h3>任务模板</h3>
        <div className="tpl-header__actions">
          <input className="tpl-header__filter" placeholder="搜索模板…" value={filter} onChange={e => setFilter(e.target.value)} />
          <button className="btn btn--accent btn--sm" onClick={() => { setEditing(null); setMode('create') }}>+ 新建模板</button>
        </div>
      </div>

      {err && <div className="errorbar" role="alert"><span>▲</span><span>{err}</span><button onClick={() => setErr(null)} aria-label="关闭">×</button></div>}

      {mode === 'create' && (
        <div className="tpl-create-panel">
          <TemplateForm initial={null} submitLabel="创建模板" busy={busy} onSubmit={submitCreate} onCancel={() => setMode(null)} />
        </div>
      )}
      {mode === 'edit' && editing && (
        <div className="tpl-create-panel">
          <TemplateForm initial={editing} submitLabel="保存修改" busy={busy} onSubmit={submitEdit} onCancel={() => { setMode(null); setEditing(null) }} />
        </div>
      )}

      {useTpl && (
        <div className="tpl-use-panel">
          <h4>从模板创建任务：{useTpl.title}</h4>
          <div className="field"><label className="field__label">任务标题 <b>*</b></label><input value={taskTitle} onChange={e => setTaskTitle(e.target.value)} placeholder="任务标题" /></div>
          <div className="field"><label className="field__label">项目</label><input value={taskProject} onChange={e => setTaskProject(e.target.value)} placeholder="例如：数据平台" /></div>
          <div className="modal__foot">
            <button type="button" className="btn btn--ghost" onClick={() => { setUseTpl(null); setTaskTitle(''); setTaskProject('') }}>取消</button>
            <button type="button" className="btn btn--accent" disabled={busy} onClick={confirmUse}>{busy ? '创建中…' : '创建任务'}</button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="skeleton" />
      ) : filtered.length === 0 ? (
        <div className="empty-state">暂无模板</div>
      ) : (
        <div className="tpl-grid">
          {filtered.map(t => (
            <div key={t.id} className="tpl-card" onClick={() => setDetail(detail?.id === t.id ? null : t)}>
              <div className="tpl-card__head">
                <span className="tpl-card__title">{t.title}</span>
                {t.category && <span className="tpl-card__cat">{t.category}</span>}
              </div>
              {t.description && <div className="tpl-card__desc">{t.description}</div>}
              <div className="tpl-card__meta">
                {t.target_agent_type && <span className="tpl-card__agent">{t.target_agent_type}</span>}
                <span className="tpl-card__dur">{t.est_duration_min}min</span>
                <span className="tpl-card__time">{fmtAgo(t.created_at)}</span>
                <span className="tpl-card__actions">
                  <button className="btn btn--ghost btn--xs" onClick={e => { e.stopPropagation(); openUse(t) }} title="创建任务">＋任务</button>
                  <button className="btn btn--ghost btn--xs" onClick={e => { e.stopPropagation(); setEditing(t); setMode('edit') }} title="编辑">✎</button>
                  <button className="btn btn--ghost btn--xs" onClick={e => { e.stopPropagation(); remove(t.id) }} title="删除">🗑</button>
                </span>
              </div>
              {detail?.id === t.id && (
                <div className="tpl-card__detail">
                  <div className="tpl-card__detail-row"><b>验收标准：</b>{t.acceptance_criteria || '—'}</div>
                  <div className="tpl-card__detail-row"><b>文件模板：</b>{(t.files_template || []).join(', ') || '—'}</div>
                  <div className="tpl-card__detail-row"><b>产出物：</b>{(t.deliverables_template || []).join(', ') || '—'}</div>
                  <div className="tpl-card__detail-row">
                    <b>版本：</b>v{t.version}
                    <button className="btn btn--ghost btn--xs" style={{ marginLeft: 8 }} onClick={e => { e.stopPropagation(); toggleVersions(t) }}>版本历史</button>
                  </div>
                  {versions?.tplId === t.id && (
                    <div className="tpl-versions">
                      {verBusy && <div className="tpl-versions__loading">加载中…</div>}
                      {!verBusy && versions.list.length === 0 && <div className="tpl-versions__empty">暂无历史版本</div>}
                      {!verBusy && versions.list.map(v => (
                        <div key={v.id} className="tpl-version-row">
                          <span className="tpl-version-row__v">v{v.version}</span>
                          <span className="tpl-version-row__desc">{v.description || '—'}</span>
                          <span className="tpl-version-row__by">{v.created_by || '—'}</span>
                          <button className="btn btn--ghost btn--xs" disabled={verBusy} onClick={e => { e.stopPropagation(); restore(t, v.version) }}>回滚</button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
