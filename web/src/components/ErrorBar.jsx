/**
 * 全局错误提示条：区分错误类型 + 重试按钮。
 * - errorType: 'network' / 'api' / 'user' / 'unknown'
 * - onRetry: 重试回调（可选）
 */
export default function ErrorBar({ message, errorType = 'unknown', onRetry, onDismiss }) {
  const icon = errorType === 'network' ? '⚠' : errorType === 'api' ? '⛔' : '▲'
  return (
    <div className={`errorbar errorbar--${errorType}`} role="alert">
      <span className="errorbar__icon">{icon}</span>
      <span className="errorbar__msg">{message}</span>
      {onRetry && (
        <button
          className="errorbar__retry"
          onClick={onRetry}
          aria-label="重试"
        >
          重试
        </button>
      )}
      {onDismiss && (
        <button
          className="errorbar__close"
          onClick={onDismiss}
          aria-label="关闭错误提示"
        >
          ×
        </button>
      )}
    </div>
  )
}
