/** 顶部更新状态条：始终展示版本与状态，并支持手动「检查更新」与一键更新。 */
import { useEffect, useState } from 'react'
import { api } from '../api'

const ACTIONABLE = ['available', 'downloading', 'ready', 'needs_manual', 'failed']

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
  const current = st.current

  async function onCheck() {
    setBusy(true)
    setHint('')
    try {
      await api.updateCheck()
      await refresh()
    } catch (e) {
      setErr(`检查失败：${e?.message || e}`)
    } finally { setBusy(false) }
  }

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
      } else if (state === 'failed') {
        await onCheck()
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
    checking: '正在检查更新…',
    up_to_date: `已是最新版本 v${current}`,
    available: `发现新版本 v${st.latest}`,
    downloading: `正在下载 ${st.progress || 0}%`,
    ready: `已下载 v${st.latest}，重启应用更新`,
    needs_manual: `v${st.latest} 需手动更新（跨代不兼容）`,
    dismissed: `新版本 v${st.latest} 已忽略`,
    check_failed: `检查更新失败${st.error ? '：' + st.error : ''}`,
    failed: `更新失败：${st.error || '见 apply.log'}`,
    idle: `当前版本 v${current}`,
  }[state] || `当前版本 v${current}`

  const tone = state === 'available' || state === 'ready' ? 'is-active'
    : state === 'failed' || state === 'check_failed' ? 'is-error'
    : ''

  return (
    <div className={`update-banner ${tone}`} role="status" aria-live="polite">
      <span className="update-banner__text">{label}</span>
      {hint && <span>{hint}</span>}
      {err && <span className="update-banner__err">{err}</span>}

      {state === 'available' && (
        <button className="btn btn--primary" disabled={busy} onClick={onPrimary}>立即更新</button>
      )}
      {state === 'ready' && (
        <button className="btn btn--primary" disabled={busy} onClick={onPrimary}>重启并更新</button>
      )}
      {state === 'failed' && (
        <button className="btn btn--primary" disabled={busy} onClick={onPrimary}>重试</button>
      )}
      {ACTIONABLE.includes(state) && state !== 'downloading' && (
        <button className="btn btn--ghost" disabled={busy} onClick={onDismiss}>忽略此版本</button>
      )}

      <button
        className="btn btn--ghost update-banner__check"
        disabled={busy || state === 'downloading' || state === 'checking'}
        onClick={onCheck}
        title="立即向更新源查询最新版本"
      >
        {state === 'checking' ? '检查中…' : '检查更新'}
      </button>
    </div>
  )
}
