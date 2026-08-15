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
- 浮动面板依赖 pywebview（已装；绿色版打包需处理 pythonnet/WebView2，spec 尚未加 widget EXE）
- hermes-memory 记忆图服务当前不可用（工具返回 JSON 解析错误）

## 下一步（等用户选择）
- **P1 编排**：Task `depends_on` 支持列表（DAG）；依赖满足自动放行 READY；调度器扩展；空闲 agent 自动捞取；编排视图
- **事件订阅推送**：task 全局事件 + `taskhub_poll_events(seq)` 增量订阅；WS 推给 UI/浮动面板
- **Idea → Task 一键拆解**：`FORMED/BROKEN_DOWN` 时生成任务集并与 idea 关联
- 绿色版加 `mio-taskhub-widget.exe`（PyInstaller 第三个 EXE + pywebview hiddenimports）