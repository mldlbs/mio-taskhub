# mio-taskhub 前端重构 — 会话记忆（2026-08-12）

> hermes-memory MCP 服务当时报错，此文件为备份记录。

## 项目
- **mio-taskhub**：跨 agent 任务中心，单用户本地服务。仓库 `E:\work\code\agent-dev\mio-taskhub`。
- 后端：FastAPI + SQLModel + SQLite（`~/.mio_taskhub/taskhub.db`），端口 8080。
- 前端：React 18 + Vite（`web/`），构建产物 `web/dist/`。
- Agent 通过 MCP（`mio_taskhub/mcp_server.py`）与 REST `/api/v1` 接入。

## 前端重构（本次会话交付）
- **设计方向**：Mono-Electric —— 石墨单色画布 + 单一电光绿 accent（`--accent`），仅用于「在线/运行」信号。
- **字体**：Unbounded（展示）/ Sora（正文）/ JetBrains Mono（数据）。严禁 Inter/Space Grotesk/Geist。
- **布局**：窄栏（66px Rail）+ 横向管线看板（6 状态列）。
- **文件**：
  - `web/src/index.css` —— 完整设计系统（CSS 变量：v1 基础 / v3 全宽+字号 / v4 微交互 / 高对比 `html[data-contrast="high"]`）。
  - `web/src/App.jsx` —— 外壳（Rail + MissionBar + 视图切换 + 详情抽屉 + 模态）。
  - `web/src/constants.js` —— LANES/优先级/格式化（fmtDur/fmtAgo/agMono/agColor）。
  - `web/src/components/`：Rail、MissionBar、BoardView、TaskCard、ListView、PlanView、CreateModal、TaskDetail。
- **功能**：任务详情抽屉、拖拽精确落点占位、键盘可达（Enter 打开 / Alt+←→ 移列）、Agent monogram、相对时间、列内优先级排序、列头时长汇总、列表搜索/排序/状态筛选、夜间计划时间轴（按优先级分层+图例）、WS 自动重连（指数退避）、仪表点击跳转列并闪光、视图记忆 + 对比度开关（localStorage: mio.view / mio.contrast）、工程网格底纹、骨架屏。
- **对比度实测**：默认主文 17:1 / 次级 9:1 / 弱文字 5.3:1；高对比档分别 19/11/7.7。
- Logo：`web/public/icon.png`（用户提供 256×256），用于 favicon + Rail + MissionBar。

## 后端配套改动
- `mio_taskhub/api/tasks.py`：`list_tasks` 返回全字段（description/est_duration/attempt/schedule/created_at…）。
- `mio_taskhub/api/runs.py`：`heartbeat` 时任务状态 claimed→running（让「在线」态生效）。
- `web/src/api.js`：新增 cancelTask/getTask/updateTask/addSubtask/updateSubtask/addDiscussion（对应合并后的富接口）。

## Git 状态
- 分支：`master`（主目录）与 `feature/task-detail`（worktree `.worktrees/task-detail`）已**合并**（合并提交 `4428cf1`）。
- master 已含扩展模型（acceptance_criteria/due_at/labels/project/workspace/files/deliverables + 子表 subtask/gitref/history/discussion）。
- 本次会话补提交：`eae3982`（logo 批次）、`b429f8c`（api.js 富接口调用）。
- **并行会话**正在改 `web/src/App.jsx`（详情抽屉接 getTask/子任务勾选）与 `CreateModal.jsx`（富字段表单），未提交，勿动。
- 工作区仅剩这两个进行中文件未提交。

## 环境要点
- `~/.mio_taskhub/taskhub.db` 是**主仓库与 worktree 共用**库；schema 必须与代码模型一致（曾因 worktree 扩展 schema 与主仓库模型不匹配导致 create_task 500）。
- **OpenCode 桌面端会监督/自动重启 8080 的 uvicorn**（`python -m uvicorn mio_taskhub.main:app --port 8080`），杀进程会被拉起。
- 主仓库还嵌套在更大的 git 仓库 `E:\work\code\agent-dev`（父仓库）里。
- 验证方式：`npm run build`（web/）+ `python -m pytest tests/`（60 passed）+ Playwright E2E（脚本放 `D:\Users\gf1913\Temp\opencode\`）。
- 启动/演示：`python -m uvicorn mio_taskhub.main:app --port 8080`（可设 USERPROFILE 隔离 DB）。
