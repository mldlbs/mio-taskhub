const VIEWS = [
  { id: 'workflow', label: '工作流', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="6" height="16" rx="1.5" />
      <rect x="11" y="4" width="6" height="10" rx="1.5" />
      <rect x="19" y="4" width="2" height="7" rx="1" />
    </svg>
  )},
  { id: 'list', label: '列表', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M4 6h16M4 12h16M4 18h10" />
    </svg>
  )},
  { id: 'plan', label: '空闲计划', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  )},
  { id: 'topo', label: '拓扑', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="5" cy="5" r="2" />
      <circle cx="19" cy="5" r="2" />
      <circle cx="12" cy="19" r="2" />
      <path d="M7 6.5 17 5M6 7l4 10M18 7l-4 10" />
    </svg>
  )},
  { id: 'gantt', label: '甘特', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <path d="M3 5h18M3 12h18M3 19h18" />
      <path d="M6 5v14M12 5v14M18 5v14" />
      <path d="M4 8h8M7 15h11" />
    </svg>
  )},
  { id: 'ideas', label: '想法', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 18h6M10 21h4" />
      <path d="M12 3a6 6 0 0 0-4 10.5c.8.7 1 1.4 1 2.5h6c0-1.1.2-1.8 1-2.5A6 6 0 0 0 12 3Z" />
    </svg>
  )},
  { id: 'templates', label: '模板', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="18" height="18" rx="2" />
      <path d="M3 9h18M9 3v18" />
    </svg>
  )},
  { id: 'scheduled', label: '定时', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 3" />
    </svg>
  )},
  { id: 'stats', label: '统计', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M4 20h16M4 20V10M10 20V4M16 20v-6M22 20V8" />
    </svg>
  )},
  { id: 'observability', label: '观测台', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 12h4l2-7 4 14 2-7h6" />
    </svg>
  )},
  { id: 'memory', label: '记忆', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 3a4 4 0 0 0-4 4v1a3 3 0 0 0-2 5.7c.4.5.5 1 .5 1.6V16a2 2 0 0 0 2 2h7a2 2 0 0 0 2-2v-.7c0-.6.1-1.1.5-1.6A3 3 0 0 0 16 8V7a4 4 0 0 0-4-4Z" />
      <path d="M9 18h6M10 21h4" />
    </svg>
  )},
  { id: 'mio', label: 'Mio 运行时', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="7" y="7" width="10" height="10" rx="2" />
      <path d="M10 3v3M14 3v3M10 18v3M14 18v3M3 10h3M3 14h3M18 10h3M18 14h3" />
    </svg>
  )},
  { id: 'observatory', label: '记忆观测', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6" cy="6.5" r="2.5" />
      <circle cx="18" cy="8.5" r="2.5" />
      <circle cx="12" cy="18" r="2.5" />
      <path d="M8.4 7.4l7.2 0.9M7.1 8.7l3.6 7M16.7 10.6l-3.3 5.2" />
    </svg>
  )},
]

const GEAR_ICON = (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
  </svg>
)

export default function Rail({ view, onChange, wsLive, contrast, onToggleContrast }) {
  return (
    <nav className="rail" aria-label="主导航">
      <div className="rail__logo" aria-hidden="true">
        <img src="/icon.png" alt="" width="38" height="38" />
      </div>

      {VIEWS.map(v => (
        <button
          key={v.id}
          className={`rail__btn${view === v.id ? ' is-active' : ''}`}
          onClick={() => onChange(v.id)}
          aria-label={v.label}
          aria-pressed={view === v.id}
        >
          {v.icon}
          <span className="rail__tip">{v.label}</span>
        </button>
      ))}

      <div className="rail__spacer" />

      <button
        className={`rail__btn${view === 'settings' ? ' is-active' : ''}`}
        onClick={() => onChange('settings')}
        aria-label="设置"
        aria-pressed={view === 'settings'}
      >
        {GEAR_ICON}
        <span className="rail__tip">设置</span>
      </button>

      <button
        className={`rail__btn${contrast ? ' is-active' : ''}`}
        onClick={onToggleContrast}
        aria-label={contrast ? '切换到标准对比' : '切换到高对比'}
        aria-pressed={contrast}
      >
        <span className="rail__contrast-glyph">Aa</span>
        <span className="rail__tip">{contrast ? '标准对比' : '高对比'}</span>
      </button>

      <div
        className={`rail__ws${wsLive ? ' is-live' : ''}`}
        title={wsLive ? '实时连接' : '连接断开'}
        aria-label={wsLive ? '实时连接' : '连接断开'}
      />
    </nav>
  )
}
