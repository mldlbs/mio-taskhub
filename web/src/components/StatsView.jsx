import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { COMPOSITE_LABELS, LANES, STAGES, parseUtc } from '../constants'
import { Skeleton } from './Skeleton'

// 语义色唯一来源：ledger.css 的 CSS 变量（phase1 C3）。
// JS 只引用 var()，不写字面色值 —— light / 高对比档自动跟随。
const TONE_COLORS = {
  live: 'var(--live)',
  ok: 'var(--ok)',
  'ok-soft': 'color-mix(in srgb, var(--ok) 62%, var(--ink-faint))',
  warn: 'var(--warn)',
  danger: 'var(--danger)',
  info: 'var(--info)',
  neutral: 'var(--neutral)',
  muted: 'var(--ink-ghost)',
  dim: 'var(--ink-faint)',
}

/* ---------- 展示组件 ---------- */

// KPI 卡：数字默认中性，只有「异常结论」才上色（R2 V1）。
// sub 副行给上下文，让数字可解释，而不是孤立的大数。
function MetricCard({ label, value, tone, sub }) {
  return (
    <div className={`stats-metric stats-metric--${tone || 'dim'}`}>
      <span className="stats-metric__value">{value}</span>
      <span className="stats-metric__label">{label}</span>
      {sub && <span className="stats-metric__sub">{sub}</span>}
    </div>
  )
}

// 状态分布：单行堆叠条 + 图例。比 N 条独立横条紧凑得多，
// 且能一眼看出各状态占比；零值项不进条，但保留在图例里。
function StateStack({ entries }) {
  const total = entries.reduce((s, e) => s + e.value, 0)
  const visible = entries.filter(e => e.value > 0)
  return (
    <div className="stats-stack">
      <div className="stats-stack__bar" role="img"
           aria-label={`状态分布，共 ${total} 个任务`}>
        {visible.length === 0 && <div className="stats-stack__empty">暂无任务</div>}
        {visible.map(e => (
          <div key={e.label} className="stats-stack__seg"
               style={{ flexGrow: e.value, background: TONE_COLORS[e.tone] || 'var(--ink-faint)' }}
               title={`${e.label} ${e.value}（${total ? Math.round((e.value / total) * 100) : 0}%）`} />
        ))}
      </div>
      <ul className="stats-stack__legend">
        {entries.map(e => (
          <li key={e.label}>
            <i style={{ background: TONE_COLORS[e.tone] || 'var(--ink-faint)' }} />
            <span className="stats-stack__name">{e.label}</span>
            <span className="stats-stack__num mono">{e.value}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

// 离散统计项：label + 数字的小胶囊。用于阶段 / 复合状态 / 事件类型这类
// 「类目多、单值小」的数据 —— 全宽横条在这种数据下会变成一排灰线。
function Pill({ label, value, tone }) {
  return (
    <span className={`stats-pill stats-pill--${tone || 'dim'}`}>
      <span className="stats-pill__label">{label}</span>
      <span className="stats-pill__num mono">{value}</span>
    </span>
  )
}

// Catmull-Rom 转三次贝塞尔：硬折线在稀疏数据上是「三角形山峰」，
// 平滑后才像一条趋势。y 值 clamp 在绘图区内，避免贴底的 0 值序列被平滑出下凹。
function smoothPath(pts, minY, maxY) {
  const cl = (v) => Math.max(minY, Math.min(maxY, v))
  if (pts.length < 3) return pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${cl(p[1]).toFixed(1)}`).join(' ')
  let d = `M${pts[0][0].toFixed(1)},${cl(pts[0][1]).toFixed(1)}`
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)]
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const p3 = pts[Math.min(pts.length - 1, i + 2)]
    d += ` C${(p1[0] + (p2[0] - p0[0]) / 6).toFixed(1)},${cl(p1[1] + (p2[1] - p0[1]) / 6).toFixed(1)}`
    d += ` ${(p2[0] - (p3[0] - p1[0]) / 6).toFixed(1)},${cl(p2[1] - (p3[1] - p1[1]) / 6).toFixed(1)}`
    d += ` ${p2[0].toFixed(1)},${cl(p2[1]).toFixed(1)}`
  }
  return d
}

function TrendChart({ data, outliers, days }) {
  if (!data || data.length === 0) return null

  const W = 640
  const H = 220
  const PAD = { top: 22, right: 20, bottom: 30, left: 40 }
  const iw = W - PAD.left - PAD.right
  const ih = H - PAD.top - PAD.bottom

  const maxVal = Math.max(1, ...data.flatMap(d => [d.created, d.completed, d.failed]))
  const n = data.length
  // 失败线只有真的有失败时才画：全 0 的失败线是一条横穿图表的红线，纯噪音
  const hasFailed = data.some(d => d.failed > 0)

  // 刻度取整到「好看的步长」（批量日已在数据层剔除，这里就是纯线性刻度）
  const rough = Math.max(1, maxVal) / 4
  const pow = Math.pow(10, Math.floor(Math.log10(rough)))
  const niceStep = [1, 2, 5, 10].map(m => m * pow).find(s => s >= rough) || pow * 10
  const tickMax = Math.max(1, Math.ceil(maxVal / niceStep) * niceStep)
  const ticks = Array.from({ length: Math.round(tickMax / niceStep) + 1 }, (_, i) => i * niceStep)

  const x = (i) => PAD.left + (n === 1 ? iw / 2 : (i / (n - 1)) * iw)
  const y = (v) => PAD.top + ih - (v / tickMax) * ih
  const axisY = PAD.top + ih

  const pts = (key) => data.map((d, i) => [x(i), y(d[key])])
  const line = (key) => smoothPath(pts(key), PAD.top, axisY)

  // 「创建」线下方渐变面积 + 平滑：给主系列体量感
  const area = `${line('created')} L${x(n - 1).toFixed(1)},${axisY.toFixed(1)} L${x(0).toFixed(1)},${axisY.toFixed(1)} Z`

  const colors = { created: 'var(--accent)', completed: 'var(--ok)', failed: 'var(--danger)' }

  // x 轴标签按密度采样：7 天全标，30 天隔 5 天，90 天隔 15 天；首尾必标
  const labelEvery = n <= 10 ? 1 : n <= 35 ? 5 : 15
  const showLabel = (i) => i % labelEvery === 0 || i === n - 1
  // hover 热区宽度随数据密度收缩；点只在稀疏时画（密了是一串噪点）
  const hoverW = Math.min(24, iw / n)
  const showDots = n <= 20

  return (
    <div className="stats-trend">
      <svg viewBox={`0 0 ${W} ${H}`} className="stats-trend__svg">
        <defs>
          <linearGradient id="gradCreated" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity="0.26" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)}
                  stroke={v === 0 ? 'var(--ink-ghost)' : 'var(--line)'}
                  strokeWidth={v === 0 ? 1 : 0.5} opacity={v === 0 ? 0.5 : 1} />
            <text x={PAD.left - 6} y={y(v) + 4} textAnchor="end" fontSize="9" fill="var(--ink-faint)">{v}</text>
          </g>
        ))}
        {data.map((d, i) => (
          <g key={i}>
            <rect x={x(i) - hoverW / 2} y={PAD.top} width={hoverW} height={ih} fill="transparent">
              <title>{`${d.date}: 创建 ${d.created} · 完成 ${d.completed} · 失败 ${d.failed}`}</title>
            </rect>
            {showLabel(i) && (
              <text x={x(i)} y={H - 10} textAnchor="middle" fontSize="9" fill="var(--ink-faint)">{d.date.slice(5)}</text>
            )}
          </g>
        ))}
        <path d={area} fill="url(#gradCreated)" />
        <path d={line('created')} fill="none" stroke={colors.created} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        <path d={line('completed')} fill="none" stroke={colors.completed} strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" opacity="0.9" />
        {hasFailed && (
          <path d={line('failed')} fill="none" stroke={colors.failed} strokeWidth="1.5"
                strokeDasharray="4 3" strokeLinecap="round" opacity="0.85" />
        )}
        {showDots && data.map((d, i) => (
          ['created', 'completed'].map((key) => (
            <circle key={`${key}-${i}`} cx={x(i)} cy={y(d[key])} r="3" fill={colors[key]} stroke="var(--bg)" strokeWidth="1.5" />
          ))
        ))}
        {hasFailed && data.map((d, i) =>
          d.failed > 0 ? (
            <circle key={`failed-${i}`} cx={x(i)} cy={y(d.failed)} r={showDots ? 2.5 : 2} fill={colors.failed} stroke="var(--bg)" strokeWidth="1.2" />
          ) : null
        )}
      </svg>
      <div className="stats-trend__legend">
        <span><i style={{ background: colors.created }} />创建</span>
        <span><i style={{ background: colors.completed }} />完成</span>
        {hasFailed && <span><i style={{ background: 'transparent', borderTop: `2px dashed ${colors.failed}`, height: 0, width: 12 }} />失败</span>}
        <span className="stats-trend__range-hint">近 {days} 日</span>
      </div>
      {outliers?.length > 0 && (
        <p className="stats-trend__footnote">
          已剔除批量日 {outliers.map(o => `${o.date.slice(5)}（${o.created} 任务）`).join('、')}——非日常节奏，不参与刻度
        </p>
      )}
    </div>
  )
}

/* ---------- 主视图 ---------- */

export default function StatsView() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [metrics, setMetrics] = useState(null)
  const [trendTasks, setTrendTasks] = useState(null)
  const [trendDays, setTrendDays] = useState(30)
  const [report, setReport] = useState(null)

  useEffect(() => {
    api.statsOverview().then(setData).catch(() => setData(null)).finally(() => setLoading(false))
    api.metrics().then(setMetrics).catch(() => {})
    api.observabilityReport().then(setReport).catch(() => {})
    // 任务列表只拉一次；趋势按 trendDays 在前端重新聚合，切范围不再请求
    api.listTasks({ cancelled: 'true' }).then(setTrendTasks).catch(() => {})
  }, [])

  // 近 N 日创建/完成/失败聚合。
  // 批量日剔除：某天一次性批量创建的任务（如 2026-10-19 历史 creativity 实验
  // 留下的 1483 个「想法评审」）会把 y 轴尺度拉爆，日常节奏全部压平。
  // 单日创建数 > max(30, 次大值×3) 视为批量事件，剔除出趋势序列并在脚注说明。
  const { series: trendSeries, outliers } = useMemo(() => {
    if (!trendTasks) return { series: null, outliers: [] }
    const days = []
    for (let i = trendDays - 1; i >= 0; i--) {
      const d = new Date()
      d.setDate(d.getDate() - i)
      days.push(d.toISOString().slice(0, 10))
    }
    const agg = days.map((date) => ({ date, created: 0, completed: 0, failed: 0 }))
    const idx = Object.fromEntries(days.map((d, i) => [d, i]))
    for (const t of trendTasks) {
      const cd = t.created_at ? parseUtc(t.created_at)?.toISOString().slice(0, 10) : null
      if (cd && cd in idx) agg[idx[cd]].created++
      const dd = t.completed_at ? parseUtc(t.completed_at)?.toISOString().slice(0, 10) : null
      if (dd && dd in idx) {
        if (t.state === 'completed') agg[idx[dd]].completed++
        if (t.state === 'failed') agg[idx[dd]].failed++
      }
    }
    const createdSorted = agg.map(d => d.created).sort((a, b) => b - a)
    const threshold = Math.max(30, (createdSorted[1] || 0) * 3)
    const outliers = agg.filter(d => d.created > threshold)
    return {
      series: outliers.length ? agg.filter(d => d.created <= threshold) : agg,
      outliers: outliers.map(o => ({ date: o.date, created: o.created })),
    }
  }, [trendTasks, trendDays])

  if (loading) return (
    <div className="stats-view">
      <div className="stats-header">
        <h2>统计</h2>
        <span className="stats-header__total">加载中…</span>
      </div>
      <div className="stats-bars">
        <Skeleton variant="box" height="60px" />
        <Skeleton variant="box" height="60px" />
        <Skeleton variant="box" height="60px" />
      </div>
    </div>
  )
  if (!data) return <div className="stats-loading">无法加载统计</div>

  const { composite_counts, by_state, by_stage, event_by_type, total_tasks, task_events_count } = data

  const compositeEntries = Object.entries(composite_counts)
    .map(([key, count]) => {
      const [state, stage] = key.split(',')
      const meta = COMPOSITE_LABELS[key] || { label: key, tone: 'dim' }
      return { key, state, stage, count, ...meta }
    })
    .sort((a, b) => b.count - a.count)

  const scope = report?.tasks?.scope
  const scopeState = Object.fromEntries(
    Object.entries(scope?.by_state || by_state || {})
      .map(([k, v]) => [String(k).toLowerCase(), v])
  )
  const completedNow = scopeState['completed'] || 0
  const scopeTotal = scope?.total ?? 0
  const successRate = scope?.success_rate != null
    ? Math.round(scope.success_rate * 100)
    : scopeTotal > 0
      ? Math.round((completedNow / scopeTotal) * 100)
      : 0
  const asOf = scope?.as_of
    ? new Date(scope.as_of).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : null
  const agentsOnline = metrics?.taskhub_agents_online?.find(a => a.label === 'online')?.count ?? 0

  const stateEntries = LANES.map(l => ({ label: l.label, value: scopeState[l.id] || 0, tone: l.tone }))

  return (
    <div className="stats-view">
      <header className="stats-header">
        <h2>统计概览</h2>
        <span className="stats-header__total mono">
          当前 {scope?.total ?? '—'} 任务 · 历史 {total_tasks} · {task_events_count} 事件
          {asOf ? ` · as of ${asOf}` : ''}
        </span>
      </header>

      {/* KPI 语义规则（R2 V1）：数字默认中性，只有「数字本身是结论且异常」才上色。
          副行给出可解释的上下文，数字不再是孤立大数。 */}
      <div className="stats-metrics">
        <MetricCard label="当前交付面" value={scope?.total ?? '—'} tone="dim"
                    sub={`历史全量 ${total_tasks}`} />
        <MetricCard label="已完成（当前）" value={completedNow} tone="dim"
                    sub={`${scopeTotal ? Math.round((completedNow / scopeTotal) * 100) : 0}% 交付面`} />
        <MetricCard label="Agent 在线" value={agentsOnline}
                    tone={agentsOnline > 0 ? 'dim' : 'danger'}
                    sub={agentsOnline > 0 ? '调度正常' : '无消费者 · 任务不会被领取'} />
        <MetricCard label="成功率（当前）" value={`${successRate}%`}
                    tone={successRate < 50 ? 'danger' : successRate < 80 ? 'warn' : 'dim'}
                    sub={`${completedNow} / ${scopeTotal || '—'} 完成`} />
      </div>

      {/* 主区：趋势图 + 状态分布并排，消除单列堆叠的纵向空旷 */}
      <div className="stats-main">
        <section className="stats-section stats-section--panel">
          <div className="stats-section__head">
            <h3>任务趋势（历史全量）</h3>
            <div className="seg-range" role="group" aria-label="趋势时间范围">
              {[7, 30, 90].map(d => (
                <button key={d} type="button"
                        className={`seg-range__btn${trendDays === d ? ' is-active' : ''}`}
                        onClick={() => setTrendDays(d)}>{d} 日</button>
              ))}
            </div>
          </div>
          <TrendChart data={trendSeries} outliers={outliers} days={trendDays} />
        </section>
        <section className="stats-section stats-section--panel">
          <h3>状态分布（当前交付面）</h3>
          <StateStack entries={stateEntries} />
        </section>
      </div>

      {/* 类目型数据改用胶囊：全宽横条在「类目多、单值小」时是一排灰线 */}
      <div className="stats-cols">
        <section className="stats-section stats-section--panel">
          <h3>阶段分布</h3>
          <div className="stats-pills">
            {STAGES.filter(s => (by_stage[s.id] || 0) > 0 || ['ready', 'implementing', 'review'].includes(s.id)).map(s => (
              <Pill key={s.id} label={s.label} value={by_stage[s.id] || 0} tone={s.tone} />
            ))}
          </div>
        </section>
        <section className="stats-section stats-section--panel">
          <h3>复合状态 · {compositeEntries.length} 种</h3>
          <div className="stats-pills">
            {compositeEntries.slice(0, 12).map(e => (
              <Pill key={e.key} label={e.label} value={e.count} tone={e.tone} />
            ))}
            {compositeEntries.length > 12 && (
              <span className="stats-pills__more">其余 {compositeEntries.length - 12} 种 · 合计 {compositeEntries.slice(12).reduce((s, e) => s + e.count, 0)}</span>
            )}
          </div>
        </section>
      </div>

      {(Object.keys(event_by_type).length > 0 || metrics) && (
        <section className="stats-section stats-section--panel">
          <h3>系统健康{Object.keys(event_by_type).length > 0 ? ` · ${task_events_count} 事件` : ''}</h3>
          <div className="stats-pills">
            {metrics && (() => {
              const up = metrics.taskhub_uptime_seconds || 0
              return <Pill label="运行时间" value={up >= 3600 ? `${Math.round(up / 3600)}h` : `${Math.max(1, Math.round(up / 60))}m`} tone="dim" />
            })()}
            {(metrics?.taskhub_agents_online || []).filter(a => a.count > 0).map(a => (
              <Pill key={a.label} label={`Agent ${a.label}`} value={a.count} tone={a.label === 'online' ? 'ok' : 'dim'} />
            ))}
            {Object.entries(event_by_type).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
              <Pill key={type} label={type} value={count} tone="dim" />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
