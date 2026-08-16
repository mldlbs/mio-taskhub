# 工作状态 · 2026-08-15

## 一句话
mio-taskhub 需求工作台已交付 P0（想法 + 讨论）；端口 48620；浮动面板可用。

## 已完成（已提交 git: 1ea51e9 → ea6e406）
1. **harness 用户侧无感接入**：对话看板（`/board/summary` + MCP `taskhub_status`）、AGENTS 引导注入（opencode/codex/claude/hermes）、workbuddy taskhub 助手 skill
2. **端口迁移 8080 → 48620**：代码默认/引导/文档/脚本全量替换（历史 spec/plan 记录保留 8080 未动）
3. **真嵌入画布（workbuddy 产物面板）**：`web/public/embed-bridge.html` + skill 模板（`__HUB_ORIGIN__` 占位）+ SKILL.md「嵌入画布」指令；看板渲染「表格 + 内嵌画布」双输出
4. **浮动任务中心面板**：`packaging/run_widget.py`（pywebview + WebView2），置顶、可缩放、全功能 SPA、自定义图标（`web/public/icon.ico`）；绿色版可打包 `mio-taskhub-widget.exe`
5. **想法 + 讨论（需求工作台 P0）**
   - 模型：`Idea` + `IdeaStatus`（new→fermenting→formed→broken_down / archived / cancelled）；`Discussion.idea_id` 双向绑定；DB 增量迁移
   - API：`api/ideas.py`（CRUD + 状态流转 + 详情含讨论）、`api/discussions.py`（创建/列表/详情/发消息/关闭，task 或 idea 绑定，WS 广播）
   - MCP 新增 7 工具：`taskhub_add_idea / ideas / update_idea / open_discussion / discussion_messages / reply_discussion / close_discussion`
   - 前端：侧栏「想法」视图（`web/src/components/IdeasView.jsx`：列表 + 新建 + 讨论气泡，user 靠右 / agent / ask 靠左），Rail 导航新增、App 每 5s 轮询 ideas
   - 测试：`tests/test_ideas_api.py` 7 个；全部 140 通过；修复 `IdeaStatus.can_advance` 用 set 导致顺序随机的 bug（改有序 list）
   - 引导：skill + `setup-agent.ps1` 模板新增「想法与讨论」节，安装副本已同步
   - 打包：exe 重打含新工具 + 新前端；MCP 握手验证通过

## 当前运行
- hub：`127.0.0.1:48620`（生产库 `~/.mio_taskhub/taskhub.db`），最近 PID 由 launcher 拉起，日志 `D:\Users\gf1913\Temp\opencode\hub_48620.out.log`
- 浮动面板：`python packaging/run_widget.py`（源码模式跑过，PID 曾是 31372/28708）
- 前端已构建 `web/dist`（含 IdeasView）；vite proxy 指向 48620

## 关键约束 / 环境
- 测试隔离：`MIO_TASKHUB_DB` 指向临时库；pytest 需在 `mio-taskhub/` 目录跑
- 前端构建：`web/` 目录 `npm run build`
- 打包：`python -m PyInstaller mio-taskhub.spec --noconfirm`（datas 用绝对路径 SPECPATH，web assets 进 `_internal/web/dist`）
- 浮动面板依赖 pywebview（已装；绿色版已含 widget EXE：`mio-taskhub-widget.exe`，spec 加了第三个 EXE + webview hiddenimports + icon，冒烟测试通过）
- hermes-memory 记忆图服务当前不可用（工具返回 JSON 解析错误）

## 已完成（编排/事件/拆解，2026-08-15 会话）
- **P1 编排（DAG）**：`depends_on` 升级 JSON 数组 + 迁移；调度器依赖满足自动放行 READY（`wiring._release_dependencies`）；环检测（`planner.detect_cycle`，create/update/breakdown 三入口）；board 依赖阻塞告警；FlowView 依赖角标 + TaskDetail 依赖区块
- **事件订阅推送**：`Event` 表加 entity/entity_id；统一 `emit_event`（与业务同事务）+ `broadcast_for_event`；全量写操作埋点；`GET /api/v1/events?after_seq=N` 增量（不传=最近200/=0=全部/>N=增量）；MCP `taskhub_poll_events`
- **Idea → Task 一键拆解**：`Task.idea_id`；`POST /ideas/{id}/breakdown`（单事务、ref 依赖、幂等 409、环 422 回滚）；MCP `taskhub_breakdown_idea`；IdeasView 拆解表单
- 清理 7 个测试残留任务（生产库）+ 临时 DB 检查；README/MCP_INTEGRATION/使用说明 已更新
- 提交：`b0f2e9a`(spec) → `e40d64d`(plan) → 14 个实现任务 → `2ada194`(MCP depends_on 数组) → widget EXE 打包批次
- 测试：190 passed；`npm run build` 通过；生产 hub(48620) 运行中，隔离实例端到端验证依赖放行/事件/拆解/环检测/409 全通过

## 已完成（自动捞取 + 编排视图，2026-08-15 第二会话）
- **空闲 agent 自动捞取**：`_claim_for(agent, db, agent_type=None)` 纯函数 + SQLite 条件更新原子领取（一个 task 只一个 run）；调度器 `wiring._assign_to_idle_agents()` Task-first 分配（priority desc + FIFO，避开忙 agent）；事件 `task_assigned` 含 run_id；claim 幂等返回已分配 run；并发测试覆盖
- **move_to_stage**：`POST /tasks/{id}/stage/move` 任意跳转（拖拽用），保留终态保护 + 产出物校验，与 advance_stage 共享 `_apply_stage_requirements` helper；MCP `taskhub_move_to_stage`
- **FlowView 拖拽换阶段**：HTML5 DnD + prompt 填产出物；`req()` 修为非 2xx 抛错（错误可见）
- **FlowView 依赖连线**：SVG overlay + ResizeObserver/MutationObserver + rAF 重绘；useMemo 稳定引用消除无限重渲染（Playwright 验证无警告）
- **TopoView 拓扑视图**：Kahn 分层 DAG，节点含 depth/indegree/outdegree（未来 CPM），Rail「拓扑」入口；Playwright 验证渲染正常
- 提交：`60928fe`(spec) → `34c6b80`(plan) → Task1-7 实现 → `ef8c35b`(TopoView)；测试 214 passed

## 下一步（可选）
- 绿色版加 `mio-taskhub-widget.exe` —— 已完成
- 空闲 agent 自动捞取 —— 已完成
- 对话内拖拽编排 / 更细粒度编排视图 —— 已完成（拖拽换阶段 + 依赖连线 + TopoView）
- widget 系统托盘驻留 —— 已完成（pystray 图标：关窗隐藏、单击打开、菜单退出；widget EXE 打包含 pystray/PIL；spec `docs/superpowers/specs/2026-08-15-widget-tray-design.md`）
- 待做：agent 心跳离线标记（现在 register 即 online，无心跳线程）；关键路径（CPM）可视化；跨列依赖连线