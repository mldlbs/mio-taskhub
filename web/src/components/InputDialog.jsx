/**
 * 输入对话框渲染器 —— promptInput() 的唯一宿主，由 App 挂载一次。
 * 与 ConfirmDialog 共用浮层栈：Esc 只由栈顶响应。
 */
import { useEffect, useRef, useState } from 'react'
import { subscribePrompt } from '../promptInput'
import { useOverlayStack } from '../lib/overlayStack'

export default function InputDialog() {
  const [req, setReq] = useState(null)
  const [value, setValue] = useState('')
  const [err, setErr] = useState('')
  const ref = useRef(null)

  useEffect(() => subscribePrompt((p) => {
    setReq(p)
    setValue(p?.defaultValue ?? '')
    setErr('')
  }), [])

  const close = (v) => { req?.resolve(v) }
  useOverlayStack(() => close(null), !!req)

  useEffect(() => {
    if (req) {
      const t = setTimeout(() => { ref.current?.focus(); ref.current?.select?.() }, 30)
      return () => clearTimeout(t)
    }
  }, [req])

  if (!req) return null

  const submit = (e) => {
    e?.preventDefault?.()
    const v = value.trim()
    if (req.required && !v) { setErr('内容不能为空'); ref.current?.focus(); return }
    close(v)
  }

  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) close(null) }}>
      <div className="modal input-dialog" role="dialog" aria-modal="true" aria-label={req.title}>
        <div className="modal__head">
          <h3 className="modal__title">{req.title}</h3>
          <button className="modal__close" onClick={() => close(null)} aria-label="关闭">×</button>
        </div>
        <form onSubmit={submit}>
          <label className="stage-advance__label" htmlFor="input-dialog-field">{req.message}</label>
          {req.multiline ? (
            <textarea
              id="input-dialog-field"
              ref={ref}
              className="stage-advance__input"
              rows={4}
              value={value}
              placeholder={req.placeholder}
              onChange={(e) => { setValue(e.target.value); setErr('') }}
              onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') submit(e) }}
            />
          ) : (
            <input
              id="input-dialog-field"
              ref={ref}
              className="stage-advance__input"
              value={value}
              placeholder={req.placeholder}
              onChange={(e) => { setValue(e.target.value); setErr('') }}
            />
          )}
          {req.hint && <p className="stage-advance__hint">{req.hint}</p>}
          {err && <p className="stage-advance__err" role="alert">{err}</p>}
          <div className="stage-advance__actions">
            <button type="button" className="btn btn--ghost" onClick={() => close(null)}>{req.cancelText}</button>
            <button type="submit" className="btn btn--primary">{req.okText}</button>
          </div>
        </form>
      </div>
    </div>
  )
}
