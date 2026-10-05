/** 设置面板：应用信息与软件更新。 */
import { useEffect, useState } from 'react'
import { api } from '../api'

const fmtUptime = (s) => {
  if (s == null) return '—'
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (d) return `${d} 天 ${h} 时`
  if (h) return `${h} 时 ${m} 分`
  return `${m} 分`
}

export default function SettingsView() {
  const [st, setSt] = useState(null)
  const [proc, setProc] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [hint, setHint] = useState('')

  async function refresh() {
    try {
      const [s, p] = await Promise.all([
        api.updateStatus().catch(() => null),
        api.processInfo().catch(() => null),
      ])
      if (s) setSt(s)
      if (p) setProc(p)
    } catch (e) { setErr(e?.message || String(e)) }
  }

  useEffect(() => { refresh() }, [])

  async function run(name, fn, onOk) {
    setBusy(name); setErr(''); setHint('')
    try {
      await fn()
      if (onOk) onOk()
      await refresh()
    } catch (e) {
      setErr(e?.message || String(e))
    } finally { setBusy('') }
  }

  const state = st?.state || 'idle'
  const label = {
    checking: '正在检查更新…',
    up_to_date: `已是最新版本（v${st?.current}）`,
    available: `发现新版本 v${st?.latest}`,
    downloading: `正在下载 ${st?.progress || 0}%`,
    ready: `已下载 v${st?.latest}，可重启更新`,
    needs_manual: `v${st?.latest} 需手动更新（跨代不兼容）`,
    dismissed: `新版本 v${st?.latest} 已忽略`,
    check_failed: `检查更新失败${st?.error ? '：' + st.error : ''}`,
    failed: `更新失败：${st?.error || '见 apply.log'}`,
  }[state] || `当前版本 v${st?.current || '—'}`

  return (
    <div className="mio-view settings-view">
      <div className="mio-head">
        <h2 className="mio-title">设置</h2>
        <button className="btn btn--ghost btn--xs" onClick={refresh}>刷新</button>
      </div>

      {err && <p className="update-banner__err">操作失败：{err}</p>}
      {hint && <p className="detail-muted">{hint}</p>}

      <h3 className="mio-sec">软件更新</h3>
      <div className="mio-cards">
        <div className="mio-card">
          <div className="mio-card__t">当前版本</div>
          <div className="mio-status is-ok mono">v{st?.current || '—'}</div>
        </div>
        <div className="mio-card">
          <div className="mio-card__t">状态</div>
          <div className="mio-status">{label}</div>
        </div>
        <div className="mio-card">
          <div className="mio-card__t">更新通道</div>
          <div className="mio-status mono">stable</div>
        </div>
      </div>

      <div className="settings-actions">
        <button className="btn btn--primary" disabled={!!busy || state === 'checking' || state === 'downloading'}
                onClick={() => run('check', () => api.updateCheck())}>
          {state === 'checking' ? '检查中…' : '检查更新'}
        </button>

        {state === 'available' && (
          <button className="btn btn--primary" disabled={!!busy}
                  onClick={() => run('download', () => api.updateDownload())}>
            下载 v{st?.latest}
          </button>
        )}
        {state === 'ready' && (
          <button className="btn btn--primary" disabled={!!busy}
                  onClick={() => run('apply', () => api.updateApply(),
                                     () => setHint('正在重启应用…'))}>
            重启并更新
          </button>
        )}
        {state === 'failed' && (
          <button className="btn btn--primary" disabled={!!busy}
                  onClick={() => run('download', () => api.updateDownload())}>
            重试下载
          </button>
        )}
        {['available', 'ready', 'needs_manual'].includes(state) && (
          <button className="btn btn--ghost" disabled={!!busy}
                  onClick={() => run('dismiss', () => api.updateDismiss())}>
            忽略此版本
          </button>
        )}
      </div>

      {st && st.notes && (
        <>
          <h3 className="mio-sec">版本说明</h3>
          <pre className="settings-notes">{st.notes}</pre>
        </>
      )}

      <h3 className="mio-sec">运行信息</h3>
      <table className="mio-table">
        <tbody>
          <tr><td>进程 PID</td><td className="mono">{proc?.pid ?? '—'}</td></tr>
          <tr><td>运行时长</td><td>{fmtUptime(proc?.uptime_seconds)}</td></tr>
          <tr><td>内存 (RSS)</td><td>{proc?.memory_rss_mb != null ? `${proc.memory_rss_mb} MB` : '—'}</td></tr>
          <tr><td>线程数</td><td>{proc?.threads ?? '—'}</td></tr>
        </tbody>
      </table>
    </div>
  )
}
