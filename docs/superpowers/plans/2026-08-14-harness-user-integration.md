# 实施计划：用户侧无感接入

日期：2026-08-14
来源：docs/superpowers/specs/2026-08-14-harness-user-integration-design.md

## 依赖图

```
T1 后端 /board/summary ──> T2 MCP taskhub_status + instructions
T3 前端 /embed 视图（独立，可并行）
T4 guide 模板 + setup 注入（依赖 T2 的工具名/描述，但主体独立）
T5 workbuddy skill（依赖 T4 的 guide 内容）
```

## 子任务

### T1：后端 `/board/summary`（api + 测试）
- 新文件/函数：`mio_taskhub/api/board.py` 或并入 tasks.py
- `GET /api/v1/board/summary?agent=` 返回 counts/ready_queue/running/alerts/recent_done/next_steps
- 空库零值、agent 过滤、超时告警、next_steps 生成
- 测试：`tests/test_board_summary.py`
- 完成标准：测试通过

### T2：MCP `taskhub_status` + instructions（mcp_server.py + 测试）
- 新增 `taskhub_status(agent="")` 调 `/board/summary`
- instructions 扩为对话使用规范（触发词/看板渲染/操作确认/汇报）
- 测试：`tests/test_mcp_status.py`（mock HTTP）
- 完成标准：测试通过

### T3：前端 `/embed` 视图（web/ EmbedView + 路由 + 构建）
- `web/src/components/EmbedView.jsx`：复用 FlowView 数据，去掉导航，紧凑样式，WS 刷新
- `web/src/App.jsx` 加 `#/embed` 路由（embed 参数隐藏 chrome）
- `embed-bridge.html`（打包入 exe，iframe → `/#/embed`）
- 验证：`cd web && npm run build`；浏览器打开 `/#/embed` 渲染 + 刷新
- 完成标准：构建通过 + 浏览器可见

### T4：guide 模板 + setup 注入（docs + packaging/setup-agent.ps1）
- `docs/harness/taskhub-guide.md`：规范正文
- setup-agent.ps1：注入 opencode/codex/claude 的 AGENTS/CLAUDE.md（幂等标记）+ 失败不阻塞
- 完成标准：脚本 dry-run 幂等追加

### T5：workbuddy skill（packaging/workbuddy/）
- `packaging/workbuddy/taskhub-skill/SKILL.md`（frontmatter triggers + 规范 + embed 链接）
- setup-agent.ps1 加第 5 步：复制 skill 到 workbuddy 技能目录（幂等）
- 完成标准：目录结构校验

## 验证

- 后端+MCP：`python -m pytest`
- 前端：`cd web && npm run build`
- 集成：hub 跑起来后手动调用 `/board/summary` + 浏览器 `/#/embed`