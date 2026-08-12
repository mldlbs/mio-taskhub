import { useState } from 'react'
import { fmtDur, prio } from '../constants'

const WINDOW_MIN = 540 // 22:00 → 07:00

function buildSchedule(tasks) {
  const pool = tasks
    .filter(t => t.state === 'queued' || t.state === 'retrying')
    .sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0) || (a.est_duration_min ?? 0) - (b.est_duration_min ?? 0))

  const items = []
  let offset = 0
  for (const t of pool) {
    const dur = Math.max(5, t.est_duration_min ?? 30)
    if (offset + dur > WINDOW_MIN) break
    items.push({ ...t, start: offset, dur })
    offset += dur
  }
  return { items, fitted: items.length, overflow: pool.length - items.length, filled: offset, total: pool.length }
}

const pct = (min) => `${(min / WINDOW_MIN) * 100}%`

export default function PlanView({ tasks, onSchedule }) {
  const [plan, setPlan] = useState(null)

  const generate = () => {
    const p = buildSchedule(tasks)
    setPlan(p)
    onSchedule && onSchedule(p)
  }

  return (
    <div className="plan">
      <div className="plan__head">
        <div>
          <h2>夜间计划 <span style={{ color: 'var(--accent)' }}>NIGHT SHIFT</span></h2>
          <p>将待处理任务按优先级与预估时长排入 22:00 – 07:00 窗口</p>
        </div>
        <button className="btn btn--accent" onClick={generate}>⟳ 生成排期</button>
      </div>

      {plan ? (
        <>
          <div className="plan__stats">
            <div className="stat stat--accent">
              <div className="stat__label">已排入</div>
              <div className="stat__value">{plan.fitted}<span style={{ fontSize: 13, color: 'var(--ink-dim)', marginLeft: 8 }}>/ {plan.total}</span></div>
            </div>
            <div className="stat">
              <div className="stat__label">窗口占用</div>
              <div className="stat__value">{Math.round(plan.filled / WINDOW_MIN * 100)}<span style={{ fontSize: 13, color: 'var(--ink-dim)', marginLeft: 4 }}>%</span></div>
            </div>
            <div className="stat stat--warn">
              <div className="stat__label">溢出待排</div>
              <div className="stat__value">{plan.overflow}</div>
            </div>
            <div className="stat">
              <div className="stat__label">窗口</div>
              <div className="stat__value" style={{ fontSize: 16, paddingTop: 8 }}>22:00 → 07:00</div>
            </div>
          </div>

          <div className="timeline">
            <div className="timeline__scale">
              {['22:00', '23:00', '00:00', '01:00', '02:00', '03:00', '04:00', '05:00', '06:00', '07:00'].map((h, i) => (
                <span key={h} style={{ textAlign: i === 0 ? 'left' : i === 9 ? 'right' : 'center', flex: 1 }}>{h}</span>
              ))}
            </div>
            <div className="timeline__track">
              {plan.items.map((t, i) => {
                const p = prio(t.priority)
                const layer = p.p >= 3 ? 'p-p3' : p.p === 2 ? 'p-p2' : p.p === 1 ? 'p-p1' : 'p-p0'
                return (
                  <div
                    key={t.id}
                    className={`timeline__block ${layer}`}
                    style={{ left: pct(t.start), width: pct(t.dur), '--i': i }}
                    title={`${t.title} · ${p.label} · ${fmtDur(t.dur)}`}
                  >
                    <span className="timeline__tag">{p.label}</span>
                    <span className="timeline__txt">{t.title}</span>
                    <span className="timeline__dur">{fmtDur(t.dur)}</span>
                  </div>
                )
              })}
              {plan.items.length === 0 && (
                <div className="plan__empty" style={{ position: 'absolute', inset: '18px 0 0' }}>
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                    <rect x="3" y="4" width="18" height="16" rx="3" />
                    <path d="M12 8v8M8 12h8" />
                  </svg>
                  队列中没有可排入窗口的任务
                </div>
              )}
            </div>
          </div>

          <div className="timeline__legend" aria-label="优先级图例">
            {[{ p: 3, t: 'P3 · 紧急' }, { p: 2, t: 'P2 · 高' }, { p: 1, t: 'P1 · 中' }, { p: 0, t: 'P0 · 低' }].map(x => (
              <span key={x.p} className={`legend-chip l-p${x.p}`}><i />{x.t}</span>
            ))}
          </div>
        </>
      ) : (
        <div className="plan__empty">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <path d="M21 12.8A9 9 0 1111.2 3 7 7 0 0021 12.8z" />
          </svg>
          点击「生成排期」预览今晚的执行序列
        </div>
      )}
    </div>
  )
}
