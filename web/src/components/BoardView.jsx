import { useEffect, useRef, useState } from 'react'
import { LANES, STAGES, fmtDur } from '../constants'
import TaskCard from './TaskCard'

export default function BoardView({ tasks, onMove, onCancel, onOpen, loading, focus, groupBy = 'state' }) {
  const [dragging, setDragging] = useState(null)
  const [overPos, setOverPos] = useState(null)
  const [flashId, setFlashId] = useState(null)
  const laneRefs = useRef({})

  const lanes = groupBy === 'stage' ? STAGES : LANES
  const groupField = groupBy === 'stage' ? 'stage' : 'state'

  useEffect(() => {
    if (!focus) return
    setFlashId(focus.id)
    const el = laneRefs.current[focus.id]
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' })
    const t = setTimeout(() => setFlashId(null), 1400)
    return () => clearTimeout(t)
  }, [focus])

  const byLane = Object.fromEntries(lanes.map(l => [
    l.id,
    [...tasks.filter(t => (t[groupField] || '') === l.id)].sort((a, b) => (b.priority ?? 0) - (a.priority ?? 0)),
  ]))

  const laneTotal = (id) => byLane[id].reduce((s, t) => s + (t.est_duration_min || 0), 0)
  const totalTasks = tasks.length
  const totalDur = tasks.reduce((s, t) => s + (t.est_duration_min || 0), 0)
  const doneLane = groupBy === 'stage' ? 'done' : 'completed'
  const doneCount = (byLane[doneLane] || []).length
  const progress = totalTasks ? Math.round((doneCount / totalTasks) * 100) : 0

  const clearDrag = () => { setDragging(null); setOverPos(null) }

  const handleDrop = (laneId) => {
    if (dragging && dragging !== laneId) onMove(dragging, laneId)
    clearDrag()
  }

  const handleOver = (e, laneId) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    const cards = e.currentTarget.querySelectorAll('.task')
    let index = cards.length
    for (let i = 0; i < cards.length; i++) {
      const r = cards[i].getBoundingClientRect()
      if (e.clientY < r.top + r.height / 2) { index = i; break }
    }
    setOverPos({ lane: laneId, index })
  }

  return (
    <div className="pipeline">
      <div className="board-stats" role="status" aria-label="看板统计">
        <span className="board-stats__total">{totalTasks} 任务</span>
        <span className="board-stats__dur">· {fmtDur(totalDur)}</span>
        <span className="board-stats__progress">完成 {doneCount}/{totalTasks} ({progress}%)</span>
        <span className="board-stats__bar" aria-hidden="true">
          <span className="board-stats__fill" style={{ width: `${progress}%` }} />
        </span>
      </div>

      {lanes.map(lane => {
        const items = byLane[lane.id]
        const total = laneTotal(lane.id)
        const slot = dragging && overPos && overPos.lane === lane.id ? overPos.index : null

        const body = []
        items.forEach((t, i) => {
          if (slot === i) body.push(<div key="__slot" className="drop-slot">松开以放置</div>)
          body.push(
            <TaskCard
              key={t.id}
              task={t}
              index={i}
              onCancel={onCancel}
              onDragStart={setDragging}
              onOpen={onOpen}
              onMove={onMove}
            />
          )
        })
        if (dragging && slot !== null && slot >= items.length) {
          body.push(<div key="__slot" className="drop-slot">松开以放置</div>)
        }

        return (
          <section
            key={lane.id}
            ref={el => { laneRefs.current[lane.id] = el }}
            className={`lane${lane.tone === 'live' ? ' is-live' : ''}${overPos && overPos.lane === lane.id ? ' is-over' : ''}${flashId === lane.id ? ' is-flash' : ''}`}
            onDragOver={e => handleOver(e, lane.id)}
            onDragLeave={() => setOverPos(v => v && v.lane === lane.id ? null : v)}
            onDrop={e => { e.preventDefault(); handleDrop(lane.id) }}
            aria-label={`${lane.label}列`}
          >
            <header className="lane__head">
              <span className={`lane__dot${lane.tone === 'live' ? ' is-live' : ''}${lane.tone === 'warn' ? ' is-warn' : ''}${lane.tone === 'danger' ? ' is-danger' : ''}`} />
              <span className="lane__title">{lane.en}</span>
              <span className="lane__count" aria-live="polite">{items.length}</span>
              {total > 0 && <span className="lane__sum">· {fmtDur(total)}</span>}
            </header>

            <div className="lane__body">
              {loading && items.length === 0 && (
                <>
                  <div className="skeleton" style={{ '--i': 0 }} />
                  <div className="skeleton" style={{ '--i': 1 }} />
                </>
              )}

              {!loading && body}

              {!loading && items.length === 0 && !dragging && (
                <div className="lane__empty">
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                    <rect x="3" y="4" width="18" height="16" rx="3" />
                    <path d="M12 8v8M8 12h8" />
                  </svg>
                  <span>拖拽任务到此处</span>
                </div>
              )}
            </div>

            {lane.id !== 'failed' && (
              <div className="lane__flow" aria-hidden="true">
                <svg width="26" height="10" viewBox="0 0 26 10" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round">
                  <path d="M1 5h22M19 1l4 4-4 4" />
                </svg>
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}
