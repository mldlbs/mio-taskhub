import { useEffect, useState, useCallback, useRef, useMemo } from 'react'

const VIEWS = [
  { id: 'workflow', label: '工作流', icon: '⬡' },
  { id: 'list', label: '列表', icon: '☰' },
  { id: 'plan', label: '夜间计划', icon: '🌙' },
  { id: 'topo', label: '拓扑', icon: '◎' },
  { id: 'gantt', label: '甘特', icon: '▬' },
  { id: 'ideas', label: '想法', icon: '💡' },
  { id: 'templates', label: '模板', icon: '📋' },
  { id: 'scheduled', label: '定时任务', icon: '⏱' },
  { id: 'stats', label: '统计', icon: '📊' },
  { id: 'memory', label: '记忆', icon: '🧠' },
]

export default function CommandPalette({ open, onClose, onNavigate, tasks = [] }) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const inputRef = useRef(null)
  const listRef = useRef(null)

  useEffect(() => {
    if (open) {
      setQuery('')
      setSelected(0)
      setTimeout(() => inputRef.current?.focus(), 50)
    }
  }, [open])

  const results = useMemo(() => {
    const q = query.toLowerCase().trim()
    const items = []

    // Navigation items
    const views = q
      ? VIEWS.filter(v => v.label.toLowerCase().includes(q) || v.id.includes(q))
      : VIEWS
    for (const v of views) {
      items.push({ type: 'view', id: v.id, label: v.label, icon: v.icon })
    }

    // Task search
    if (q && tasks.length > 0) {
      const matched = tasks.filter(t =>
        t.title.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q) ||
        (t.project || '').toLowerCase().includes(q)
      ).slice(0, 8)
      for (const t of matched) {
        items.push({
          type: 'task',
          id: t.id,
          label: t.title,
          stage: t.stage,
          state: t.state,
          project: t.project,
        })
      }
    }

    return items
  }, [query, tasks])

  useEffect(() => { setSelected(0) }, [query])

  const execute = useCallback((item) => {
    if (!item) return
    if (item.type === 'view') {
      onNavigate(item.id)
    } else if (item.type === 'task') {
      onNavigate('workflow', item.id)
    }
    onClose()
  }, [onNavigate, onClose])

  const handleKeyDown = useCallback((e) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setSelected(s => Math.min(s + 1, results.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setSelected(s => Math.max(s - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      execute(results[selected])
    } else if (e.key === 'Escape') {
      onClose()
    }
  }, [results, selected, execute, onClose])

  // Scroll selected into view
  useEffect(() => {
    const el = listRef.current?.children[selected]
    el?.scrollIntoView({ block: 'nearest' })
  }, [selected])

  if (!open) return null

  return (
    <div className="cmd-overlay" onClick={onClose}>
      <div className="cmd-palette" onClick={e => e.stopPropagation()} role="dialog" aria-label="命令面板">
        <div className="cmd-input-wrap">
          <span className="cmd-input-icon">⌘</span>
          <input
            ref={inputRef}
            className="cmd-input"
            placeholder="搜索视图、任务…"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
          />
          <kbd className="cmd-esc">ESC</kbd>
        </div>
        <div className="cmd-list" ref={listRef}>
          {results.length === 0 ? (
            <div className="cmd-empty">无匹配结果</div>
          ) : (
            results.map((item, i) => (
              <div
                key={`${item.type}-${item.id}`}
                className={`cmd-item${i === selected ? ' is-selected' : ''}`}
                onClick={() => execute(item)}
                onMouseEnter={() => setSelected(i)}
              >
                {item.type === 'view' ? (
                  <>
                    <span className="cmd-item__icon">{item.icon}</span>
                    <span className="cmd-item__label">{item.label}</span>
                    <kbd className="cmd-item__badge">{VIEWS.findIndex(v => v.id === item.id) + 1}</kbd>
                  </>
                ) : (
                  <>
                    <span className="cmd-item__icon">📌</span>
                    <span className="cmd-item__label">{item.label}</span>
                    <span className="cmd-item__meta mono">{item.stage} · {item.state}</span>
                  </>
                )}
              </div>
            ))
          )}
        </div>
        <div className="cmd-footer">
          <span><kbd>↑↓</kbd> 导航</span>
          <span><kbd>Enter</kbd> 选择</span>
          <span><kbd>ESC</kbd> 关闭</span>
        </div>
      </div>
    </div>
  )
}
