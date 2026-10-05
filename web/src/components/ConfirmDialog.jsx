import { useEffect, useState } from 'react'
import { subscribeConfirm } from '../confirm'

/** 通用确认弹窗（.overlay + .modal--sm，与 ListView 取消确认同款样式）。
 *  由 App 挂载一次；调用方走 confirm()（见 src/confirm.js）。 */
export default function ConfirmDialog() {
  const [data, setData] = useState(null)
  useEffect(() => subscribeConfirm(setData), [])

  useEffect(() => {
    if (!data) return undefined
    const onKey = (e) => { if (e.key === 'Escape') data.resolve(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [data])

  if (!data) return null
  const close = (v) => data.resolve(v)
  return (
    <div className="overlay" onClick={() => close(false)}>
      <div className="modal modal--sm" role="alertdialog" aria-modal="true"
           aria-label={data.title} onClick={(e) => e.stopPropagation()}>
        <div className="modal__head">
          <h3>{data.title}</h3>
          <button className="modal__close" onClick={() => close(false)} aria-label="关闭">×</button>
        </div>
        <div className="modal__body">
          <p style={{ whiteSpace: 'pre-line' }}>{data.message}</p>
        </div>
        <div className="modal__foot">
          <button className="btn btn--ghost" onClick={() => close(false)}>{data.cancelText}</button>
          <button className={`btn ${data.danger ? 'btn--danger' : 'btn--primary'}`}
                  autoFocus onClick={() => close(true)}>{data.okText}</button>
        </div>
      </div>
    </div>
  )
}
