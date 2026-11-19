# 实现计划 · 前端动效优化

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.1 |
| 状态 | approved |
| 最后更新 | 2026-11-19 09:39 |
| 负责人 | 项目作者（单人） |
| 关联任务 | d1a14902 |
| 需求 | `docs/taskhub/d1a14902/requirement.md`（FR-1 ~ FR-7） |
| 设计 | `docs/taskhub/d1a14902/spec.md` |
| 契约 | `docs/taskhub/d1a14902/api.md` |
| 测试 | `docs/taskhub/d1a14902/test.md` |

## 实施原则

- **先复现再修改**：分层实验（停 hub → 禁动画 → 只恢复 2 个 wsPulse）已经定位根因，不靠猜。
- **纯前端改动**：不碰后端、不重打 PyInstaller，只重建 `web/dist` 并部署。
- **改完即验**：每次改动都用同一套计时口径复测，不接受"感觉快了"。
- **保留可逆性**：动效可一键开回，不做不可逆删除。

## 批次 0 · 流程前置

| 步骤 | 动作 | 完成条件 |
|---|---|---|
| 0.1 | 建任务 + scaffold 文档链 | 任务 `d1a14902`，requirement/spec/api/test 已落盘 |
| 0.2 | 写四份文档并推进到 approved | 质量分 ≥ 80、errors = 0 |
| 0.3 | 补讨论记录（进 design 前置） | 讨论 `7995cd3c` 已创建 |
| 0.4 | 推进阶段到 ready | brainstorming → design → planning → ready |
| 0.5 | 建分支 | `task-d1a14902` |

## 批次 A · 定位与基线（已完成）

| 步骤 | 动作 | 对应 FR | 完成条件 |
|---|---|---|---|
| A1 | 统计整机 CPU 排名，识别真正占用者 | — | 确认 hub 已降到 5.6%，WebView2 才是大头 |
| A2 | 实验：停 hub → 看 WebView2 是否回落 | — | 162.6% → 134.9%（与数据无关） |
| A3 | 实验：注入「禁用所有动画」 | — | **3.2%**（确认为 CSS 无限动画） |
| A4 | 实验：仅恢复 2 个 `wsPulse` | — | **107.1%**（确认少量 box-shadow 即触发整页重绘） |
| A5 | 确认环境：虚拟显示器 + WebView2 软件渲染 | — | `spacedeskService` 在跑；`dwm` 44% 佐证整屏重绘 |

## 批次 B · 改造（已完成）

| 步骤 | 动作 | 对应 FR | 文件 | 完成条件 |
|---|---|---|---|---|
| B1 | `wsPulse` → 伪元素脉冲环（transform+opacity） | FR-1 | `web/src/index.css` | 三处状态点改用 `wsRing` |
| B2 | `liveScan` → transform 位移（父级加 `overflow: hidden`） | FR-1 | `web/src/index.css` | 扫描条改为元素位移 |
| B3 | 新建 `web/src/motion.js`（偏好读写 + 应用） | FR-2 FR-3 | 新文件 | 三个导出函数齐备 |
| B4 | `App.jsx` 顶层调用 `applyMotion()` | FR-2 | `web/src/App.jsx` | 启动即生效 |
| B5 | `SettingsView.jsx` 加「界面 → 动效」开关 | FR-2 FR-3 | `web/src/components/SettingsView.jsx` | 开关可切换并持久化 |
| B6 | `.no-anim` 规则（只停持续动画，保留 transition） | FR-4 | `web/src/index.css` | 含 `animation-iteration-count: 1` |
| B7 | 修复全局 `prefers-reduced-motion`（补 iteration-count） | FR-5 | `web/src/index.css` | 不再产生 0.01ms 死循环 |

> 行尾注意事项：`index.css` / `App.jsx` 为 CRLF，`SettingsView.jsx` 为 LF → 脚本改写须按文件保持。

## 批次 C · 构建与部署

| 步骤 | 动作 | 完成条件 |
|---|---|---|
| C1 | `cd web && npm run build` | 产出新 hash 的 `index-*.js` / `index-*.css` |
| C2 | 拷到 `dist/mio-taskhub-v3/_internal/web/dist/`（Tauri 打包源） | 下次打壳带上本次改动 |
| C3 | 拷到 `E:\Program Files\TaskHub\hub\_internal\web\dist\`（安装版） | 线上即时生效 |
| C4 | 重启 Tauri 壳（`mio-hub-shell.exe`） | 窗口加载新 bundle |

## 批次 D · 验证

| 步骤 | 动作 | 对应 FR | 完成条件 |
|---|---|---|---|
| D1 | 静态检查：持续动画无 box-shadow/background-position | FR-1 | grep 结果符合预期 |
| D2 | 采样 WebView2 CPU（40s 窗口） | FR-6 | **3.7% 单核**（修复前 162.6%） |
| D3 | 采样 hub CPU | — | 7.3% 单核（属前序任务成果） |
| D4 | 手动验证开关：切换 / 重启保持 / transition 仍在 | FR-2 FR-3 FR-4 | 行为符合预期 |
| D5 | 跑既有后端用例（不改断言） | FR-7 | 全过 |

## 涉及文件清单

| 文件 | 批次 | 动作 |
|---|---|---|
| `web/src/index.css` | B1 B2 B6 B7 | 改 |
| `web/src/motion.js` | B3 | 新增 |
| `web/src/App.jsx` | B4 | 改 |
| `web/src/components/SettingsView.jsx` | B5 | 改 |
| `web/dist/`、`dist/mio-taskhub-v3/_internal/web/dist/` | C1 C2 | 构建产物 |
| `E:\Program Files\TaskHub\hub\_internal\web\dist\` | C3 | 部署产物 |

## 风险与回退

| 风险 | 表现 | 回退动作 |
|---|---|---|
| 脉冲环观感与原 box-shadow 扩散差异明显 | 目视不满意 | 调 `wsRing` 的 scale/opacity 曲线；不影响功能 |
| 误把 transition 一起关掉 | 界面变得生硬 | `.no-anim` 规则不加 `transition` |
| 关闭动效后仍高 CPU | 还有别的持续动画 | 注入全局禁用复测定位（`laneFlash` / `sk` 骨架等） |
| 偏好写不进 localStorage（隐私模式） | 开关无效 | 已 try/catch 回落默认；可加内存态兜底 |
| 前端 bundle 未更新（窗口缓存） | 改了没生效 | 重启 Tauri 壳 |
| hub CPU 反弹 | 数据量继续增长 | 属 613bc0ca 的后续（分页 / 归档），另案 |

## 后续独立立项（本期不做）

- **列表分页**（契约破坏性，需通知全部消费方）。
- **僵尸任务归档**（task 表 1506 条 FAILED）。
- **console.log 日志轮转**（已 83 MB 无上限）。
- **`laneFlash` 等瞬时动画的 transform 化**：本次未做（代价有界），可在下次统一治理。
