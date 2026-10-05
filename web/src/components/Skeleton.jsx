/**
 * 通用 Skeleton 骨架屏：loading 时的占位结构。
 * - 闪烁动画表示加载中
 * - 提供预制 variants：text/line/circle/box
 */
import './Skeleton.css'

export function Skeleton({ variant = 'text', width, height, count = 1, style = {} }) {
  const items = Array.from({ length: count }, (_, i) => i)
  return (
    <>
      {items.map(i => (
        <div
          key={i}
          className={`skeleton skeleton--${variant}`}
          style={{ width, height, ...style }}
          aria-hidden="true"
        />
      ))}
    </>
  )
}

export function SkeletonText({ lines = 3, width = '100%' }) {
  return <Skeleton variant="text" count={lines} width={width} />
}

export function SkeletonField({ count = 6 }) {
  return (
    <div className="skeleton-grid">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skeleton-field">
          <Skeleton variant="text" width="40%" height="11px" />
          <Skeleton variant="text" width="60%" height="14px" />
        </div>
      ))}
    </div>
  )
}

export function SkeletonList({ count = 5 }) {
  return (
    <div className="skeleton-list">
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skeleton-row">
          <Skeleton variant="text" width="30%" height="12px" />
          <Skeleton variant="text" width="50%" height="12px" />
          <Skeleton variant="text" width="15%" height="12px" />
        </div>
      ))}
    </div>
  )
}
