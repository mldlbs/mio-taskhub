/**
 * 阶段推进对话框 —— 替代 window.prompt（Phase 2 C-4 / A3）。
 *
 * 原生 prompt 的问题：不可样式化、单行、无格式说明、无校验、无法展示任务上下文、
 * 移动端/键盘用户无焦点管理，且错误只能靠 alert 二次打断。
 *
 * 本组件负责：
 *  1. 展示「当前阶段 → 目标阶段」的上下文，说清这个阶段为什么必须提供产出物；
 *  2. 预填该阶段已有产出物（stageDocs.currentArtifact）；
 *  3. 行内校验与**浮层内**错误显示（不再由 App 层 ErrorBar 代劳，避免被遮罩盖住）；
 *  4. Esc 取消（走 overlayStack，只由栈顶响应）；打开即聚焦输入框。
 *
 * 2026-11-04 改版（用户反馈「不能让我去翻文件目录」）：
 *  5. done 阶段给「直接写结论 / 填文档路径」二选一——没有文档的任务不再被迫手填路径；
 *  6. 路径模式下把 GET /tasks/{id}/documents 的结果做成可点 chip（登记 + 工作区扫描），
 *     点一下自动填入；无候选时给出明确原因（未设置工作区 / 工作区无同类文档）。
 */
import { useEffect, useRef, useState } from 'react'
import { useOverlayStack } from '../lib/overlayStack'
import { STAGE_META } from '../constants'
import { api } from '../api'
import { REVIEW_PRESETS } from '../stageDocs'

export default function StageAdvanceDialog({ task, nextStage, spec, onCancel, onConfirm, busy, error }) {
  const [value, setValue] = useState(() => spec?.initial || '')
  const inputRef = useRef(null)
  const [localErr, setLocalErr] = useState('')
  // 候选文档：来自 GET /tasks/{id}/documents（登记 doc_paths + 工作区扫描），免去翻目录
  const [docs, setDocs] = useState([])
  const [docsLoaded, setDocsLoaded] = useState(false)
  // 模式：'text' 写结论 / 'path' 填路径（仅 acceptsText 的阶段可切换）
  const [mode, setMode] = useState(spec?.acceptsText ? 'text' : 'path')
  const modeTouched = useRef(false)

  useOverlayStack(onCancel)
  useEffect(() => { inputRef.current?.focus() }, [])

  useEffect(() => {
    let alive = true
    if (!task?.id) { setDocsLoaded(true); return }
    api.getTaskDocuments(task.id)
      .then(d => { if (alive) setDocs((d && d.documents) || []) })
      .catch(() => { /* 拉不到就退回手填 */ })
      .finally(() => { if (alive) setDocsLoaded(true) })
    return () => { alive = false }
  }, [task?.id])

  const wantKind = spec?.kind
  const pickMatched = docs.filter(d => d.exists !== false && d.kind === wantKind).slice(0, 6)
  // 只在没有同类候选时才兜底展示其他文档，避免一屏无关文件
  const pickOthers = pickMatched.length === 0
    ? docs.filter(d => d.exists !== false && d.kind !== wantKind).slice(0, 4)
    : []

  // 有同类文档时默认切到「填路径」（点一下就有），用户手动切过就不再干预
  useEffect(() => {
    if (!spec?.acceptsText || modeTouched.current || !docsLoaded) return
    if (pickMatched.length > 0) setMode('path')
  }, [docsLoaded, pickMatched.length, spec?.acceptsText])

  const switchMode = (m) => {
    modeTouched.current = true
    setMode(m)
    setLocalErr('')
    if (m === 'text') inputRef.current?.focus()
  }

  const pick = (path) => { setValue(path); setLocalErr('') }

  // 后端可能返回的校验错误由外部传入，显示在浮层内部
  const shownError = localErr || error || ''

  const submit = (e) => {
    e?.preventDefault?.()
    const v = value.trim()
    if (!v) {
      setLocalErr(mode === 'text' ? '请先写审查结论' : '请填写或点选一份产出物')
      inputRef.current?.focus()
      return
    }
    if (spec?.acceptsText && mode === 'text' && v.length < 8) {
      setLocalErr('审查结论至少需要 8 个字符，请写清结论依据')
      inputRef.current?.focus()
      return
    }
    if (mode === 'path' && !/[\\/]/.test(v) && !/\.(md|markdown|txt|pdf)$/i.test(v)) {
      setLocalErr('这看起来不像文档路径；如果你是在写结论，请切到「直接写审查结论」')
      inputRef.current?.focus()
      return
    }
    setLocalErr('')
    onConfirm(v, mode)
  }

  const cur = STAGE_META[task?.stage]?.label || task?.stage || '当前阶段'
  const next = STAGE_META[nextStage]?.label || nextStage
  const hasWorkspace = !!(task?.workspace || '').trim()

  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel() }}>
      <div className="modal stage-advance" role="dialog" aria-modal="true" aria-label={`推进到${next}`}>
        <div className="modal__head">
          <h3 className="modal__title">推进到「{next}」</h3>
          <button className="modal__close" onClick={onCancel} aria-label="关闭">×</button>
        </div>

        <div className="stage-advance__ctx">
          <span className="chip chip--muted">{cur}</span>
          <span className="stage-advance__arrow" aria-hidden="true">→</span>
          <span className="chip chip--ok-soft">{next}</span>
          <span className="stage-advance__task" title={task?.title}>{task?.title}</span>
        </div>

        <form onSubmit={submit}>
          <label className="stage-advance__label" htmlFor="stage-artifact">
            此阶段要求产出物：{spec?.label}
          </label>

          {spec?.acceptsText && (
            <div className="stage-advance__modes" role="tablist" aria-label="产出物方式">
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'text'}
                className={`stage-advance__mode${mode === 'text' ? ' is-on' : ''}`}
                onClick={() => switchMode('text')}
              >
                直接写审查结论
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'path'}
                className={`stage-advance__mode${mode === 'path' ? ' is-on' : ''}`}
                onClick={() => switchMode('path')}
              >
                填写 / 点选文档路径
              </button>
            </div>
          )}

          {spec?.acceptsText && mode === 'text' && (
            <div className="stage-advance__pick-row" style={{ marginBottom: 8 }}>
              {REVIEW_PRESETS.map((p, i) => (
                <button
                  key={i}
                  type="button"
                  className="stage-advance__pick"
                  onClick={() => { setValue(p.text); setLocalErr(''); inputRef.current?.focus() }}
                >
                  {p.label}
                </button>
              ))}
            </div>
          )}

          <textarea
            id="stage-artifact"
            ref={inputRef}
            className="stage-advance__input"
            rows={3}
            value={value}
            placeholder={mode === 'text'
              ? '点上方选项直接填，或自己写一句（≥8 字）'
              : (spec?.ph || '')}
            onChange={(e) => { setValue(e.target.value); setLocalErr('') }}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') submit(e)
            }}
          />
          <p className="stage-advance__hint">
            {mode === 'text'
              ? '点选项自动填好结论（可再改），或自己写（≥8 字）。Ctrl+Enter 提交。'
              : '从下方已有文档点选，或手填仓库内路径。Ctrl+Enter 提交。'}
          </p>

          {mode === 'path' && (
            <div className="stage-advance__picks">
              {pickMatched.length > 0 || pickOthers.length > 0 ? (
                <>
                  <div className="stage-advance__picks-title">已有文档（点选填入，免翻目录）</div>
                  {pickMatched.length > 0 && (
                    <div className="stage-advance__pick-row">
                      {pickMatched.map(d => (
                        <button
                          key={d.rel_path}
                          type="button"
                          className={`stage-advance__pick${value === d.rel_path ? ' is-on' : ''}`}
                          title={d.rel_path}
                          onClick={() => pick(d.rel_path)}
                        >
                          <span className="stage-advance__pick-kind">{d.kind}</span>
                          <span className="stage-advance__pick-path">{d.rel_path}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  {pickMatched.length === 0 && (
                    <>
                      <div className="stage-advance__picks-empty">
                        工作区里没找到「{wantKind}」类文档，下面是从其他类型里挑的，可借用。
                      </div>
                      <div className="stage-advance__pick-row" style={{ marginTop: 8 }}>
                        {pickOthers.map(d => (
                          <button
                            key={d.rel_path}
                            type="button"
                            className={`stage-advance__pick${value === d.rel_path ? ' is-on' : ''} is-dim`}
                            title={d.rel_path}
                            onClick={() => pick(d.rel_path)}
                          >
                            <span className="stage-advance__pick-kind">{d.kind}</span>
                            <span className="stage-advance__pick-path">{d.rel_path}</span>
                          </button>
                        ))}
                      </div>
                    </>
                  )}
                </>
              ) : docsLoaded ? (
                <div className="stage-advance__picks-empty">
                  {hasWorkspace
                    ? `工作区里没有可用的「${wantKind}」类文档。`
                    : '该任务未设置工作区，无法扫描已有文档。'}
                  {spec?.acceptsText
                    ? '建议切到「直接写审查结论」——一句话就能推进。'
                    : '请手填仓库内路径（需在 workspace 内，或填绝对路径）。'}
                </div>
              ) : null}
            </div>
          )}

          {shownError && <p className="stage-advance__err" role="alert">{shownError}</p>}

          <div className="stage-advance__actions">
            <button type="button" className="btn btn--ghost" onClick={onCancel}>取消</button>
            <button type="submit" className="btn btn--primary" disabled={busy}>
              {busy ? '推进中…' : '确认推进'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
