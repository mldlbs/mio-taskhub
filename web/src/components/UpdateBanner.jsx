/** 顶部更新提示条：仅在有新版可操作时出现（检查/设置在「设置」面板）。 */
import { useEffect, useState } from 'react'
import { api } from '../api'

const ACTIONABLE = ['available', 'downloading', 'ready', 'applying', 'done', 'needs_manual', 'failed']

export default function UpdateBanner({ eventTick, onOpenSettings }) {
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
  if (!ACTIONABLE.includes(state)) return null

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
    applying: '正在应用更新，即将自动重启…',
    done: '更新完成，正在重启…',
    needs_manual: `v${st.latest} 需手动更新（跨代不兼容）`,
    failed: `更新失败：${st.error || '见 apply.log'}`,
  }[state]

  const tone = state === 'available' || state === 'ready'
      || state === 'applying' || state === 'done' ? 'is-active'
    : state === 'failed' ? 'is-error' : ''

  return (
    <div className={`update-banner ${tone}`} role="status" aria-live="polite">
      <span className="update-banner__text">{label}</span>
      {hint && <span>{hint}</span>}
      {err && <span className="update-banner__err">{err}</span>}
      {state === 'available' && (
        <button className="btn btn--primary btn--sm" disabled={busy} onClick={onPrimary}>立即更新</button>
      )}
      {state === 'ready' && (
        <button className="btn btn--primary btn--sm" disabled={busy} onClick={onPrimary}>重启并更新</button>
      )}
      {state === 'available' && (
        <button className="btn btn--ghost btn--sm" disabled={busy} onClick={onDismiss}>忽略</button>
      )}
      <button className="btn btn--ghost btn--sm" disabled={busy}
              onClick={() => onOpenSettings && onOpenSettings()} title="在设置面板中查看">
        设置
      </button>
    </div>
  )
}
