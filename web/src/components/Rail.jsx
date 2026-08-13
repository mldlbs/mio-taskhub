const VIEWS = [
  { id: 'board', label: '看板', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
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
  { id: 'plan', label: '夜间计划', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  )},
  { id: 'flow', label: '流程', icon: (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="5" rx="1.5" />
      <rect x="3" y="15" width="18" height="5" rx="1.5" />
      <path d="M9 9v6M15 9v6" />
    </svg>
  )},
]

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
