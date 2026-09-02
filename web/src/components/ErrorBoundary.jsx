import { Component } from 'react'

/**
 * 错误边界：组件渲染时抛错时显示降级 UI 而非全白屏。
 * - componentDidCatch 捕获子组件错误
 * - 显示错误摘要 + 重试按钮（reset state）
 * - 可选 onError 回调（用于上报到 /api/memory/observer/ingest）
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null, info: null }
    this._reset = this._reset.bind(this)
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, info) {
    this.setState({ info })
    // 上报
    try {
      console.error('[ErrorBoundary]', error, info)
      if (typeof this.props.onError === 'function') {
        this.props.onError(error, info)
      }
    } catch { /* 静默 */ }
  }

  _reset() {
    this.setState({ hasError: false, error: null, info: null })
  }

  render() {
    if (this.state.hasError) {
      const e = this.state.error
      const name = e?.name || 'Error'
      const message = e?.message || String(e)
      const fallback = this.props.fallback
      if (fallback) return fallback({ error: e, reset: this._reset, name, message })
      return (
        <div className="error-boundary" role="alert">
          <div className="error-boundary__icon">▲</div>
          <h3>组件出错了</h3>
          <div className="error-boundary__name mono">{name}</div>
          <div className="error-boundary__msg">{message}</div>
          {this.state.info?.componentStack && (
            <details className="error-boundary__stack">
              <summary>查看堆栈</summary>
              <pre className="mono">{this.state.info.componentStack}</pre>
            </details>
          )}
          <div className="error-boundary__actions">
            <button className="memory-btn" onClick={this._reset}>重试</button>
            <button className="memory-btn" onClick={() => location.reload()}>刷新页面</button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
