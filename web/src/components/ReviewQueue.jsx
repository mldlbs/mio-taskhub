import { useState, useEffect, useCallback } from 'react'
import { prio, fmtDate } from '../constants'
import { api } from '../api'

function formatDuration(sec) {
  if (sec == null) return '—'
  if (sec < 60) return `${sec}s`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ${sec % 60}s`
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  return `${h}h ${m}m`
}

function slaClass(sec) {
  if (sec == null) return ''
  if (sec > 7200) return 'rq-sla--danger'   // > 2h
  if (sec > 3600) return 'rq-sla--warn'     // > 1h
  return 'rq-sla--ok'
}

export default function ReviewQueue({ onOpen }) {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)

  const load = useCallback(async () => {
    try {
      const data = await api.reviewQueue()
      setTasks(data.tasks || [])
    } catch { /* silent */ }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  // 每 30s 刷新一次 SLA 计时
  useEffect(() => {
    const interval = setInterval(() => setTick(t => t + 1), 30000)
    return () => clearInterval(interval)
  }, [])

  const avgWait = tasks.length
    ? Math.round(tasks.reduce((s, t) => s + (t.wait_seconds || 0), 0) / tasks.length)
    : 0
  const overdue = tasks.filter(t => (t.wait_seconds || 0) > 3600).length

  return (
    <div className="rq">
      <div className="rq__head">
        <div>
          <h2>审阅队列 <span className="rq__accent">REVIEW QUEUE</span></h2>
          <p className="rq__sub">
            {tasks.length} 个任务待审阅 · 平均等待 {formatDuration(avgWait)}
            {overdue > 0 && <span className="rq__overdue"> · {overdue} 个超时</span>}
          </p>
        </div>
        <button className="btn btn--ghost" onClick={load}>{loading ? '⏳' : '🔄'}</button>
      </div>

      {tasks.length === 0 && !loading && (
        <div className="rq__empty">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
            <path d="M9 12l2 2 4-4" />
            <circle cx="12" cy="12" r="10" />
          </svg>
          当前没有待审阅的任务
        </div>
      )}

      {tasks.length > 0 && (
        <div className="rq__list">
          {tasks.map(t => {
            const p = prio(t.priority)
            const wait = tick >= 0 ? t.wait_seconds : t.wait_seconds
            return (
              <div key={t.id} className="rq__row" onClick={() => onOpen && onOpen(t)}>
                <div className="rq__row-left">
                  <span className={`rq__prio rp-check--p${p.p}`}>{p.label}</span>
                  <span className="rq__row-title" title={t.title}>{t.title}</span>
                  {t.project && <span className="rq__row-project">{t.project}</span>}
                </div>
                <div className="rq__row-right">
                  <span className={`rq__sla mono ${slaClass(wait)}`}>
                    {formatDuration(wait)}
                  </span>
                  <span className="rq__row-attempt mono">#{t.attempt}</span>
                  {t.review_started_at && (
                    <span className="rq__row-started mono">{fmtDate(t.review_started_at)}</span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {tasks.length > 0 && (
        <div className="rq__legend">
          <span className="rq__legend-item"><span className="rq-sla rq-sla--ok" />{"< 1h"}</span>
          <span className="rq__legend-item"><span className="rq-sla rq-sla--warn" />1-2h</span>
          <span className="rq__legend-item"><span className="rq-sla rq-sla--danger" />{"> 2h"}</span>
        </div>
      )}
    </div>
  )
}
