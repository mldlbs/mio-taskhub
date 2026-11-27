# 部署运维：研发评审会

## 文档信息

| 项 | 值 |
|---|---|
| 任务 ID | a20f3f9f |
| 文档类型 | runbook |
| 版本 | v1.1 |
| 状态 | draft |
| 负责人 | 产品管理专家团 |
| 日期 | 2026-11-26 |
| 上游 | plan.md / api.md |
| 范围 | 开发环境起停、打包部署、验证、回滚、常见故障 |

## 1. 环境与依赖

| 项 | 值 |
|---|---|
| 服务地址 | `http://127.0.0.1:48620/api/v1` |
| 工作目录 | `E:/work/code/agent-dev`（启动 hub 时必须） |
| 数据库 | SQLite，create_all 自动建 review_point 表，零迁移 |
| 前端 | `web/dist`，纯前端改动可直接拷贝，无需重建 exe |
| 后端 | 任何 .py 改动需 PyInstaller 重建 exe |
| LLM | 可选增强，走现有代理；超时 8s 降级 |
| 依赖端点 | 既有 `/decisions`、`/{task_id}/reviews`、`/doc`、`stage gate` |

- 启动 hub 只能 WMI `Create(CommandLine, CurrentDirectory)`，cwd 必须为 `E:/work/code/agent-dev`；mutex 单实例锁会静默 return。
- 打包时 dist/vN 先移出 dist/；免断服打包用 `--distpath`/`--buildpath` 独立目录。

## 2. 部署步骤

- 1. 后端改动：确认修复已在当前分支（`git branch --contains`）；PyInstaller 主程序 → 拷 8 个分发文件 + `workbuddy/` → PyInstaller installer。
- 2. 前端改动：`npm run build` → `cp web/dist/*` 到 `E:\Program Files\TaskHub\hub\_internal\web\dist\`（dest 存在会嵌套，先 ls 核实顶层）；Tauri 壳不重启不换 bundle。
- 3. 杀 hub：先杀 supervisor 再杀 worker（遍历所有 TaskHub.exe），避免 supervisor 自动拉起抢回端口。
- 4. 启动：WMI Create，`CommandLine="E:\Program Files\TaskHub\hub\TaskHub.exe" hub`，`CurrentDirectory=E:/work/code/agent-dev`。
- 5. 验证：`curl /api/v1/tasks/inbox` 返回 200；`GET /reviews/points?task_id=<t>` 可用。
- 6. MCP 工具：新增工具需**重启 WorkBuddy 宿主**才可见；比工具数确认加载新代码。
- 7. 收尾三件套：提交 + 推送 + **合入 main**（孤儿分支会被后续构建回退）。

## 3. 验证清单

| 项 | 方法 | 通过标准 |
|---|---|---|
| 服务存活 | `curl /api/v1/tasks/inbox` | HTTP 200 |
| 提取 | `POST /reviews/extract` 对 decision-entity/api.md | ≥1 个 RP 点，version 为 sha256 |
| 契约 | pytest TC-1~22 | 全绿 |
| 前端 | 评审页三栏加载、六动作可点 | 无 console error |
| MCP | 重启宿主后列工具 | 7 个新工具可见 |
| 打包 | PYZ 反查新模块 | 含 `review/` 子包（extract / h5）/ `review_point` |
| 文档门 | `GET /tasks/{id}/doc/quality` | requirement/spec/api ≥80，errors=0 |

- 线上验行为用 `__verify_` 前缀任务；清理用 `DELETE ?confirm=true`（POST cancel 会 405）。
- 批量写操作限速 ≥0.4s 间隔，429 重试补跑。

## 4. 回滚

- 前端回滚：从 `dist_archives/vN/` 取上一版 `web/dist` 拷回；纯前端生效无需重启 exe。
- 后端回滚：从 `dist_archives/vN/` 取上一版 exe 覆盖；先杀全部 TaskHub.exe 再覆盖。
- 数据回滚：review_point 为新增表，drop 不影响既有数据；但已产生的 Discussion/TaskReview/Decision 是写在既有表，需在测试环境先演练回滚脚本再上生产。
- 关会产物不可变：回滚不删除已写 TaskReview，避免总账断链。
- 失败回退阈值：H5 pilot Recall<30% 时功能保留，仅把价值叙事从"提前发现风险"改为"结构化留痕 + 逐条裁决"，无需代码回滚。2026-11-26 实测 44%（5 样本 / 22÷49），落 30-50% 观察区，价值叙事已固定为"防遗漏 + 结构化留痕 + 逐条裁决"，代码不回滚。

## 5. 常见故障

| 现象 | 原因 | 处理 |
|---|---|---|
| MCP 报"无法连接 48620"但 curl 正常 | MCP 那一跳连接失败（非服务问题） | 直接走 REST；或重启宿主重连 |
| 新 MCP 工具不可见 | 工具面在连接建立时固定 | 重启 WorkBuddy 宿主 |
| 关会 409 | 门控未过（点未终态 / decisions<2 / action_items<1） | 看响应 gaps 逐项补齐 |
| 驳回/暂缓 422 | 理由为空或 <8 字 | 补 ≥8 字理由 |
| 第二次 active 会议 409 | 单 active 约束 | 用现有会议，勿重复创建 |
| 重复关会 409 | TaskReview 不可变 | 查 GET 确认历史 |
| 提取 0 点 | 文档无 H2/H3/表格，或非四类文档 | 检查文档结构与 kind |
| 批量写 429 | 600 req/min/IP 限流 | 退避重试，间隔 ≥0.4s |
| 前端无变化 | dist 未拷到 `_internal/web/dist/`，或 Tauri 壳未重启 | 核对路径 + 重启壳 |
| exe 更新不生效 | 修复未合入 main 被后续构建回退 | `merge-base --is-ancestor` + 线上 bundle 反查 |
