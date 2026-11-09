/**
 * 浮层栈（overlay stack）—— 全仓唯一的 Esc 归属仲裁。
 *
 * 问题（Phase 2 D1 / C-2）：TaskDetail 与 DocPanel 各自在 document 上监听 Escape，
 * 两层同时打开时按一次 Esc 会两层一起关，用户被扔回看板、丢失上下文。
 *
 * 约定：
 *  1. 每个浮层挂载时 push 自己、卸载时 pop（用 useOverlayStack hook 自动管理）；
 *  2. 只有一个全局 keydown 监听器，且**只把 Esc 交给栈顶**；
 *  3. 栈顶 = 最后打开的那一层（后 push 者优先），与视觉层级一致：
 *     ConfirmDialog > CreateModal > DocPanel > TaskDetail > CommandPalette。
 *
 * 用法：
 *   useOverlayStack(onClose, active)   // active=false 时不占栈位（如未打开的面板）
 */
import { useEffect, useRef } from 'react'

const stack = []
let bound = false

function bindGlobalListener() {
  if (bound || typeof document === 'undefined') return
  bound = true
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return
    const top = stack[stack.length - 1]
    if (!top) return
    // 只让栈顶响应：阻止事件继续传播到其它监听器（历史遗留的 document 监听器也收不到）
    e.stopPropagation()
    if (typeof e.preventDefault === 'function') e.preventDefault()
    top.onClose()
  })
}

/** 返回当前栈深度（调试/测试用） */
export function overlayDepth() {
  return stack.length
}

export function useOverlayStack(onClose, active = true) {
  const idRef = useRef(null)
  const cbRef = useRef(onClose)
  // 每次渲染同步最新回调，避免把 onClose 放进依赖导致反复 push/pop
  cbRef.current = onClose

  useEffect(() => {
    if (!active) return undefined
    bindGlobalListener()
    if (!idRef.current) idRef.current = Symbol('overlay')
    const entry = { id: idRef.current, onClose: () => cbRef.current && cbRef.current() }
    stack.push(entry)
    return () => {
      const i = stack.lastIndexOf(entry)
      if (i >= 0) stack.splice(i, 1)
    }
  }, [active])
}

export default useOverlayStack
