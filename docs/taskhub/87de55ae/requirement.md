# 📋 想法驾驶舱「一键生成草稿」+ 本地假设 hid 补全 需求规格（P5）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.2 |
| 状态 | approved |
| 任务 | 87de55ae（branch task-87de55ae） |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.3 冻结，驾驶舱分节渲染） |
| 来源想法 | df1ff13c（想法：一键生成草稿；用户原话「应该一键生成吧，我去修改吧，让我从零写还是有点困难」） |
| 前序需求 | requirement-idea-landing-p0/p1/p2/p3.md（FR-1~FR-29）、requirement.md(P4: FR-30~FR-33) |
| 负责人 | opencode（mio-taskhub） |

## 背景与问题

驾驶舱的 8 个结构化字段（`goal`/`success_metric`/`constraints`/`out_of_scope`/`mvp_scope`/`tags`/`assumptions`/`risks`）**只能手写**：用户点开一条想法，右侧 7 个区块全是一屏灰色占位提示（「给【谁】解决【什么问题】…」），从零写门槛高、易放弃——实测用户反馈「让我从零写还是有点困难」，于是一屏空块看起来还「每块都一样」。

同时发现一个连带缺陷：用「编辑」表单录入的本地假设条目**没有 `hid`**，导致「回写…」按钮点击后静默无反应（`openHypWrite` 取不到 hid → `saveHypWrite` 直接 return），FR-15 的人工回写链路对**新录入**数据不可用。

本任务给驾驶舱加「AI 起草 + 人工微调」：**一键生成草稿 → 填进编辑表单 → 人改完再保存**，并修掉 hid 缺陷。

## 目标与非目标

**目标（可衡量）**：

- 新增端点 `POST /api/v1/ideas/{idea_id}/draft-fields`：用想法标题 + 描述（+ 已填字段）由 LLM 生成 8 字段草稿 JSON，**纯生成不落库**；
- 前端「✏️ 结构化字段」加「✨ 生成草稿」按钮：点击 → loading → 自动打开编辑表单填入草稿，已填字段默认**不覆盖**（需二次确认），表单标注「AI 草稿，请核对」；
- 录入的本地假设自动获得 `hid`，`PATCH /ideas/{id}/assumptions/{hid}` 回写可用（前端「回写…」不再静默无效）；
- 全量 pytest ≥924 passed + 1 skipped 只增不减；文档链 requirement/spec/api/plan approved。

**非目标（明确排除）**：

- 不自动保存草稿（必须人工确认后保存；生成动作零写库、零 IdeaChange）；
- 不做记忆/RAG 注入、不做多轮对话式补全（单次生成）；
- 不改动既有字段模型与既有端点契约（只新增 1 个端点 + 补 hid）；
- 不引入新的 LLM SDK/依赖（用标准库 urllib 直连 OpenAI 兼容 /chat/completions）；
- 不把 apiKey 写入日志、响应或任何前端可见位置。

## 前序 FR 全集枚举（供追溯）

本任务继承前序全部需求编号：FR-1、FR-2、FR-3、FR-4、FR-5、FR-6、FR-7、FR-8、FR-9、FR-10、FR-11、FR-12、FR-13、FR-14、FR-15、FR-16、FR-17、FR-18、FR-19、FR-20、FR-21、FR-22、FR-23、FR-24、FR-25、FR-26、FR-27、FR-28、FR-29、FR-30、FR-31、FR-32、FR-33。

## 功能需求

| FR | 描述 | 分期 | 验收 |
|---|---|---|---|
| FR-34 | **生成草稿端点**：`POST /api/v1/ideas/{idea_id}/draft-fields`。入参可选 `{overwrite?: bool, fields?: list}`；服务端读 Mio LLM 配置（`~/.mio-intelligence/config.json` 的 `apiUrl`/`apiKey`/`model`，env `MIO_LLM_*` 可覆盖）→ 以标题+描述+已有字段构造结构化 prompt → 调 OpenAI 兼容 `/chat/completions`（`response_format=json_object` 优先，失败则从文本里截取 JSON）→ 返回 `{draft:{goal,success_metric,constraints,out_of_scope,mvp_scope,tags[],assumptions[],risks[]}, source:"llm", model, elapsed_ms}`。**不写库**（不产生 IdeaChange）。降级：未配置/不可用 503、超时(≤30s) 504、返回非法 JSON 502、想法不存在 404 | P5 | 正常 200 且 8 字段齐全；三种降级码正确；调用前后 idea.version 与 IdeaChange 数不变 |
| FR-35 | **前端草稿交互**：「✏️ 结构化字段」头部「编辑」旁加「✨ 生成草稿」；点击 → 按钮 loading（禁用重复点击）→ 成功后打开编辑表单填入草稿；**合并策略**：仅填补空字段，已有非空字段保留（若显式点「覆盖已有内容」则覆盖，需 confirm）；表单顶部提示「AI 草稿，请核对后保存」；失败按状态码提示（503「LLM 未配置/不可用，可手动填写」、504「生成超时，可重试」），面板其余部分不受影响 | P5 | 点击后表单被填入草稿；已有内容不被静默覆盖；失败有明确提示且不写脏数据 |
| FR-36 | **本地假设 hid 补全**：整表 `PATCH /ideas/{id}` 的 assumptions 归一化路径为每条缺 `hid`/`id` 的条目 `setdefault` 生成稳定 hid（保留既有 hid 不变）；生成规则形如 `as-<8位hex>` 且同一请求内不重复；补全后 `PATCH /ideas/{id}/assumptions/{hid}` 回写 200，前端「回写…」可用 | P5 | 整表 PATCH 后每条 assumptions 都有 hid；用返回的 hid 回写成功；既有 hid 不被改写 |
| FR-37 | **回归与文档链**：新增 draft-fields 客户端**不真调外网**（monkeypatch 假 LLM）；全量 pytest ≥924 passed + 1 skipped 只增不减 + `npm run build` 绿；requirement/spec/api/plan 齐备并 approved；push 过 pre-push 文档门与 FR 门 | P5 | 定点/全量绿；push 成功；文档 approved |

## 非功能需求

| 项 | 约束 |
|---|---|
| 安全 | apiKey 只进程内使用；不写日志、不进响应；错误文案不回显 key；超时与连接失败统一为可读提示 |
| 可降级 | LLM 不可用绝不阻塞想法面板其余功能（fail-open 到「手动填写」） |
| 幂等/副作用 | 生成为纯读端点（无 DB 写）；hid 补全发生在既有 PATCH 事务内，随 diff 一起进 IdeaChange |
| 可复核 | 测试可离线复跑；活体验证记录含实际响应片段（不含 key） |

## 追溯

| FR | 落点 |
|---|---|
| FR-34 | mio_taskhub/api/draft.py（新增）+ mio_runtime.llm_config()；tests/test_idea_draft_fields.py |
| FR-35 | web/src/components/IdeasView.jsx（按钮+合并策略）、web/src/api.js（draftIdeaFields） |
| FR-36 | mio_taskhub/api/ideas.py（整表 PATCH 归一化） |
| FR-37 | 全量 pytest + npm build + 文档链 |

## 验收标准

- 生成端点 8 字段齐全且零写库（version/IdeaChange 不变），三种降级码可复现；
- 前端一键生成后表单被填、已有内容不被静默覆盖、失败提示明确；
- 表单录入假设自动有 hid，回写链路对新数据可用；
- 全量 pytest ≥924 passed + 1 skipped 不倒退，构建绿；
- 文档链 spec/api/plan approved，push 过双门。
