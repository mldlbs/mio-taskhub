# Ledger 设计系统接入实际界面（2026-11-01）

> 依据 `docs/ui`（Ledger 设计系统，液态玻璃、明暗双主题）。本次只做主题层接入，不改业务结构与路由。

## 1 已接入

| 项 | 实现 |
|---|---|
| 令牌桥接 | `web/src/ledger.css`：把 Ledger 语义色/玻璃材质映射到现有 `--bg/--panel/--accent/--ink` 变量 |
| 主色 | 青柠 `#cdfa3f` → Ledger 靛紫 `#6f68f7`（高对比主题同步） |
| 字体 | 标题/正文切换为 Inter/Segoe UI 栈，数据仍用 JetBrains Mono |
| 液态玻璃 | 重新定义 `--glass-rail/card/lane/modal/sheen`；保持原有 backdrop-filter 与四层叠加 |
| 环境光 | `.ambient` 与 `::before/::after` 改为靛紫/青/琥珀色团，移除青柠 |
| 明暗主题 | `html[data-theme=light|dark]`；Rail 新增切换按钮；`localStorage(mio.theme)` 记忆 |
| 可分享主题 | `?theme=light` / `?theme=dark` 可直接打开对应主题（用于验收/截图） |
| 可分享视图 | `?view=workflow|list|...` 可直接打开指定视图（用于截图/深链） |

## 2 未接入

- 未重写 23 个组件；现有组件继续消费旧变量，因此个别硬编码色仍保留；
- `docs/ui` 规范站仍是独立静态页，不随 exe 分发；
- 未引入 Ledger 的 `design-tokens.json` 作为构建期类型源；
- 状态色的 7×4 令牌未逐项替换，仅通过 `--accent/--ok/--warn/--danger` 收敛。

## 3 验证

| 检查 | 结果 |
|---|---|
| `npm run build` | 通过；新资产 `index-D6N_DTyo.js` / `index-Be7YL3Ih.css` |
| PyInstaller `build.ps1 -Quick` | 退出码 0 |
| 深色截图 | 靛紫主色、Inter 标题、玻璃面板、无青柠主光 |
| 浅色截图 | 浅色画布、白色玻璃卡片、深色文字、浅色顶栏 |
| `GET /api/v1/tasks` | HTTP 200 |
| `GET /api/v1/materials/sources` | HTTP 200 |
| `task` / `updated_at` | 1,970 行 / NULL=0 |
| 素材库 | 2,351 active / 38 quarantine |

## 4 部署

| 项 | 值 |
|---|---|
| 新 exe sha256 | `18c6949ec056452f5921010d999221eb45d801a1f154d4c74365d89a1dbf1666` |
| 大小 | 16,147,323 字节 |
| 进程 | supervisor + worker（PID 12488 / 25820） |
| 版本 | 0.6.1（未发 Release） |
| 功能截图 | `docs/ui/screenshots/`（14 个视图 + `contact-sheet.png`） |
| UI 回滚备份 | `~/.mio_taskhub/deploy-20261029/dist-backend-only/`（新后端 + 旧青柠 UI） |
| 旧 exe 备份 | `~/.mio_taskhub/deploy-20261029/old-dist/` |

回滚：停止 TaskHub → 用 `dist-backend-only` 覆盖运行目录 → 启动 `TaskHub.exe hub --worker`。数据库无需变更。

## 5 边界

- 只改界面主题层，未改任务状态机、门禁、素材数据或 API；
- 素材业务效用仍未测量；
- 浅色主题做了核心变量覆盖，个别历史硬编码色仍可能在边缘页面出现；如发现具体页面，按产品缺陷单独修。
