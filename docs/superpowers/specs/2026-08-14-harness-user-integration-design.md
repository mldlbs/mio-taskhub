# 用户侧无感接入（harness 对话集成 + workbuddy skill + 嵌入视图）

日期：2026-08-14
状态：已确认

## 背景

mio-taskhub 的 MCP 工具链（15 工具）与一键配置已就绪，但**用户侧**仍是"开浏览器看板"。用户目标是：在 **opencode/workbuddy/codex/hermes 的桌面对话界面**里，像跟助手说话一样用 taskhub——看进度、派活、收结果，不离开聊天窗；workbuddy 还能把画布流程图真·嵌进客户端。

经澄清：
- 用户 = 本机小白，界面 = 各 harness 桌面客户端对话窗
- 形态全要：①对话内看板（只读）②对话内操作（建/改/验收）③真嵌入画布视图
- workbuddy 入口用自定义 Skill；其余客户端用全局指令注入
- 顺序：①→②→③，最终全做完

## 方案总览

三层：

```
                    ┌────────────────────────────────────┐
 用户(对话窗) ──→   │ ① 引导注入(AGENTS/skill/指令)        │
                    │   触发词 + 标准操作流               │
                    └──────────────┬─────────────────────┘
                                   ▼
                    ┌────────────────────────────────────┐
                    │ ② MCP 增强：taskhub_status 聚合工具  │
                    │   + 已有 15 工具(建/改/验收/推进)    │
                    └──────────────┬─────────────────────┘
                                   ▼
                    ┌────────────────────────────────────┐
                    │ ③ 后端 /board/summary + /embed 视图  │
                    │   (workbuddy 产物预览/内置浏览器)    │
                    └────────────────────────────────────┘
```

## 后端 API（api/tasks.py 或新 api/board.py）

### `GET /api/v1/board/summary?agent=xxx`

返回对话友好看板汇总：

```json
{
  "updated_at": "2026-08-14T10:00:00Z",
  "counts": {"brainstorming": 3, "design": 1, "planning": 0, "ready": 4,
             "implementing": 1, "review": 0, "done": 12, "cancelled": 1},
  "ready_queue": [{"id", "title", "priority", "target_agent_type", "project", "due_at", "created_at"}],
  "running": [{"id", "title", "stage", "claimed_by", "run_id", "progress", "heartbeat_at"}],
  "alerts": [{"level": "warning", "message": "任务 X 心跳超时待重领"}],
  "recent_done": [{"id", "title", "completed_at"}],
  "next_steps": ["可领取任务 N 个，最高优先级 ...", "有 M 个任务超过截止时间"]
}
```

- `agent` 可选：传入后 `running` 只列该 agent 的 run
- `next_steps`：后端生成简单建议（待领取数 / 超时告警数），供 agent 直接引用
- 空库返回零值而非报错

### `GET /api/v1/tasks/{id}`（已有）

供 skill / 引导中的"看单任务详情"复用，无需改。

## MCP（mcp_server.py）

### 新增 `taskhub_status`

- 参数：`agent: str = ""`（可选，传入后 running 只列自己）
- 调用 `/board/summary`，返回 `_fmt(data)`
- 描述文案强调"一次调用即获全局上下文 + 下一步建议，优先用它开头"

### 增强 `mcp.instructions`

把 instructions 扩为一段"对话使用规范"：触发词、看板渲染成 markdown 表格的约定、操作前确认约定。让 agent 无需外部文档也能用。

## 引导注入（setup-agent.ps1 增强）

### 统一规范模板

新增 `docs/harness/taskhub-guide.md`，内容：

```markdown
## mio-taskhub 使用规范
- 触发词：用户提到 任务/看板/派活/进度/待办/活干完没/安排 时，主动调用 taskhub_status
- 看板渲染：把 taskhub_status 结果渲染成 markdown 表格（阶段|数量|任务列表），不贴原始 JSON
- 只读询问：问进度/看板 → 只调 taskhub_status / taskhub_get_task，不创建不修改
- 建任务：taskhub_create_task，写清描述/验收标准；给用户复述标题+描述确认后提交
- 推进阶段：taskhub_advance_stage 需带产出物路径/审查结论；缺失则先向用户要
- 汇报：任务提交 result 后，向用户一句话汇报结果（成功/失败+原因）
- 完整工具列表见 taskhub 文档，不确定时调用 taskhub_status
```

### 各客户端注入点

| 客户端 | 注入位置 |
|---|---|
| opencode | `~/.config/opencode/AGENTS.md`（无则创建，追加规范段） |
| codex | `~/.codex/AGENTS.md`（追加） |
| claude code | `~/.claude/CLAUDE.md`（追加） |
| workbuddy | 独立 Skill（见下） |
| hermes | 追加到 `~/.hermes/config.yaml` 的全局 instructions？—— 无 AGENTS 机制；改为随包提供说明 + 也写入 `~/.agents/AGENTS.md`（hermes 读取）。需实施时按 hermes 实际机制核实，若不可行则退化为文档说明 |

- 幂等：检测已有 `mio-taskhub 使用规范` 标记，已注入则跳过
- 失败不阻塞：注入失败仅告警，不影响其他步骤

## workbuddy Skill（层③载体）

新增目录 `packaging/workbuddy/taskhub-skill/`：

- `SKILL.md`：frontmatter（name/description/triggers 关键词：看板/任务/进度/派活/验收）+ 使用规范正文（同 guide，但面向 workbuddy 对话）+ 嵌入视图链接说明
- `assets/`（可选）：嵌 view 用说明截图/HTML
- setup-agent.ps1 增加第 5 步：检测 workbuddy 技能目录，把 skill 目录复制进去（幂等，已存在跳过）

## 嵌入视图（层③）

### 新增 `/embed` 精简页

- 前端新增 `EmbedView`（`web/src/components/EmbedView.jsx`）：
  - 复用 FlowView 节点流数据/布局，但去掉侧栏/顶栏/全局导航
  - 紧凑样式，适合 iframe 小窗（宽度 360px+）
  - 保留 WS 自动刷新
- 路由：`/#/embed`（或 `/embed`），embed 参数控制隐藏 chrome
- 后端无需新接口（复用现有 `/tasks` + WS）
- 产物 HTML 包装：打包一个 `embed-bridge.html`（打包进 exe），iframe 指向 `http://127.0.0.1:8080/#/embed`，供 workbuddy 产物预览面板打开——不依赖浏览器导航权限

### workbuddy 打开方式（二选一，实测定）

1. 产物预览面板打开 `embed-bridge.html`（本地文件，内含 iframe → 本地 URL）
2. 内置浏览器直接打开 `http://127.0.0.1:8080/#/embed`

## 测试

- 后端：`/board/summary` 空库零值、有数据计数、agent 过滤、next_steps 生成
- MCP：`taskhub_status` 调用成功、instructions 含规范
- 前端：`npm run build` 通过 + 浏览器验证 `/embed` 渲染与 WS 刷新
- setup 注入：mock 检查 AGENTS.md 幂等追加
- workbuddy skill：目录结构校验（不依赖真实客户端）

## 不做的事（YAGNI）

- 不做各 harness 专属 UI 插件（依赖对方 SDK，成本高）
- 不做对话内可视化拖拽（markdown 已够用）
- 不做 hermes 专有 skill（无确定性注入机制，退化为 AGENTS/文档）
- 不重构 FlowView 为可复用库（EmbedView 直接复用组件）
