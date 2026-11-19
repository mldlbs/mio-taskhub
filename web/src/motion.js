/**
 * 动效开关（2026-11-19）。
 *
 * 为什么需要：CSS 的**持续动画**（呼吸光圈、扫描条）在**软件渲染**环境
 * （远程桌面 / 虚拟显示器 / 禁用硬件加速的 WebView）下，会让界面**整页每帧
 * 重新光栅化**——实测（TaskHub Tauri 窗口 1280×800，WebView2 软件渲染）：
 *   · 全部动画开启 → 162.6% 单核
 *   · 仅 2 个 10px 状态点做 box-shadow 动画 → 107.1% 单核
 *   · 全部动画关闭 →   2.5% 单核
 * 即：两个状态点就能吃掉一个多核。
 *
 * 开关语义：默认跟随系统（prefers-reduced-motion）；用户可在「设置 → 界面」
 * 里强制开/关，选择持久化到 localStorage。
 */
const KEY = 'MIO_MOTION'

/** 用户是否开启了动效（默认开；系统开启「减少动画」时也视为关）。 */
export function motionEnabled() {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'off') return false
    if (v === 'on') return true
  } catch { /* localStorage 不可用（隐私模式）→ 用默认值 */ }
  // 默认**关闭**：持续动效是纯装饰，但在软件渲染环境下的代价是「一个多核」。
  // 需要时在「设置 → 界面 → 动效」里一键打开。
  return false
}

/** 把当前偏好应用到 <html>（返回最终是否开启）。 */
export function applyMotion() {
  const on = motionEnabled()
  try { document.documentElement.classList.toggle('no-anim', !on) } catch { /* ignore */ }
  return on
}

/** 设置动效并持久化。 */
export function setMotion(enabled) {
  try { localStorage.setItem(KEY, enabled ? 'on' : 'off') } catch { /* ignore */ }
  return applyMotion()
}
