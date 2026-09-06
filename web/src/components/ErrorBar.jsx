/**
 * 全局错误提示条：区分错误类型 + 重试按钮 + hint 提示。
 * - errorType: 'network' / 'api' / 'user' / 'unknown'
 * - onRetry: 重试回调（可选）
 * - error: ApiError 实例（可选，优先于 message/errorType）
 */
export default function ErrorBar({ message, errorType = 'unknown', onRetry, onDismiss, error }) {
  // 从 ApiError 提取信息
  const displayMsg = error?.userMessage || message
  const hint = error?.hint
  const errorCode = error?.error_code
  const status = error?.status

  const icon = errorType === 'network' ? '⚠' : errorType === 'api' ? '⛔' : '▲'

  return (
    <div className={`errorbar errorbar--${errorType}`} role="alert">
      <span className="errorbar__icon">{icon}</span>
      <span className="errorbar__msg">
        {displayMsg}
        {status && <span className="errorbar__status mono"> ({status})</span>}
        {errorCode && <span className="errorbar__code mono"> [{errorCode}]</span>}
      </span>
      {hint && <span className="errorbar__hint">{hint}</span>}
      {onRetry && (
        <button className="errorbar__retry" onClick={onRetry} aria-label="重试">重试</button>
      )}
      {onDismiss && (
        <button className="errorbar__close" onClick={onDismiss} aria-label="关闭">×</button>
      )}
    </div>
  )
}
