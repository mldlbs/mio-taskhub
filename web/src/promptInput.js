/**
 * 通用输入对话框（promise 式，框架无关）—— 与 confirm.js 同构。
 *
 * 用于替代 window.prompt：原生 prompt 不可样式化、单行、无校验、无说明、
 * 且会阻塞整个渲染线程。
 *
 * 用法：
 *   import { promptInput } from './promptInput'
 *   const text = await promptInput('写下结论：', { title: '关闭讨论', defaultValue: '…' })
 *   if (text === null) return   // 用户取消
 *
 * 渲染方：components/InputDialog.jsx（App 挂载一次）。
 * 同一时刻只允许一个输入框：新请求在有未决输入时直接 resolve(null)。
 */
let pending = null
const subscribers = new Set()

const emit = () => subscribers.forEach((fn) => fn(pending))

export function promptInput(message, opts = {}) {
  if (pending) return Promise.resolve(null)
  return new Promise((resolve) => {
    pending = {
      message: String(message ?? ''),
      title: opts.title || '输入内容',
      okText: opts.okText || '确认',
      cancelText: opts.cancelText || '取消',
      defaultValue: opts.defaultValue ?? '',
      placeholder: opts.placeholder || '',
      hint: opts.hint || '',
      multiline: opts.multiline !== false,   // 默认多行（结论、备注类文本居多）
      required: opts.required !== false,
      resolve(value) {
        pending = null
        emit()
        resolve(value)
      },
    }
    emit()
  })
}

export function subscribePrompt(fn) {
  subscribers.add(fn)
  fn(pending)
  return () => subscribers.delete(fn)
}
