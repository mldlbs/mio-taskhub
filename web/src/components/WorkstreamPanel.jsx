import { useEffect, useState } from 'react'
import { api } from '../api'
import { STAGE_META, fmtDur } from '../constants'

export default function WorkstreamPanel({ task, onOpen }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let dead = false
    setData(null)
    setError(null)
    api.getWorkstream(task.id)
      .then(d => { if (!dead) setData(d) })
      .catch(e => { if (!dead) setError(e.message || String(e)) })
    return () => { dead = true }
  }, [task.id])

  if (error) return <p className="detail-muted">工作流加载失败：{error}</p>
  if (!data) return <p className="detail-muted">加载工作流…</p>

  const nodes = data.nodes || []
  if (!nodes.length) {
    const cancelled = data.stats?.cancelled || 0
    return (
      <div className="ws-empty">
        <p className="detail-muted">
          {cancelled > 0
            ? `该工作流有 ${cancelled} 个已取消任务，当前没有进行中的成员。`
            : <>这个任务还没有工作流成员。建平级任务时加
              {' '}<code>labels=["part-of:{task.id}"]</code>{' '}
              并用 <code>depends_on</code> 连依赖，就会出现在这里。</>}
        </p>
      </div>
    )
  }

  const byId = Object.fromEntries(nodes.map(n => [n.id, n]))
  const blocked = new Set((data.blocked || []).map(b => b.id))
  const st = data.stats || {}

  return (
    <div className="ws">
      <div className="ws-stats">
        <span><b>{st.done || 0}/{st.total || 0}</b> 完成</span>
        <span className={st.blocked ? 'ws-stat--warn' : ''}>
          <b>{st.blocked || 0}</b> 阻塞
        </span>
        <span>预估 <b>{fmtDur(st.estimate_min)}</b></span>
        <span>关键路径 <b>{st.critical_path || 0}</b> 步</span>
        {data.owner_title && <span className="ws-owner">组：{data.owner_title}</span>}
      </div>

      {data.cycle && (
        <div className="ws-alert">检测到依赖环：{JSON.stringify(data.cycle_path || [])}</div>
      )}

      <div className="ws-layers">
        {(data.layers || []).map((layer, i) => (
          <div className="ws-layer" key={i}>
            <div className="ws-layer__head">第 {i + 1} 层</div>
            {layer.map(id => {
              const n = byId[id]
              if (!n) return null
              const meta = STAGE_META[n.stage] || { label: n.stage, tone: 'dim' }
              return (
                <button
                  key={id}
                  className={`ws-card${blocked.has(id) ? ' is-blocked' : ''}`}
                  onClick={() => onOpen(id)}
                  title={n.title}
                >
                  <span className="ws-card__title">{n.title}</span>
                  <span className="ws-card__meta">
                    <span className={`chip chip--${meta.tone}`}>{meta.label}</span>
                    <span>{fmtDur(n.est_duration_min)}</span>
                    {blocked.has(id) && <span className="ws-card__blocked">阻塞</span>}
                  </span>
                  {(n.depends_on || []).length > 0 && (
                    <span className="ws-card__deps">
                      {(n.depends_on || []).map(d => (
                        <span key={d} className="ws-dep">{byId[d]?.title || d}</span>
                      ))}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        ))}
      </div>

      {(data.external || []).length > 0 && (
        <div className="ws-extra">
          <span className="ws-extra__label">外部前置</span>
          {data.external.map((e, i) => (
            <span key={i} className={`ws-dep${e.closed ? '' : ' is-open'}`}>
              {e.from_title || e.from} → {byId[e.to]?.title || e.to}
            </span>
          ))}
        </div>
      )}
      {(data.missing || []).length > 0 && (
        <div className="ws-extra ws-extra--bad">
          <span className="ws-extra__label">缺失依赖</span>
          {data.missing.map((e, i) => (
            <span key={i} className="ws-dep is-open">{e.from} → {e.to}</span>
          ))}
        </div>
      )}
    </div>
  )
}
