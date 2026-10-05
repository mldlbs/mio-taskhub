# 🗓️ 想法落地闭环 P2 实现计划（包 C 结构化评审）

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | 286dd112 |
| 需求 | docs/taskhub/requirement-idea-landing-p2.md（FR-18~FR-25） |
| 设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结·包 C） |
| 更新日期 | 2026-09-27 |

---

## 🎯 目标

实现讨论双模式（free/review + roles）、评审关闭五段结构门控、行动项幂等转任务、Agent 角色 prompt 数据库+缓存+创建时快照、高风险词表迁 DB、MCP 透传与前端模式推荐。守则：**`mode=free` 与现状完全一致**、**门控只验结构**、**快照隔离热更新**。分支命名 `task-286dd112`。

---

## 📦 前置条件

| 项 | 状态 |
|---|---|
| requirement approved（100 分，FR-18~25） | ✅ |
| spec approved（v1.2 冻结稿，P0 已批） | ✅ 随本计划确认 |
| 设计阶段讨论结论 | ✅ 0cc2e1be |
| P0 底座（high_risk 字段、next_action 规则、Task breakdown 约定） | ✅ |
| 测试基线 | 905 passed, 1 skipped |

---

## 🔨 交付步骤

### 步骤 ① 讨论双模式字段 + 创建校验（FR-18/NFR-1）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/models.py`、`mio_taskhub/migrations.py`、`mio_taskhub/api/discussions.py` |
| 要点 | `Discussion` 增四个可空列：`mode`(默认 `free`)、`roles`(JSON 默认 `[]`)、`review`(JSON 可空)、`prompt_snapshot`(JSON 可空)；迁移走既有补列机制；`POST /discussions` 读 `mode`/`roles`——`mode` 非法值 422、`mode=review` 缺 roles/空列表 422、不传等价 free；`_disc_full` 只增键回显新字段 |
| 测试 | `tests/test_discussions_api.py`（或既有讨论测试文件）扩：free 不传参回归、review 缺 roles 422、非法 mode 422、旧讨论读取新字段为默认值 |
| 完成定义 | 验收 1/6 的创建侧通过 |

### 步骤 ② prompt 数据库+缓存+快照 + 词表迁 DB（FR-19/FR-20/NFR-2/4）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/models.py`（新表 `role_prompt`：`role`/`prompt`/`version`/`updated_at`）、`mio_taskhub/migrations.py`（建表+种子 5 角色：产品/技术/商业/合规/红队）、新 `mio_taskhub/api/config.py`（或并入既有 config 路由）、`mio_taskhub/next_action.py`（词表读取改造） |
| 要点 | 启动加载进进程缓存；`GET/PUT /api/v1/config/role-prompts`——PUT 写库、`version+1`、**使缓存失效**；词表同端点存储（key=`risk_vocab`），读取优先级 **env `MIO_IDEA_RISK_TAGS` > DB > 默认常量**，`is_high_risk` 对外语义不变；评审创建时一次性读取 roles 对应 prompt+version 快照进 `Discussion.prompt_snapshot`（单次读取，无半新半旧） |
| 测试 | 新 `tests/test_role_prompts.py`：种子存在、PUT 后新创建评审用新版、**进行中评审仍用创建时快照**、词表 env>DB>默认优先级、high_risk 契约回归（复用 `test_next_action.py` 断言） |
| 完成定义 | 验收 5/7 通过 |

### 步骤 ③ 评审关闭结构门控 + `review` 落库（FR-21/NFR-3）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/api/discussions.py`（`close_discussion`） |
| 要点 | `mode=review` 的 close 读 body `review` 字段并五段校验：`risks`≥1、`divergences` 非空、`suggestions` 非空、`decisions`≥2、`action_items`≥1 且逐条含 `id`(会话内唯一)/`owner`/`action`/`due`(YYYY-MM-DD)/`status`(pending\|doing\|done)/`task_id`；缺任一 → **422**，detail 指明缺哪段；达标 → `review` JSON 落库、conclusions/summary 照旧、置 closed 并发事件；`mode=free` 分支代码路径不动 |
| 测试 | 扩讨论测试：五种缺项分别 422 且 detail 含段名、达标落库回读、free close 回归、`due` 格式与 `status` 枚举 422、`id` 重复 422 |
| 完成定义 | 验收 2 通过 |

### 步骤 ④ 行动项幂等转任务（FR-22）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/api/discussions.py`（新 `POST /discussions/{id}/convert`） |
| 要点 | body `{item_ids?: list}`，缺省=全部 `task_id` 为空的条目；幂等——已带 `task_id` 条目跳过返回原 id；新建 `Task(title=action, description=含 owner/due, acceptance_criteria=action, due_at=due, idea_id=讨论.idea_id, stage=ready)`；多条**单事务**任一失败整体回滚；成功 `task_id` 回写持久化；`emit_event` 发转换事件 |
| 测试 | 扩讨论测试：首次转换创建任务（断言 acceptance_criteria/stage/due/idea_id）、同条目二次转换不重复创建、批量部分失败整体回滚、缺省参数转全部未转条目、讨论未关闭/非 review 是否允许（按设计：行动项在 review 关闭后产生，convert 对无 review 的讨论 422） |
| 完成定义 | 验收 3 通过 |

### 步骤 ⑤ MCP 透传（FR-23）

| 项 | 内容 |
|---|---|
| 文件 | `mio_taskhub/mcp_server.py`（`taskhub_open_discussion` 增 `mode`/`roles`；`taskhub_close_discussion` 增结构化 `review` 参数） |
| 要点 | open 缺省 `mode=free` 向后兼容；close 的 review 参数透传受步骤 ③ 同一门控，422 detail 原样回传；工具 desc 同步更新 |
| 测试 | 扩 MCP 相关测试（或 API 层等价断言 + 工具签名检查）：透传落库、close 缺段 422 信息不丢失 |
| 完成定义 | 验收 4 通过（MCP 生效需重启 opencode MCP 进程，验收注明） |

### 步骤 ⑥ 前端模式推荐 + 评审 UI + 行动项（FR-24）

| 项 | 内容 |
|---|---|
| 文件 | `web/src/components/IdeasView.jsx`、`web/src/api.js`、`web/src/index.css` |
| 要点 | 讨论入口模板下拉「讨论会 / 评审会（结构化）」；预选：`new/fermenting` 且无 goal → free，`formed` 或有 goal → review；cockpit `high_risk=true` → 预选 review 且 roles 默认含「红队」（可取消）；评审关闭结构化表单（五段输入 + 行动项行内编辑）；行动项列表（状态 chip、已转「查看任务」、未转「一键转任务」→ convert，422 detail 展示）；`api.js` 增 `convertActionItems`、close 带 review |
| 测试 | `npm run build` 绿；新旧讨论渲染无回归 |
| 完成定义 | 验收 7 通过，构建绿 |

### 步骤 ⑦ API 文档 + 全量回归（FR-25/NFR-5）

| 项 | 内容 |
|---|---|
| 文件 | `docs/taskhub/286dd112/api.md`（新，P2 端点契约） |
| 要点 | 契约含：discussions 创建 mode/roles、close 门控与 review 结构、convert、config/role-prompts、MCP 变更、错误码；含「变更记录」H2；过质量门（≥80 且 errors=0）→ draft→review→approved |
| 测试 | 全量 pytest 只增不减（基线 905）；`npm run build`；pre-push 门控 spec/api/plan approved + FR-18~25 真实引用 |
| 完成定义 | 验收 8 通过，四文档全绿 |

---

## 🧪 测试与构建

| 项 | 命令 |
|---|---|
| 后端全量 | `.venv\Scripts\python.exe -m pytest -q --tb=short`（基线 905 passed, 1 skipped，只增不减；两实例不并发防抢临时库） |
| 前端构建 | `npm run build`（`web/`） |
| 打包 | `packaging\build.ps1 -Quick`（命令文本不得含仓库名字面） |
| 门控 | 提交前 `taskhub_doc_quality`；pre-push 需 spec/api/plan approved + FR 真实引用 |

---

## 🔗 FR 追溯

| 步骤 | FR |
|---|---|
| ① | FR-18 |
| ② | FR-19、FR-20 |
| ③ | FR-21 |
| ④ | FR-22 |
| ⑤ | FR-23 |
| ⑥ | FR-24 |
| ⑦ | FR-25（回归） |

---

## ⚠️ 风险与对策

| 风险 | 对策 |
|---|---|
| free 行为被门控误伤 | free 分支独立代码路径 + 回归专测（验收 1） |
| 快照被热更新污染 | 创建时单次读取落 `prompt_snapshot`，进行中只读快照（验收 5 专测） |
| convert 重复创建任务 | task_id 非空即跳过 + 幂等专测（验收 3） |
| 批量转换半截数据 | 单事务整体回滚，失败专测断言无残留任务 |
| 词表改 DB 引入行为回退 | env > DB > 默认优先级专测；high_risk 既有断言全量回归 |
| SQLite 补列静默失败 | 复用 P1 补列机制（PRAGMA table_info 检查），旧库兼容测试 |
