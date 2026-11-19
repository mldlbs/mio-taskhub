# 测试验收 · 前端动效优化

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 状态 | planned（待执行） |
| 最后更新 | 2026-11-19 09:52 |
| 负责人 | 项目作者（单人） |
| 关联任务 | d1a14902 |
| 需求 | `docs/taskhub/d1a14902/requirement.md`（FR-1 ~ FR-7） |
| 设计 | `docs/taskhub/d1a14902/spec.md` |
| 契约 | `docs/taskhub/d1a14902/api.md` |

## 验收标准

| FR | 验收判据 | 对应用例 |
|---|---|---|
| FR-1 | `wsPulse`/`liveScan` 已无 `box-shadow`/`background-position` 动画；`wsRing` 用 transform+opacity；视觉意图保持 | TC-1 TC-2 |
| FR-2 | 设置页「界面 → 动效」开关可切换；写入 `MIO_MOTION`；`<html>` 出现/移除 `no-anim`；重启后保持 | TC-3 TC-4 |
| FR-3 | 首次启动（无 localStorage 键）动效为关闭态 | TC-5 |
| FR-4 | 关闭动效后：无持续动画帧（CPU 回落）；**transition 仍在**（悬停/面板展开有动效） | TC-6 TC-7 |
| FR-5 | 系统「减少动画」开启时，持续动画停止且**无 0.01ms 死循环**（CSS 规则含 `animation-iteration-count: 1`） | TC-8 |
| FR-6 | 无 GPU 加速环境下 WebView2 合计 CPU **≤ 10% 单核** | TC-9 |
| FR-7 | 后端 HTTP 接口契约零变更；既有后端用例不改断言全过 | TC-10 |

**总体验收判据**：TC-9 实测 WebView2 ≤ 10% 单核（起点 162.6%），且 TC-1~TC-8 全过。

## 测试范围

### 测什么

- **静态检查**：CSS 中不再有 `box-shadow`/`background-position` 驱动的无限动画；`wsRing`/`liveScan` 关键帧只用 transform/opacity；`.no-anim` 与 reduced-motion 规则均含 `animation-iteration-count: 1`。
- **偏好逻辑**：`motion.js` 三个函数的取值/降级行为（可用 node 直接跑纯逻辑，或以浏览器手测）。
- **性能实测**：同一台机器、同一窗口，40s 窗口取均值，对比修复前后。
- **回归**：后端既有用例（证明本次未碰后端）。

### 不测什么（明确排除）

- **不测跨设备的偏好同步**：偏好明确是本地 profile 存储，不同步。
- **不测浏览器兼容性矩阵**：本次只验证 Chromium（WebView2）与本地 Chrome。
- **不测视觉细节的像素级还原**：脉冲环与原 box-shadow 扩散的观感等价由人眼判断，不做自动化断言。
- **不测系统「减少动画」开关**：实测 WebView2 不跟随该系统偏好（见需求背景），因此不作为验收依赖。

### 测试环境与纪律

- 前端构建：`cd web && npm run build`。
- 部署后**必须重启 Tauri 壳**，否则窗口仍用旧 bundle。
- 后端用例：`MIO_TEST_DB_PATH=<独立路径>` + `--basetemp=<仓库外目录>`（仓库内会触发 safe-delete 批量删除保护）。
- 失败名单落盘再读：`pytest --lf -q -rf > out.txt`。

## 用例清单

| 编号 | 覆盖 FR | 前置 | 步骤 | 预期 |
|---|---|---|---|---|
| TC-1 | FR-1 | 源码 | grep `animation:` 相关的 `box-shadow`/`background-position` | 持续动画中**无**这两类属性；`wsRing` 关键帧只含 transform/opacity |
| TC-2 | FR-1 | 打开 Tauri 窗口 | 目视 WS 状态点与运行中任务卡的扫描条 | 动效开启时仍有呼吸光圈与扫描条（视觉未丢失） |
| TC-3 | FR-2 | 设置页 | 打开「设置 → 界面」，切换「动效」 | 开关状态立即改变；`<html>` 出现/移除 `no-anim`；`localStorage['MIO_MOTION']` 写入 `'on'`/`'off'` |
| TC-4 | FR-2 | 已切换过一次 | 关闭并重开 Tauri 壳 | 动效状态与上次一致（偏好持久化生效） |
| TC-5 | FR-3 | 清空 localStorage 后刷新 | 观察动效 | 默认**关闭**（无持续动画） |
| TC-6 | FR-4 | 动效关闭态 | 观察 WS 状态点、运行中任务卡 | **无**持续动画（光圈不呼吸、扫描条不移动） |
| TC-7 | FR-4 | 动效关闭态 | 悬停按钮、打开任务详情抽屉、触发 toast | 过渡效果**仍在**（未被误杀） |
| TC-8 | FR-5 | 源码 | 检查全局 `prefers-reduced-motion` 规则 | 含 `animation-iteration-count: 1 !important`（否则无限动画会变 0.01ms 死循环） |
| TC-9 | FR-6 | 无 GPU 加速环境（虚拟显示器/远程桌面） | 部署并重启 Tauri 壳，采样 WebView2 合计 CPU 40s | **≤ 10% 单核**（修复前 162.6%，全禁用动画下界 2.5%） |
| TC-10 | FR-7 | 隔离库 | 跑既有后端用例（`test_board_summary` + `test_task_list_projection` + 列表相关模块），**不改断言** | 全过——证明后端契约与行为未受影响 |

## 验收流程

- **谁验收**：项目作者（单人项目，本人即验收人）。
- **通过条件**：TC-1 ~ TC-10 全过；TC-9 ≤ 10% 单核。
- **不通过的回退路径**：
  1. 视觉不达标（脉冲环观感不对）→ 调整 `wsRing` 的 scale/opacity 曲线，不动其他部分。
  2. CPU 未达标 → 用分层实验定位：注入 `.no-anim` 全局规则复测（应立即回落），若仍高则说明还有别的持续动画（如 `laneFlash` 或 `sk` 骨架），逐个排查。
  3. 开关无效 → 检查 `App.jsx` 顶层 `applyMotion()` 是否执行、`localStorage` 是否被隐私模式屏蔽。
- **排查顺序**：先 TC-1/TC-8 静态检查（最快）→ 再 TC-9 性能实测 → 最后才动代码。
