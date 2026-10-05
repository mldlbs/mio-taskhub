/**
 * 通用确认对话框（promise 式，框架无关）。
 *
 * 用法（任何地方，包括 api.js 这类非 React 模块）：
 *   import { confirm } from './confirm'
 *   const ok = await confirm('确定要删除吗？', { title: '删除任务', danger: true })
 *   if (!ok) return
 *
 * 渲染方：components/ConfirmDialog.jsx（App 挂载一次）。
 * 同一时刻只允许一个确认框：新请求在有弹窗未决时直接 resolve(false)。
 */
let pending = null
const subscribers = new Set()

function emit() {
  subscribers.forEach((fn) => fn(pending))
}

export function confirm(message, opts = {}) {
  if (pending) return Promise.resolve(false)
  return new Promise((resolve) => {
    pending = {
      message: String(message ?? ''),
      title: opts.title || '确认操作',
      okText: opts.okText || '确认',
      cancelText: opts.cancelText || '取消',
      danger: !!opts.danger,
      resolve(value) {
        pending = null
        emit()
        resolve(value)
      },
    }
    emit()
  })
}

export function subscribeConfirm(fn) {
  subscribers.add(fn)
  fn(pending)
  return () => subscribers.delete(fn)
}
