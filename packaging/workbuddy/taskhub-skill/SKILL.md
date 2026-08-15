---
name: taskhub-assistant
description: 任务中心助手：查看任务看板与进度、创建/推进任务、接收各 agent 执行结果。用户提到"看板/任务/进度/派活/待办/活干完没/安排"时使用。
---

# TaskHub 任务中心助手

本地跨 agent 任务中心（服务 http://127.0.0.1:48620）。用户通过对话使用，全程无需离开聊天窗。

## 看板渲染
- 优先调用 `taskhub_status`（MCP 工具）获取全局状态，渲染成 markdown 表格（阶段 | 数量 | 任务列表），不要贴原始 JSON。
- 只读询问（看进度/看板/详情）时只调 `taskhub_status` / `taskhub_get_task` / `taskhub_list_tasks`，不创建不修改。
- **默认双输出**：除了文字表格，同时用产物面板内嵌任务画布（见下方「嵌入画布」），让用户直接看到可视化。

## 建任务
- 用 `taskhub_create_task` 创建，写清 description 与 acceptance_criteria（验收标准）。
- 提交前先向用户复述标题+描述+验收标准，确认后再提交。
- 默认 stage=brainstorming，任务需走完 理解→设计→计划 才可被领取执行。

## 推进阶段
- `taskhub_advance_stage`：
  - →design 需 spec_path（设计文档路径）
  - →planning 需 plan_path（计划文档路径）
  - →done 需 review_result（审查结论）
  - 产出物缺失时先向用户要，不要伪造。

## 执行与汇报
- 领取任务：`taskhub_register` → `taskhub_claim` → 执行中周期性 `taskhub_heartbeat` 上报进度。
- 完成后 `taskhub_submit_result(success, result)`，并向用户一句话汇报：「任务 X 完成：…」/「任务 X 失败：原因」。

## 想法与讨论（需求发酵）
- 用户说「记个想法 / 有需求 / 有个点子」：用 `taskhub_add_idea` 记录下来（标题/描述/项目），并告诉用户可在界面「想法」里看到和推进。
- 用户说「开会 / 讨论一下 / 聊聊这个想法」：用 `taskhub_open_discussion`（绑定 idea 或 task）→ `taskhub_discussion_messages` 读取已有内容 → `taskhub_reply_discussion` 回复参与。
- 需要用户决策时用 `role=ask` 提问；用户回复会以 `role=user` 回到同一讨论。
- 讨论有结论后用 `taskhub_close_discussion` 写结论；可据此把想法推进为 formed / broken_down（`taskhub_update_idea status`）。

## 嵌入画布（产物面板内嵌，不新开浏览器）
- 用户要看任务画布/流程图时，**优先在产物预览面板里内嵌**，而不是丢给浏览器 Tab。
- 方法：读取本技能同目录的 `embed-bridge-template.html`，把里面两处 `__HUB_ORIGIN__` 替换成实际 hub 地址 `http://127.0.0.1:48620`，然后把它作为 HTML 产物输出到产物预览面板。
- **模板是纯 iframe（无边框无标题条），产物面板里会直接显示深色任务画布，像原生组件**。若产物面板渲染宽高不完整，可在 iframe 外包一层 `height:100%` 容器。
- 若产物面板不支持 iframe 加载本地地址，fallback：内置浏览器打开 `http://127.0.0.1:48620/#/embed`。
- 也可直接让用户访问 `http://127.0.0.1:48620/embed-bridge.html`（服务端托管版，已自适应端口）。
- 内嵌成功标志：产物面板里出现深色任务画布（7 阶段节点流 + 顶部统计，无外框）。

## 工具清单
- 查看：`taskhub_status`（全局）、`taskhub_list_tasks`（看板）、`taskhub_get_task`（详情）
- 操作：`taskhub_create_task`、`taskhub_update_task`、`taskhub_advance_stage`、`taskhub_cancel_task`
- 子任务：`taskhub_add_subtask`、`taskhub_update_subtask`
- 讨论/追溯：`taskhub_add_discussion`、`taskhub_add_gitref`、`taskhub_add_history`
- 执行：`taskhub_claim`、`taskhub_heartbeat`、`taskhub_submit_result`
