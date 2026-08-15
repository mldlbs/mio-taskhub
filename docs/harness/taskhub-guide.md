# mio-taskhub 使用规范（harness 引导模板）

把本文件追加到你的 agent 全局规则文件，即可让它在对话里无感使用任务中心。
支持：opencode（`AGENTS.md`）、codex（`AGENTS.md`）、claude code（`CLAUDE.md`）、hermes（`AGENTS.md`）。

```markdown
## mio-taskhub 使用规范

mio-taskhub 是本地跨 agent 任务中心（服务 http://127.0.0.1:48620）。用户通过对话使用，
全程无需离开聊天窗、无需开浏览器。

### 触发时机
- 用户提到：任务、看板、派活、进度、待办、活干完没、安排 等词时，主动调用 taskhub_status。
- 对话开始时若不知道当前任务情况，可先调一次 taskhub_status 建立上下文。

### 看板渲染
- 把 taskhub_status 结果渲染成 markdown 表格（阶段 | 数量 | 任务列表），不要贴原始 JSON。
- 只读询问（看进度/看板/详情）时只调 taskhub_status / taskhub_get_task / taskhub_list_tasks，
  不创建不修改任何数据。

### 建任务
- 用 taskhub_create_task 创建，写清 description 与 acceptance_criteria（验收标准）。
- 提交前先向用户复述标题 + 描述 + 验收标准，确认后再提交。
- 默认 stage=brainstorming；任务需走完 理解→设计→计划 才可被领取执行。

### 推进阶段
- 用 taskhub_advance_stage，按阶段带产出物：
  - →design 需 spec_path（设计文档路径）
  - →planning 需 plan_path（计划文档路径）
  - →done 需 review_result（审查结论）
  - 产出物缺失时先向用户要，不要伪造。

### 执行与汇报
- 领取任务：taskhub_register → taskhub_claim → 执行中周期性 taskhub_heartbeat 上报进度。
- 完成后 taskhub_submit_result(success, result)，并向用户一句话汇报：
  「任务 X 完成：…」/「任务 X 失败：原因」。

### 嵌入视图
- 用户要看可视化流程图时，打开 http://127.0.0.1:48620/#/embed（workbuddy 可用产物面板嵌入）。

### 工具
- taskhub_status 全局状态 | taskhub_list_tasks 看板 | taskhub_get_task 详情
- taskhub_create_task 建 | taskhub_update_task 改 | taskhub_advance_stage 推进 | taskhub_cancel_task 取消
- taskhub_add_subtask / taskhub_update_subtask 子任务 | taskhub_add_discussion 讨论
- taskhub_add_gitref / taskhub_add_history 追溯 | taskhub_claim / taskhub_heartbeat / taskhub_submit_result 执行
```
