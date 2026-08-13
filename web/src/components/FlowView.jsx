import { useState } from 'react'
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

export default function FlowView({ tasks, onOpen, onCancel, onAdvance }) {
  const [advancing, setAdvancing] = useState(null)
  const [artifact, setArtifact] = useState('')

  const byStage = Object.fromEntries(STAGES.map(s => [s.id, tasks.filter(t => t.stage === s.id)]))

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
      <div className="flow__track">
        {STAGES.map(stage => (
          <section key={stage.id} className="flow__lane">
            <header className="flow__head">
              <span className="flow__en">{stage.en}</span>
              <span className="flow__count">{byStage[stage.id].length}</span>
            </header>
            <div className="flow__label">{stage.label}</div>
            <div className="flow__body">
              {byStage[stage.id].map(t => {
                const p = prio(t.priority)
                const ac = t.target_agent_type && agColor(t.target_agent_type)
                const next = nextStage(t.stage)
                return (
                  <div key={t.id} className="flow-card" onClick={() => onOpen && onOpen(t)}>
                    <div className="flow-card__top">
                      <h4>{t.title}</h4>
                      <span className="flow-card__chev">›</span>
                    </div>
                    <div className="flow-card__meta">
                      <span className={`chip${p.p >= 3 ? ' chip--p3' : ''}${p.p === 2 ? ' chip--p2' : ''}`}>{p.label}</span>
                      {t.target_agent_type && (
                        <span className="task__agent">
                          <span className="agent-mono" style={{ background: ac.bg, borderColor: ac.fg, color: ac.fg }}>{agMono(t.target_agent_type)}</span>
                        </span>
                      )}
                      {t.spec_path && <span className="chip chip--doc" title={t.spec_path}>📄</span>}
                      {t.plan_path && <span className="chip chip--doc" title={t.plan_path}>📝</span>}
                    </div>
                    <div className="flow-card__foot">
                      <span>{fmtDur(t.est_duration_min)}</span>
                      {next && (
                        <button className="btn btn--ghost flow-card__adv" onClick={e => { e.stopPropagation(); setAdvancing({ task: t, target: next }); setArtifact('') }}>
                          → {STAGES.find(s => s.id === next)?.label}
                        </button>
                      )}
                      {['brainstorming','design','planning'].includes(t.stage) && (
                        <button className="btn btn--ghost btn--danger flow-card__cancel" onClick={e => { e.stopPropagation(); onCancel(t.id) }}>×</button>
                      )}
                    </div>
                  </div>
                )
              })}
              {byStage[stage.id].length === 0 && <div className="flow__empty">—</div>}
            </div>
          </section>
        ))}
      </div>

      {advancing && (
        <div className="overlay" onClick={() => setAdvancing(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal__head">
              <h3>推进到「{STAGES.find(s => s.id === advancing.target)?.label}」</h3>
              <button className="modal__close" onClick={() => setAdvancing(null)}>×</button>
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
