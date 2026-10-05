import { useState, useEffect } from 'react'
import { api } from '../api'

const LEVEL_META = {
  ok:        { label: '正常', tone: 'ok',    color: '#22c55e' },
  degraded:  { label: '降级', tone: 'warn',  color: '#f59e0b' },
  critical:  { label: '严重', tone: 'danger', color: '#ef4444' },
  unknown:   { label: '未知', tone: 'dim',   color: '#6b7280' },
}

export default function GateStatusCard({ onForceGenerate }) {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState(null)
  const [forceLoading, setForceLoading] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const res = await api.gateStatus()
      setStatus(res)
      setErr(null)
    } catch (e) {
      setErr(e.message || '加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    const t = setInterval(load, 30000) // 30s 轮询
    return () => clearInterval(t)
  }, [])

  const handleForceGenerate = async () => {
    if (!confirm('强制触发每日灵感生成（绕过闸门，留痕审计）。确定？', { title: '强制生成', danger: true })) return
    setForceLoading(true)
    try {
      await api.forceGenerate('1271eac5', '面板手动触发')
      await load()
    } catch (e) {
      alert('强制生成失败：' + e.message)
    } finally {
      setForceLoading(false)
    }
  }

  if (loading && !status) {
    return (
      <div className="gate-status skeleton">
        <div className="gate-status__head-skel"></div>
        <div className="gate-status__bars-skel">
          <div className="skel-bar"></div>
          <div className="skel-bar"></div>
          <div className="skel-bar"></div>
          <div className="skel-bar"></div>
        </div>
      </div>
    )
  }

  if (err && !status) {
    return (
      <div className="gate-status gate-status--error" role="alert">
        <span className="gate-status__err">▲ 加载失败：{err}</span>
        <button className="btn btn--ghost btn--sm" onClick={load}>重试</button>
      </div>
    )
  }

  const meta = status ? (LEVEL_META[status.level] || LEVEL_META.unknown) : LEVEL_META.unknown

  return (
    <div className={`gate-status gate-status--${status?.level || 'unknown'}`} role="region" aria-label="生产闸门状态">
      <div className="gate-status__head">
        <div className="gate-status__level">
          <span className="gate-status__dot" style={{ background: meta.color }} />
          <span className="gate-status__label">生产闸门</span>
          <span className={`badge badge--${meta.tone}`}>{meta.label}</span>
        </div>
        {onForceGenerate && (
          <button className="btn btn--ghost btn--sm gate-status__force"
                  onClick={handleForceGenerate}
                  disabled={forceLoading}
                  title="强制触发生成（绕过闸门，留痕审计）">
            {forceLoading ? '生成中…' : '⚡ 强制生成'}
          </button>
        )}
      </div>

      <div className="gate-status__metrics">
        <div className="gate-status__metric">
          <span className="gate-status__metric-label">加权总分</span>
          <span className="gate-status__metric-value mono">{status?.total_weight ?? '—'}</span>
        </div>
        <div className="gate-status__metric">
          <span className="gate-status__metric-label">INBOX</span>
          <span className="gate-status__metric-value mono">{status?.inbox_count ?? '—'}</span>
        </div>
        <div className="gate-status__metric">
          <span className="gate-status__metric-label">NEW</span>
          <span className="gate-status__metric-value mono">{status?.new_count ?? '—'}</span>
        </div>
        <div className="gate-status__metric">
          <span className="gate-status__metric-label">评审带宽</span>
          <span className="gate-status__metric-value mono">{status?.capacity_available ?? '—'} 槽</span>
        </div>
      </div>

      <div className="gate-status__thresholds">
        <span className="gate-status__thresh">软阈值 ≥ {status?.config?.soft_threshold ?? 500} → weekly</span>
        <span className="gate-status__thresh">硬阈值 ≥ {status?.config?.hard_threshold ?? 800} → 告警</span>
      </div>

      {status?.throughput_per_day != null && (
        <div className="gate-status__throughput">
          <span className="gate-status__metric-label">吞吐</span>
          <span className="gate-status__metric-value mono">{status.throughput_per_day} 评审/天</span>
        </div>
      )}
    </div>
  )
}

// 确认弹窗（复用 api.js 的 confirm 风格）
function confirm(message, { title = '确认', danger = false, okText = '确定' } = {}) {
  return new Promise((resolve) => {
    const overlay = document.createElement('div')
    overlay.className = 'confirm-overlay'
    overlay.innerHTML = `
      <div class="confirm-box${danger ? ' confirm-box--danger' : ''}">
        <h4>${title}</h4>
        <p>${message}</p>
        <div class="confirm-actions">
          <button class="btn btn--ghost" data-action="cancel">取消</button>
          <button class="btn btn--primary${danger ? ' btn--danger' : ''}" data-action="ok">${okText}</button>
        </div>
      </div>
    `
    document.body.appendChild(overlay)
    const clean = (v) => {
      overlay.remove()
      resolve(v)
    }
    overlay.querySelector('[data-action="ok"]').onclick = () => clean(true)
    overlay.querySelector('[data-action="cancel"]').onclick = () => clean(false)
    overlay.onclick = (e) => { if (e.target === overlay) clean(false) }
  })
}