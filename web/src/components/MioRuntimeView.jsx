import { useEffect, useState } from 'react'
import { api } from '../api'

/** Mio Agent Runtime 只读面板（低耦合：数据来自 MIO_HOME，不写入、不改任务/文档数据）。 */

const fmtBytes = (n) => n == null ? '' : n < 1024 ? `${n} B`
  : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`

const fmtTime = (t) => {
  if (!t) return '—'
  const d = typeof t === 'number' ? new Date(t * 1000) : new Date(t)
  return isNaN(d.getTime()) ? String(t) : d.toLocaleString()
}

const clip = (s, n = 200) => {
  const str = s == null ? '' : String(s)
  return str.length > n ? str.slice(0, n) + '…' : str
}

export default function MioRuntimeView() {
  const [st, setSt] = useState(null)
  const [traces, setTraces] = useState([])
  const [mem, setMem] = useState([])
  const [crea, setCrea] = useState(null)
  const [err, setErr] = useState(null)

  async function load() {
    try {
      const [s, t, m, c] = await Promise.all([
        api.mioStatus(), api.mioTraces(15), api.mioMemory(15), api.mioCreativity(15),
      ])
      setSt(s)
      setTraces(t.items || [])
      setMem(m.items || [])
      setCrea(c)
      setErr(null)
    } catch (e) {
      setErr(e?.message || String(e))
    }
  }

  useEffect(() => { load() }, [])

  if (err) return <div className="mio-view"><p className="detail-muted">加载失败：{err}</p></div>
  if (!st) return <div className="mio-view"><p className="detail-muted">加载中…</p></div>

  if (!st.available) {
    return (
      <div className="mio-view">
        <h2 className="mio-title">Mio 运行时</h2>
        <p className="detail-muted">
          未检测到 MIO_HOME（<span className="mono">{st.home}</span>）。安装并初始化
          <span className="mono"> mio-agent-runtime </span>后此处会自动显示。
        </p>
      </div>
    )
  }

  const agents = Object.keys(st.config?.agents || {})

  return (
    <div className="mio-view">
      <div className="mio-head">
        <h2 className="mio-title">Mio 运行时</h2>
        <span className="detail-muted mono">{st.home}</span>
        <button className="btn btn--ghost btn--xs" onClick={load}>刷新</button>
      </div>

      <div className="mio-cards">
        <div className="mio-card">
          <div className="mio-card__t">观察器</div>
          <div className={`mio-status${st.observer?.running ? ' is-ok' : ' is-off'}`}>
            {st.observer?.running ? `运行中 · pid ${st.observer.pid}` : '未运行'}
          </div>
          {!st.observer?.running && (
            <p className="detail-muted">启动：<span className="mono">mio observe --start</span></p>
          )}
        </div>
        <div className="mio-card">
          <div className="mio-card__t">CLI</div>
          <div className={`mio-status${st.cli?.available ? ' is-ok' : ' is-off'}`}>
            {st.cli?.available ? '可用' : '未找到'}
          </div>
          {st.cli?.path && <p className="detail-muted mono">{st.cli.path}</p>}
        </div>
        <div className="mio-card">
          <div className="mio-card__t">宿主接入</div>
          <div className="mio-agents">
            {agents.length === 0
              ? <span className="detail-muted">无</span>
              : agents.map(k => <span key={k} className="mio-chip mono">{k}</span>)}
          </div>
        </div>
      </div>

      <h3 className="mio-sec">数据文件</h3>
      <table className="mio-table">
        <thead>
          <tr><th>文件</th><th>有效记录</th><th>大小</th><th>最后更新</th></tr>
        </thead>
        <tbody>
          {(st.files || []).map(f => (
            <tr key={f.name} className={f.exists ? '' : 'is-missing'}>
              <td className="mono">{f.name}</td>
              <td>{f.exists ? f.records : '—'}</td>
              <td>{fmtBytes(f.bytes)}</td>
              <td>{fmtTime(f.mtime)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="mio-cols">
        <div className="mio-col">
          <h3 className="mio-sec">最近 Trace</h3>
          <ul className="mio-list">
            {traces.length === 0 && <li className="detail-muted">无</li>}
            {traces.map((t, i) => (
              <li key={i} className="mio-item">
                <div className="mio-item__head">
                  <span className="mono mio-tag">{t.event_type || t.type || '—'}</span>
                  {t.outcome && <span className={`mio-outcome mio-outcome--${t.outcome}`}>{t.outcome}</span>}
                  <span className="detail-muted mono">{t.agent || t.project || ''}</span>
                </div>
                <div className="mio-snippet">
                  {clip(t.payload ? JSON.stringify(t.payload) : (t.summary || t.content || ''))}
                </div>
              </li>
            ))}
          </ul>
        </div>
        <div className="mio-col">
          <h3 className="mio-sec">最近记忆</h3>
          <ul className="mio-list">
            {mem.length === 0 && <li className="detail-muted">无</li>}
            {mem.map((m, i) => (
              <li key={i} className="mio-item">
                <div className="mio-item__head">
                  <span className="mono mio-tag">{m.kind || '—'}</span>
                  <span className="detail-muted mono">{m.project || ''}</span>
                </div>
                <div className="mio-snippet">{clip(m.content)}</div>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {crea?.available && (crea.items?.length > 0 || crea.status?.hypotheses > 0) && (
        <>
          <h3 className="mio-sec">
            创意假设（Mio）
            {crea.status && (
              <span className="detail-muted mono" style={{ marginLeft: 10, fontSize: 11 }}>
                共 {crea.status.hypotheses ?? 0} · 活跃 {crea.status.active ?? 0} ·
                已验证 {crea.status.validated ?? 0} · 已拒 {crea.status.rejected ?? 0}
              </span>
            )}
          </h3>
          <ul className="mio-list">
            {(crea.items || []).map((h, i) => (
              <li key={h.id || i} className="mio-item">
                <div className="mio-item__head">
                  <span className="mio-hypo-title">{h.title || '(无标题)'}</span>
                  {h.status && <span className="mio-tag">{h.status}</span>}
                  <span className="mio-scores">
                    <span title="novelty">N {h.novelty ?? '—'}</span>
                    <span title="feasibility">F {h.feasibility ?? '—'}</span>
                    <span title="impact">I {h.impact ?? '—'}</span>
                    {h.score != null && <span className="mio-score-total">Σ {h.score}</span>}
                  </span>
                </div>
                <div className="mio-snippet">{clip(h.idea || h.description)}</div>
              </li>
            ))}
            {(crea.items || []).length === 0 && <li className="detail-muted">无</li>}
          </ul>
        </>
      )}

      <p className="detail-muted mio-foot">
        只读视图：数据来自 MIO_HOME（由 mio-agent-runtime 维护），本页不写入、不改动任务/文档数据。
      </p>
    </div>
  )
}
