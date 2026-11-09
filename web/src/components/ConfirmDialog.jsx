import { useEffect, useState } from 'react'
import { subscribeConfirm } from '../confirm'
import { useOverlayStack } from '../lib/overlayStack'

/** 通用确认弹窗（.overlay + .modal--sm，与 ListView 取消确认同款样式）。
 *  由 App 挂载一次；调用方走 confirm()（见 src/confirm.js）。
 *
 *  Phase 2 B1：危险操作的默认焦点必须落在「取消」上——原来 autoFocus 在确认键，
 *  用户按 Enter 会直接执行不可逆操作。现在只有 danger !== true 时才聚焦确认键。
 */
export default function ConfirmDialog() {
  const [data, setData] = useState(null)
  useEffect(() => subscribeConfirm(setData), [])

  // Esc = 取消，且只由栈顶响应（确认弹窗永远是最顶层）
  useOverlayStack(() => data && data.resolve(false), !!data)

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
          <button className={`btn btn--ghost${data.danger ? ' confirm-default' : ''}`}
                  autoFocus={!!data.danger} onClick={() => close(false)}>{data.cancelText}</button>
          <button className={`btn ${data.danger ? 'btn--danger' : 'btn--primary'}`}
                  autoFocus={!data.danger} onClick={() => close(true)}>{data.okText}</button>
        </div>
      </div>
    </div>
  )
}
