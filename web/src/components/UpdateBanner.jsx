/** 顶部更新提示条：有新版本时展示并支持一键更新。 */
import { useEffect, useState } from 'react'
import { api } from '../api'

export default function UpdateBanner({ eventTick }) {
  const [st, setSt] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [hint, setHint] = useState('')

  async function refresh() {
    try { setSt(await api.updateStatus()); setErr('') } catch { /* ignore */ }
  }

  useEffect(() => { refresh() }, [eventTick])

  if (!st) return null
  const state = st.state
  if (!['available', 'downloading', 'ready', 'needs_manual', 'failed'].includes(state)) return null

  async function onPrimary() {
    setBusy(true)
    try {
      if (state === 'available') {
        try { await api.updateDownload() }
        catch (e) { setErr(`下载失败：${e?.message || e}`) }
        await refresh()
      } else if (state === 'ready') {
        try { await api.updateApply() }
        catch { setHint('正在重启应用…') }
      }
    } finally { setBusy(false) }
  }

  async function onDismiss() {
    setBusy(true)
    try {
      try { await api.updateDismiss() }
      catch (e) { setErr(`操作失败：${e?.message || e}`) }
      await refresh()
    } finally { setBusy(false) }
  }

  const label = {
    available: `发现新版本 v${st.latest}`,
    downloading: `正在下载 ${st.progress || 0}%`,
    ready: `已下载 v${st.latest}，重启应用更新`,
    needs_manual: `v${st.latest} 需手动更新（跨代不兼容）`,
    failed: `更新失败：${st.error || '见 apply.log'}`,
  }[state]

  return (
    <div className="update-banner" role="status" aria-live="polite">
      <span className="update-banner__text">{label}</span>
      {hint && <span>{hint}</span>}
      {err && <span className="update-banner__err">{err}</span>}
      {state === 'available' && (
        <button className="btn btn--primary" disabled={busy} onClick={onPrimary}>立即更新</button>
      )}
      {state === 'ready' && (
        <button className="btn btn--primary" disabled={busy} onClick={onPrimary}>重启并更新</button>
      )}
      {state === 'available' && (
        <button className="btn btn--ghost" disabled={busy} onClick={onDismiss}>忽略此版本</button>
      )}
    </div>
  )
}
