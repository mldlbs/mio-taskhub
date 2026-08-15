import { useState, useEffect } from 'react'
import { prio, fmtDur, agMono, agColor } from '../constants'

const STAGES = [
  { id: 'brainstorming', label: '需求理解', en: 'BRAINSTORM' },
  { id: 'design',        label: '设计',     en: 'DESIGN' },
  { id: 'planning',      label: '计划',     en: 'PLANNING' },
  { id: 'ready',         label: '待执行',   en: 'READY' },
  { id: 'implementing',  label: '执行中',   en: 'IMPL' },
  { id: 'review',        label: '审查',     en: 'REVIEW' },
  { id: 'done',          label: '完成',     en: 'DONE' },
]

const BACKTRACKS = [
  { from: 'design',       to: 'brainstorming', note: '设计时需求不明' },
  { from: 'planning',     to: 'design',        note: '计划时发现设计不足' },
  { from: 'review',       to: 'planning',      note: '审查不过需改计划' },
  { from: 'review',       to: 'design',        note: '审查不过需改设计' },
  { from: 'review',       to: 'ready',         note: '审查不过直接重做' },
]

const labelOf = (id) => STAGES.find(s => s.id === id)?.label || id
const nodeScale = (n) => Math.min(1 + n * 0.06, 1.5)

const depState = (t, tasks) => {
  const deps = t.depends_on || []
  if (!deps.length) return null
  const map = Object.fromEntries(tasks.map(x => [x.id, x]))
  const blocked = deps.some(d => {
    const p = map[d]
    return p && (p.state === 'cancelled' || p.state === 'failed')
  })
  const done = deps.every(d => {
    const p = map[d]
    return p && (p.state === 'completed' || p.stage === 'done')
  })
  return { count: deps.length, done, blocked }
}

export default function FlowView({ tasks, onOpen, onCancel, onAdvance, onMoveToStage }) {
  const [advancing, setAdvancing] = useState(null)
  const [artifact, setArtifact] = useState('')
  const [expanded, setExpanded] = useState(STAGES[0].id)
  const [draggingId, setDraggingId] = useState(null)

  const onCardDragStart = (e, id) => {
    setDraggingId(id)
    e.dataTransfer.setData('text/plain', id)
    e.dataTransfer.effectAllowed = 'move'
  }

  const onColumnDrop = async (stageId) => {
    if (!draggingId) return
    const task = tasks.find(t => t.id === draggingId)
    setDraggingId(null)
    if (!task || task.stage === stageId) return
    const body = { target_stage: stageId }
    if (stageId === 'design') {
      const val = window.prompt(`移动到 design 需提供 Spec 路径：`, task.spec_path || '')
      if (val === null) return
      if (!val.trim()) return
      body.spec_path = val
    } else if (stageId === 'planning') {
      const val = window.prompt(`移动到 planning 需提供 Plan 路径：`, task.plan_path || '')
      if (val === null) return
      if (!val.trim()) return
      body.plan_path = val
    } else if (stageId === 'done') {
      const val = window.prompt(`移动到 done 需提供审查结论：`, task.review_result || '')
      if (val === null) return
      if (!val.trim()) return
      body.review_result = val
    }
    try {
      await onMoveToStage(task.id, body)
    } catch (e) {
      alert('移动失败: ' + (e.message || '阶段不合法'))
    }
  }

  const flowTasks = tasks.filter(t => t.stage !== 'cancelled')
  const byStage = Object.fromEntries(STAGES.map(s => [s.id, flowTasks.filter(t => t.stage === s.id)]))
  const stage = STAGES.find(s => s.id === expanded)
  const activeTasks = stage ? byStage[stage.id] : []

  useEffect(() => {
    if (!advancing) return
    const onKey = (e) => { if (e.key === 'Escape') { setAdvancing(null); setArtifact('') } }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [advancing])

  const artifactField = (target) =>
    target === 'design' ? { key: 'spec_path', ph: 'docs/superpowers/specs/xxx.md' } :
    target === 'planning' ? { key: 'plan_path', ph: 'docs/superpowers/plans/xxx.md' } :
    target === 'done' ? { key: 'review_result', ph: '审查结论…' } : null

  const nextStage = (stage) => {
    const i = STAGES.findIndex(s => s.id === stage)
    return i >= 0 && i < STAGES.length - 1 ? STAGES[i + 1].id : null
  }

  const confirmAdvance = async () => {
    if (!advancing) return
    const { task, target } = advancing
    const af = artifactField(target)
    const body = { target_stage: target }
    if (af) {
      if (!artifact.trim()) { alert('请填写产出物'); return }
      body[af.key] = artifact.trim()
    }
    try {
      await onAdvance(task.id, body)
      setAdvancing(null); setArtifact('')
    } catch (e) { alert('推进失败: ' + e.message) }
  }

  return (
    <div className="flow">
      <div className="flow__canvas">
        <div className="flow__nodes">
          {STAGES.map((s, i) => (
            <div key={s.id} className="flow__step"
              onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }}
              onDragEnter={e => { e.currentTarget.classList.add('is-dragover') }}
              onDragLeave={e => { e.currentTarget.classList.remove('is-dragover') }}
              onDrop={e => { e.preventDefault(); e.currentTarget.classList.remove('is-dragover'); onColumnDrop(s.id) }}
            >
              {i > 0 && (
                <div className="flow__arrow" aria-hidden="true"><span>→</span></div>
              )}
              <button
                type="button"
                className={`flow-node${expanded === s.id ? ' is-open' : ''}`}
                style={{ '--s': nodeScale(byStage[s.id].length) }}
                onClick={() => setExpanded(expanded === s.id ? null : s.id)}
                aria-expanded={expanded === s.id}
                aria-label={`阶段 ${s.label}，${byStage[s.id].length} 个任务`}
              >
                <span className="flow-node__en">{s.en}</span>
                <span className="flow-node__label">{s.label}</span>
                <span className="flow-node__count">{byStage[s.id].length}</span>
              </button>
            </div>
          ))}
        </div>

        {stage && (
          <div className="flow__panel">
            <div className="flow__panel-head">
              <span className="flow__panel-title">{stage.label}</span>
              <span className="flow__panel-count">{activeTasks.length} 个任务</span>
            </div>
            {activeTasks.map(t => {
              const p = prio(t.priority)
              const ac = t.target_agent_type && agColor(t.target_agent_type)
              const next = nextStage(t.stage)
              return (
                <div key={t.id} className="flow-card" role="button" tabIndex={0}
                  draggable
                  onDragStart={e => onCardDragStart(e, t.id)}
                  aria-label={`任务 ${t.title}，阶段 ${stage.label}。回车查看详情`}
                  onClick={() => onOpen && onOpen(t)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      if (onOpen) onOpen(t)
                    }
                  }}>
                  <div className="flow-card__top">
                    <h4>{t.title}</h4>
                    <span className="flow-card__chev">›</span>
                  </div>
                  <div className="flow-card__meta">
                    <span className={`chip${p.p >= 3 ? ' chip--p3' : ''}${p.p === 2 ? ' chip--p2' : ''}`}>{p.label}</span>
                    {(() => {
                      const ds = depState(t, tasks)
                      if (!ds) return null
                      return (
                        <span
                          className={`chip dep-chip${ds.done ? ' dep-chip--ok' : ''}${ds.blocked ? ' dep-chip--blocked' : ''}`}
                          title={ds.blocked ? '前置任务已取消/失败，无法放行' : (ds.done ? '前置任务已完成' : '等待前置任务')}
                        >⛓ {ds.count}</span>
                      )
                    })()}
                    {t.target_agent_type && (
                      <span className="task__agent">
                        <span className="agent-mono" style={{ background: ac.bg, borderColor: ac.fg, color: ac.fg }}>{agMono(t.target_agent_type)}</span>
                      </span>
                    )}
                  </div>
                  <div className="flow-card__foot">
                    <span>{fmtDur(t.est_duration_min)}</span>
                    {next && (
                      <button className="btn btn--ghost flow-card__adv" onClick={e => { e.stopPropagation(); setAdvancing({ task: t, target: next }); setArtifact('') }}>
                        → {labelOf(next)}
                      </button>
                    )}
                    {['brainstorming','design','planning'].includes(t.stage) && (
                      <button className="btn btn--ghost btn--danger flow-card__cancel" onClick={e => { e.stopPropagation(); onCancel(t.id) }}>×</button>
                    )}
                  </div>
                </div>
              )
            })}
            {activeTasks.length === 0 && <div className="flow__empty">—</div>}
          </div>
        )}

        <div className="flow__legend">
          <span className="flow__legend-title">回溯</span>
          {BACKTRACKS.map((b, i) => (
            <span key={i} className="flow__legend-item">
              <b>{labelOf(b.from)}</b>
              <span className="flow__legend-arrow">↶</span>
              <b>{labelOf(b.to)}</b>
              <i>{b.note}</i>
            </span>
          ))}
        </div>
      </div>

      {advancing && (
        <div className="overlay" onClick={() => setAdvancing(null)}>
          <div className="modal" role="dialog" aria-modal="true" aria-label="推进阶段" onClick={e => e.stopPropagation()}>
            <div className="modal__head">
              <h3>推进到「{labelOf(advancing.target)}」</h3>
              <button className="modal__close" onClick={() => setAdvancing(null)} aria-label="关闭">×</button>
            </div>
            <div className="modal__body">
              <p style={{ fontSize: 13, color: 'var(--ink-dim)' }}>{advancing.task.title}</p>
              {artifactField(advancing.target) ? (
                <div className="field">
                  <label className="field__label">{advancing.target === 'done' ? '审查结论' : '产出物路径'}</label>
                  <input autoFocus value={artifact}
                    onChange={e => setArtifact(e.target.value)}
                    placeholder={artifactField(advancing.target).ph} />
                </div>
              ) : (
                <p style={{ fontSize: 13 }}>确认推进？</p>
              )}
              <div className="modal__foot">
                <button className="btn btn--ghost" onClick={() => setAdvancing(null)}>取消</button>
                <button className="btn btn--accent" onClick={confirmAdvance}>推进</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
