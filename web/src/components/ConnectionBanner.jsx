/**
 * WS 连接状态条：断线时显示重连提示 + lastSync 时间。
 */
import { useEffect, useState } from 'react'

export default function ConnectionBanner({ wsLive, lastSync, retryIn }) {
  if (wsLive) return null
  return (
    <div className="conn-banner" role="status" aria-live="polite">
      <span className="conn-banner__dot" />
      <span>
        连接断开
        {typeof retryIn === 'number' && retryIn > 0
          ? `，${retryIn}s 后重试`
          : '，正在重试…'}
      </span>
      {lastSync && (
        <span className="conn-banner__sync">最后同步：{formatTime(lastSync)}</span>
      )}
    </div>
  )
}

function formatTime(d) {
  if (!d) return ''
  const date = typeof d === 'string' ? new Date(d) : d
  return date.toLocaleTimeString()
}
