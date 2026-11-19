# 模块设计 · 前端动效优化

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.1 |
| 状态 | approved |
| 最后更新 | 2026-11-19 09:39 |
| 负责人 | 项目作者（单人） |
| 关联任务 | d1a14902 |
| 上游需求 | `docs/taskhub/d1a14902/requirement.md`（FR-1 ~ FR-7） |
| 接口契约 | `docs/taskhub/d1a14902/api.md`（本次无后端契约变更） |

## 模块职责与边界

### 做什么

- 把两处**持续型**无限动画从「逐帧重绘属性」改为「合成器可接管的属性」（transform / opacity）。
- 新增一个极小的客户端偏好模块 `web/src/motion.js`，负责：读取偏好 → 应用到 `<html>` 的 class → 持久化。
- 在设置页暴露一个开关；应用启动时即应用偏好（不等用户进设置页）。

### 不做什么

- 不删除动效代码，不改视觉设计语言。
- 不动后端任何代码路径、不动数据、不改任务/看板交互。
- 不做「自动检测软件渲染」（无法可靠判定，见需求 N-1）。
- 不动瞬时动画（`laneFlash` 等）与事件驱动的 transition。

### 边界归属

| 关注点 | 归属 | 理由 |
|---|---|---|
| 「动效开/关」的偏好状态与持久化 | `web/src/motion.js`（新建） | 独立小模块，便于单测与复用 |
| 「关掉动效」如何作用于 CSS | `index.css` 的 `.no-anim` 规则 | 纯表现层，CSS 是唯一落点 |
| 启动时应用偏好 | `web/src/App.jsx`（入口） | 早于首帧，避免闪一下动效 |
| 用户操作入口 | `web/src/components/SettingsView.jsx` | 已有设置页，新增一行开关 |

## 输入 / 输出

### `web/src/motion.js` 对外接口

| 函数 | 输入 | 输出 | 副作用 |
|---|---|---|---|
| `motionEnabled()` | 无 | `boolean`（是否开启动效） | 无（只读 localStorage） |
| `applyMotion()` | 无 | `boolean`（应用后的最终状态） | 给 `<html>` 加/去 `no-anim` class |
| `setMotion(enabled)` | `boolean` | `boolean`（应用后的最终状态） | 写 localStorage + 调 `applyMotion()` |

### 偏好存储契约

| 项 | 值 |
|---|---|
| 存储键 | `localStorage['MIO_MOTION']` |
| 取值 | `'on'` \| `'off'`（**无键 = 默认关闭**） |
| 作用对象 | `document.documentElement.classList` 的 `no-anim` |
| 持久化 | 写入 localStorage，跨重启/刷新保持 |
| 降级 | localStorage 不可用（隐私模式）→ 捕获异常，回落默认值，不报错 |

## 详细设计

### 一、动画属性改造（FR-1）

**`wsPulse`（box-shadow 扩散）→ 伪元素脉冲环**

```css
.rail__ws.is-live,
.ws-dot.is-live,
.np-status__dot.is-live {
  position: relative;
  will-change: transform;
}
.rail__ws.is-live::after,
.ws-dot.is-live::after,
.np-status__dot.is-live::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 2px solid currentColor;   /* 颜色跟随元素自身 color */
  opacity: 0;
  pointer-events: none;
  will-change: transform, opacity;
  animation: wsRing 2s var(--ease) infinite;
}
@keyframes wsRing {
  0%   { transform: scale(0.55); opacity: 0.75; }
  70%  { transform: scale(1.9);  opacity: 0; }
  100% { transform: scale(1.9);  opacity: 0; }
}
```

- 视觉等价：原来「box-shadow 从 0 扩散到 7px 再收回」≈ 现在「一个 2px 环从 0.55 倍放大到 1.9 倍并淡出」，同为呼吸光圈。
- 为让 `currentColor` 生效，三个宿主选择器各自补 `color:`（原背景色改为 `background` + `color` 双写）。
- `pointer-events: none` 保证伪元素不拦截点击。

**`liveScan`（background-position 位移）→ transform 位移**

```css
.task__live {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  height: 2px;
  overflow: hidden;          /* 新增：裁掉移出边界的扫描条 */
  opacity: 0.85;
}
.task__live::after {
  content: '';
  position: absolute;
  top: 0; left: 0;
  width: 220px; height: 2px;
  background: linear-gradient(90deg, transparent, var(--live), transparent);
  will-change: transform;
  animation: liveScan 1.5s linear infinite;
}
@keyframes liveScan {
  0%   { transform: translateX(-220px); }
  100% { transform: translateX(100%); }
}
```

- 扫描条从「背景图位移」改为「元素位移」，位移量与原 `background-position: -220px → 240px` 等价。
- 原实现用 `background-repeat: no-repeat` 天然裁剪；改为元素位移后需显式 `overflow: hidden`。

### 二、动效开关（FR-2 ~ FR-4）

**`web/src/motion.js`（新建）**

```js
const KEY = 'MIO_MOTION'

export function motionEnabled() {
  try {
    const v = localStorage.getItem(KEY)
    if (v === 'off') return false
    if (v === 'on') return true
  } catch { /* 隐私模式 → 用默认值 */ }
  return false          // 默认关闭：持续动效是纯装饰，软件渲染下代价是一个多核
}

export function applyMotion() {
  const on = motionEnabled()
  try { document.documentElement.classList.toggle('no-anim', !on) } catch { /* ignore */ }
  return on
}

export function setMotion(enabled) {
  try { localStorage.setItem(KEY, enabled ? 'on' : 'off') } catch { /* ignore */ }
  return applyMotion()
}
```

**`index.css` 的 `.no-anim` 规则（FR-4）**

```css
.no-anim *,
.no-anim *::before,
.no-anim *::after {
  animation-duration: 0.01ms !important;
  animation-iteration-count: 1 !important;
}
```

- **只停持续动画，不动 transition**：transition 由事件驱动（悬停、面板展开），不产生常驻帧开销，不该一起牺牲。
- **必须配 `iteration-count: 1`**：只压 duration 而保留 infinite 会变成 0.01ms 高速死循环——比不关更糟。
- `animation-fill-mode` 默认 `none` → 一次 0.01ms 迭代后元素回到基础样式（光圈消失、扫描条停在起点），视觉自然。

**`web/src/App.jsx`（启动即应用）**

```js
import { applyMotion } from './motion'

// 启动即应用动效偏好（用户关闭动效 / 系统开启「减少动画」时禁用持续动画）
applyMotion()
```

放在 import 之后（模块级，早于首帧渲染）→ 避免「先动一下再停」的闪烁。

**`web/src/components/SettingsView.jsx`（用户入口）**

- 新增状态：`const [motionOn, setMotionOn] = useState(motionEnabled())`
- 新增「界面」小节（放在「运行信息」之前），表格一行：动效（呼吸光圈 / 扫描条 / 过渡）+ 复选框。
- 切换动作：`setMotion(v)` → `setMotionOn(v)`。
- 附一句说明：软件渲染环境下持续动效会让界面每帧重绘，实测仅 2 个状态点就能占用一个多核。

### 三、`prefers-reduced-motion` 缺陷修复（FR-5）

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;   /* ← 补这一行 */
    transition-duration: 0.01ms !important;
  }
}
```

修复前：系统开启「减少动画」→ 无限动画变 0.01ms 死循环 → **比不开还糟**（且用户无感知）。
修复后：系统偏好真正生效。

> 注：Windows 的「减少动画」实测**不会**被 WebView2 跟随（`SPI_GETCLIENTAREAANIMATION` 本就为 0，但页面仍在动画），所以**不能只依赖系统偏好**，必须有应用内开关。

### 四、部署路径（前端是 data 资源）

```
web/src 改动
  → cd web && npm run build          # 产出 web/dist
  → 拷到 dist/mio-taskhub-v3/_internal/web/dist/   （Tauri 打包源，下一次打壳会带上）
  → 拷到 E:\Program Files\TaskHub\hub\_internal\web\dist/  （安装版，即时生效）
  → 重启 Tauri 壳                    （窗口会缓存旧 bundle，不重启不生效）
```

**无需重打 PyInstaller**（纯前端资源改动）。

## 异常与边界情况

| 场景 | 处理 | 是否符合预期 |
|---|---|---|
| localStorage 不可用（隐私模式） | `try/catch` 吞掉，用默认值 | FR-2 |
| 用户显式开启动效 | `MIO_MOTION='on'` → 动画恢复 | FR-3 |
| 用户清空 localStorage | 回到默认（关闭） | FR-3 |
| 系统开启「减少动画」但用户显式开启动效 | 以用户显式选择为准（应用内开关优先） | FR-5 的兜底 |
| 伪元素 `::after` 覆盖了点击 | `pointer-events: none` | FR-1 |
| `.task__live` 扫描条移出边界 | 父级 `overflow: hidden` | FR-1 |
| WebView2 缓存旧 bundle | 重启 Tauri 壳 | 部署要求 |

## 涉及文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `web/src/index.css` | 改 | wsPulse→wsRing 伪元素环、liveScan→transform、`.no-anim` 规则、reduced-motion 补 iteration-count |
| `web/src/motion.js` | 新增 | 偏好读取/应用/持久化 |
| `web/src/App.jsx` | 改 | 启动即 `applyMotion()` |
| `web/src/components/SettingsView.jsx` | 改 | 「界面 → 动效」开关 |
| `tests/test_task_list_projection.py` | 不动 | 本次无后端改动，既有后端用例不受影响 |
