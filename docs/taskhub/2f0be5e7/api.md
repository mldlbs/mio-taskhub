# TH-01 历史决策闭环 接口契约（API）

## 文档信息

| 项 | 值 |
|---|---|
| 契约版本 | v1.0 |
| 最后更新 | 2026-10-27 11:22 |
| 状态 | approved |
| 负责人 | opencode |
| 服务 | mio-taskhub（FastAPI，本地单用户） |
| 适用任务 | 2f0be5e7（TH-01） |
| 上游依据 | 需求规格 FR-1 ~ FR-11、设计规格 spec |

## 文档信息与范围

本契约只覆盖 TH-01 涉及的 6 个接口：claim 附加 `route`、提交结果（对外不变、新增异步投递）、pending 查询、经验确认、决策反查、outbox 查询。

**明确不含**：runtime 自身的 `task.route` / `task.record_outcome` / `experience.*` CLI 契约（那是上游，见 `mio-agent-runtime@0.18.0`）；creativity / insight / world model / observer 研究管线 / evolution 的任何端点；任务 CRUD、文档链、门控相关端点。

---

## 环境与基础地址

| 环境 | Base URL | 说明 |
|---|---|---|
| 本地默认 | `http://127.0.0.1:48620/api/v1` | 绿色版 / 源码启动的默认监听 |
| 自定义端口 | `http://127.0.0.1:<PORT>/api/v1` | 以启动参数为准，路径前缀不剥离 |

## 认证与鉴权

| 项 | 说明 |
|---|---|
| 默认模式 | 未设置 `MIO_TASKHUB_TOKEN` 时**无鉴权**（本机单用户） |
| Token 模式 | 设置后所有请求需带 `Authorization: Bearer <token>` |
| 失败语义 | 401 `{"detail":"unauthorized"}` 表示未携带或 token 不匹配；本服务无 403 越权模型（单用户） |

## 通用响应结构

| 情形 | 外壳 | 说明 |
|---|---|---|
| 成功 | 直接返回业务对象（无 `code/data` 包裹） | 字段见各接口「响应字段」 |
| 失败 | `{"detail": <string 或 object>}` | FastAPI 默认；业务错误在 object 形式中带 `code` 字段 |
| 无内容 | HTTP 204，空 body | 仅 `claim` 无候选任务时 |

## 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 400 | `invalid_target` | 参数非法 | 枚举值、limit 越界、body 结构错误 | 修正参数 |
| 401 | `unauthorized` | 未认证 | token 模式未带 Bearer | 补 `Authorization` 头 |
| 404 | `route_decision.not_found` | 决策记录不存在 | 该 run 未产生决策（历史 run） | 视为无决策，不重试 |
| 404 | `experience.not_found` | pending 记录不存在 | id 拼错或已被确认 | 刷新列表 |
| 409 | `experience.not_confirmable` | 该经验不可确认 | `outcomeImproved=false`（失败来源） | 不接受；这是设计约束，不是错误重试 |
| 422 | `experience.not_auto_claim` | 非自动采集记录 | `source != auto_claim` | 不应出现在 pending 列表 |
| 500 | `internal` | 服务内部错误 | 未捕获异常 | 查日志，可重试 |
| 503 | `runtime.unavailable` | runtime 不可用 | `MIO_HOME` 缺失 / CLI 缺失 / 超时 | 稍后重试，或检查 runtime 安装 |

## 字段命名与类型规范

| 约定 | 规则 | 反例 |
|---|---|---|
| 命名风格 | 下划线小驼峰混用已存在；**新增字段一律 snake_case** | `routeCount` |
| 时间 | ISO 8601 UTC 字符串（`2026-10-27T03:12:37.961333+00:00`） | 本地时间、时间戳数字 |
| ID | 字符串；run 为 8 位十六进制，decision 为 `rd-<run_id>` | 数字自增 |
| 布尔 | 真布尔，不用 `"true"` 字符串 | `"yes"` |
| 数组/空值 | 无数据用 `[]`；**不可用/未产生用 `null`**，不混用 | `{}` 表示空列表 |
| 字段缺失 vs null | 新增字段始终存在；`null` 表示「已判定为空」，缺失表示「本版本无此字段」 | 用缺失表达空 |

## 幂等性与重试

| 接口 | 幂等 | 幂等键 | 重复提交返回 | 可安全重试 |
|---|---|---|---|---|
| `POST /tasks/claim` | 是 | `agent` 已有 claimed/running run | 同一 run + 复用原决策 | 是 |
| `POST /runs/{run_id}/result` | 是（写回幂等，提交本身受 run 状态保护） | `run:<run_id>:outcome` | 不重复入队，返回同结构 | 否（run 已终结会 409） |
| `GET /mio/experience/pending` | 是（只读） | — | 同结果 | 是 |
| `POST /mio/experience/{id}/confirm` | 是 | 记录 `id` | `state=already`，200 | 是 |
| `GET /mio/route-decisions/{run_id}` | 是（只读） | — | 同结果 | 是 |
| `GET /mio/outbox` | 是（只读） | — | 同结果 | 是 |

可安全重试的状态码：`400`（修正后）、`503`。不可重试：`401`、`404`、`409`、`422`。

---

## 接口清单

| # | Method | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|---|---|---|---|---|---|---|---|
| 1 | POST | `/tasks/claim` | 领取任务并附加历史路由建议 | 无（本地） | 是 | 无 | run=claimed |
| 2 | POST | `/runs/{run_id}/result` | 提交执行结果（异步写回 runtime） | 无 | 是 | 无 | run=finished |
| 3 | GET | `/mio/experience/pending` | 查询待确认经验 | 无 | 是 | 无 | — |
| 4 | POST | `/mio/experience/{id}/confirm` | 人工确认一条经验 | 无 | 是 | 无 | 经验 pending→confirmed |
| 5 | GET | `/mio/route-decisions/{run_id}` | 按 run 反查决策记录 | 无 | 是 | 无 | — |
| 6 | GET | `/mio/outbox` | 查询写回投递状态 | 无 | 是 | 无 | outbox 四态 |

---

## 接口明细

### POST /tasks/claim

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 示例 |
|---|---|---|---|---|---|
| `agent` | query | string | 是 | 领取者标识 | `opencode` |
| `agent_type` | query | string | 否 | 缺省时回查 Agent 表 | `code` |
| `task_id` | query | string | 否 | 指定领取，需为 queued | `2f0be5e7` |
| `project` | query | string | 否 | 回填 task.project | `mio-taskhub` |
| `workspace` | query | string | 否 | 回填 task.workspace | `E:\work\code\agent-dev\mio-taskhub` |
| `files` | query | string | 否 | 逗号分隔 | `a.py,b.py` |
| `project_scope` | query | string | 否 | 领取范围过滤，多值逗号分隔 | `mio-taskhub,agent-dev` |

#### 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | run id |
| `task_id` | string | 任务 id |
| `state` | string | `claimed` |
| `agent_name` | string | 领取者 |
| `attempt` | int | 第几次尝试 |
| `branch` | string | `task-<task_id>` |
| `required_reads` | string[] | 必读文档 kind |
| `documents` | object | kind → {path,status,version,content,truncated} |
| `lessons` | object | 同项目参考（既有字段） |
| `readiness` | object | 就绪度（既有字段） |
| `route` | object \| null | **新增**：历史路由建议，见下 |
| `route.decision_id` | string | `rd-<run_id>` |
| `route.count` | int | 命中的已验证经验条数 |
| `route.top_experience_id` | string \| null | 首条经验 id |
| `route.agent_health_best` | string \| null | `agent:rate:total` 快照，仅展示 |
| `route.degraded_reason` | string | `none`/`runtime_unavailable`/`timeout`/`empty_project`/`schema_error` |
| `route.suggestion` | string \| null | runtime 摘要，≤300 字 |

#### 错误码

| HTTP | 业务码 | 含义 |
|---|---|---|
| 200 | — | 领取成功；无候选时返回 204 |
| 204 | — | 无可领取任务 |
| 404 | `task.not_found` | 指定 task_id 不存在 |
| 409 | `gate.doc_missing` | 指定任务 readiness 未通过 |
| 409 | `decompose.children_incomplete` | 主任务不可被 worker 领取 |
| 401 | `unauthorized` | token 模式未认证 |

#### 示例

```
POST /api/v1/tasks/claim?agent=opencode&project=mio-taskhub

200
{
  "id": "9c1f2e3a",
  "task_id": "2f0be5e7",
  "state": "claimed",
  "agent_name": "opencode",
  "attempt": 1,
  "branch": "task-2f0be5e7",
  "route": {
    "decision_id": "rd-9c1f2e3a",
    "count": 2,
    "top_experience_id": "exp_17f0",
    "agent_health_best": "opencode:93:14",
    "degraded_reason": "none",
    "suggestion": "Route 2 verified experience(s) to this task."
  }
}
```

### POST /runs/{run_id}/result

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 示例 |
|---|---|---|---|---|---|
| `run_id` | path | string | 是 | 必须可提交状态 | `9c1f2e3a` |
| `agent` | query | string | 否 | 传入则校验 run 所有权 | `opencode` |
| `success` | body | bool | 否 | 缺省 true | `false` |
| `result` | body | string | 否 | 结果摘要，写回时截断 200 字 | `pytest 100%` |
| `exit_code` | body | int | 否 | 缺省 0/1 由 success 推导 | `1` |

其余提交字段（证据、命令、diff 等）沿用既有契约，本任务不改。

#### 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | run id |
| `state` | string | `finished` |
| `result` | string | 回显结果摘要 |
| `task_state` | string \| null | 任务后续状态 |
| `retry_at` | string | 仅 retrying 时返回 |
| `backoff_seconds` | int | 仅 retrying 时返回 |

**不返回**写回状态：写回是异步投递，查询走 `GET /mio/outbox?run_id=...`。

#### 错误码

| HTTP | 业务码 | 含义 |
|---|---|---|
| 200 | — | 提交成功（含入队成功或入队失败但已记日志） |
| 403 | `run.not_owner` | agent 不匹配 |
| 404 | `run.not_found` | run 不存在 |
| 409 | `run.not_submittable` | run 已终结 |
| 422 | `gate.read_evidence` | 成功提交但未满足读证要求 |

#### 示例

```
POST /api/v1/runs/9c1f2e3a/result?agent=opencode
{"success": false, "result": "timeout after 300s", "exit_code": 1}

200
{"id": "9c1f2e3a", "state": "finished", "result": "timeout after 300s", "task_state": "retrying"}
```

### GET /mio/experience/pending

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 示例 |
|---|---|---|---|---|---|
| `project` | query | string | 否 | 缺省不过滤 | `mio-taskhub` |
| `limit` | query | int | 否 | 1–100，默认 20 | `50` |

#### 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `available` | bool | runtime 是否可用 |
| `reason` | string \| null | 不可用原因 |
| `count` | int | 本次返回条数 |
| `total` | int | 满足条件的总数 |
| `records[].id` | string | **确认时使用的 id**（不是 experienceId） |
| `records[].experienceId` | string | 关联的经验 id |
| `records[].targetAgent` | string | 实际执行者 |
| `records[].sourceAgent` | string | 经验来源 |
| `records[].project` | string | 项目 |
| `records[].outcomeImproved` | bool | 是否改善结果 |
| `records[].confirmable` | bool | = `outcomeImproved`，false 时禁止确认 |
| `records[].timestamp` | string | ISO 8601 |

#### 错误码

| HTTP | 业务码 | 含义 |
|---|---|---|
| 200 | — | 成功；runtime 不可用时 `available=false` 且 `records=[]` |
| 400 | `invalid_target` | limit 越界 |
| 503 | `runtime.unavailable` | 需要强失败语义时（默认不返回，走 200 降级） |

#### 示例

```
GET /api/v1/mio/experience/pending?project=mio-taskhub&limit=20

200
{
  "available": true, "reason": null, "count": 1, "total": 1,
  "records": [{
    "id": "xfer_1793069477624_a1b2", "experienceId": "exp_17f0",
    "sourceAgent": "cli", "targetAgent": "opencode", "project": "mio-taskhub",
    "outcomeImproved": true, "confirmable": true,
    "timestamp": "2026-10-27T03:20:00.000Z"
  }]
}
```

### POST /mio/experience/{id}/confirm

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 示例 |
|---|---|---|---|---|---|
| `id` | path | string | 是 | pending 记录的 `id` | `xfer_1793069477624_a1b2` |
| `by` | body | string | 否 | 缺省 `taskhub` | `taskhub:human` |
| `note` | body | string | 否 | ≤300 字 | `确认该经验可复用` |

#### 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 记录 id |
| `state` | string | `confirmed` / `already` |
| `outcomeImproved` | bool | 确认后的值 |
| `confirmedBy` | string | 确认者 |
| `confirmedAt` | string | ISO 8601 |

#### 错误码

| HTTP | 业务码 | 含义 |
|---|---|---|
| 200 | — | 已确认或此前已确认（`state=already`） |
| 404 | `experience.not_found` | id 不存在 |
| 409 | `experience.not_confirmable` | `outcomeImproved=false`，不调用 runtime |
| 422 | `experience.not_auto_claim` | 非 auto_claim 记录 |
| 503 | `runtime.unavailable` | CLI 不可用或超时 |

#### 示例

```
POST /api/v1/mio/experience/xfer_1793069477624_a1b2/confirm
{"by": "taskhub:human", "note": "确认该经验可复用"}

200 {"id": "xfer_1793069477624_a1b2", "state": "confirmed",
     "outcomeImproved": true, "confirmedBy": "taskhub:human",
     "confirmedAt": "2026-10-27T03:25:11.000Z"}
```

### GET /mio/route-decisions/{run_id}

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 示例 |
|---|---|---|---|---|---|
| `run_id` | path | string | 是 | 8 位十六进制 | `9c1f2e3a` |

#### 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `decision_id` | string | `rd-<run_id>` |
| `task_id` | string | 任务 id |
| `agent` | string | 领取者 |
| `project` | string | 项目 |
| `query` | string | 传给 route 的检索文本 |
| `route_count` | int | 命中条数 |
| `top_experience_id` | string \| null | 首条经验 |
| `agent_health_best` | string \| null | 健康快照 |
| `degraded_reason` | string | 降级原因 |
| `at` | string | ISO 8601 |

#### 错误码

| HTTP | 业务码 | 含义 |
|---|---|---|
| 200 | — | 成功 |
| 404 | `route_decision.not_found` | 该 run 无决策记录 |

#### 示例

```
GET /api/v1/mio/route-decisions/9c1f2e3a

200 {"decision_id": "rd-9c1f2e3a", "task_id": "2f0be5e7", "agent": "opencode",
     "project": "mio-taskhub", "query": "TH-01 接入 task.route",
     "route_count": 2, "top_experience_id": "exp_17f0",
     "agent_health_best": "opencode:93:14", "degraded_reason": "none",
     "at": "2026-10-27T03:20:00.000000+00:00"}
```

### GET /mio/outbox

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 示例 |
|---|---|---|---|---|---|
| `status` | query | string | 否 | `pending`/`sent`/`failed`/`dead` | `failed` |
| `run_id` | query | string | 否 | 精确匹配 | `9c1f2e3a` |
| `limit` | query | int | 否 | 1–200，默认 50 | `100` |
| `include_payload` | query | bool | 否 | 默认 false | `false` |

#### 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `count` | int | 返回条数 |
| `items[].key` | string | `run:<run_id>:outcome` |
| `items[].run_id` | string | |
| `items[].task_id` | string | |
| `items[].status` | string | 四态 |
| `items[].attempts` | int | 已尝试次数 |
| `items[].next_at` | string | 下次投递时间 |
| `items[].last_error` | string \| null | 最近失败原因 |
| `items[].payload` | object | 仅 `include_payload=true` 时返回 |

#### 错误码

| HTTP | 业务码 | 含义 |
|---|---|---|
| 200 | — | 成功 |
| 400 | `invalid_target` | status/limit 非法 |

#### 示例

```
GET /api/v1/mio/outbox?run_id=9c1f2e3a

200
{"count": 1, "items": [{"key": "run:9c1f2e3a:outcome", "run_id": "9c1f2e3a",
  "task_id": "2f0be5e7", "status": "sent", "attempts": 1,
  "next_at": "2026-10-27T03:21:00+00:00", "last_error": null}]}
```

---

## 版本策略

| 项 | 说明 |
|---|---|
| 版本位置 | 无 URL 版本段；契约版本记录在本文件「文档信息」 |
| 兼容原则 | 只增字段、不改既有字段语义 |
| 破坏性变更 | 需新增 `/v2` 前缀或保留旧字段一个发布周期 |

## 通用请求头

| Header | 必填 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `Authorization` | 条件 | string | 无 | token 模式下 `Bearer <token>` |
| `Content-Type` | POST/PUT 必填 | string | — | `application/json; charset=utf-8` |
| `X-Request-ID` | 否 | string | 服务生成 | 透传回响应，用于日志串联 |

## 分页

| 项 | 说明 |
|---|---|
| 方式 | 偏移式 `limit`，无 `offset` |
| 默认/上限 | pending 20/100；outbox 50/200 |
| 超限 | 400 `invalid_target`，不静默截断 |

## 时间 / 数值 / 空值约定

| 类型 | 约定 |
|---|---|
| 时间 | ISO 8601，UTC，微秒可选；展示层再做本地化 |
| 数值 | `route_count`/`attempts` 为非负整数；`limit` 越界报 400 |
| 空值 | 列表空用 `[]`；未产生用 `null`；不返回 `undefined` |

## 枚举全集

| 枚举 | 取值 | 出现位置 |
|---|---|---|
| `degraded_reason` | `none`、`runtime_unavailable`、`timeout`、`empty_project`、`schema_error` | claim、route-decisions |
| `outbox.status` | `pending`、`sent`、`failed`、`dead` | outbox |
| `confirm.state` | `confirmed`、`already` | confirm |
| `failure_type` | `timeout`、`gate_rejected`、`execution_error`、`unknown` | 仅内部：拼进写回 summary，不作接口字段 |

调用方必须能处理未知枚举值（按未知处理，不报错）。

## 限流与配额

| 项 | 说明 |
|---|---|
| 限流 | 无。本服务为本地单用户，不设 QPS 阈值 |
| 429 | 不会返回；不做客户端退避设计 |

## 超时与并发

| 项 | 说明 |
|---|---|
| 服务端超时 | claim 内 route 子进程预算 1.5s；写回由后台 job 异步执行 |
| 客户端建议超时 | 只读 5s；claim 10s；提交 30s |
| 并发冲突 | claim 以条件更新抢占，冲突返回 204；submit 以 run 状态为乐观锁，冲突 409 |
| 长任务 | 写回不阻塞请求，属异步；查询投递进度用 outbox |

## 文件上传与下载

本契约不涉及文件上传与下载：所有请求体均为 JSON，无 multipart 与二进制响应。

## 安全与脱敏

| 项 | 规则 |
|---|---|
| 敏感字段 | runtime `config.json` 的 `llm` 段（含 apiKey）永不读取进响应、永不落日志 |
| outbox payload | 默认不返回；返回时不含凭据 |
| 日志 | 不记录 token；错误信息截断 500 字 |
| 注入 | 所有参数按类型校验，不拼接 SQL |

## 兼容性与废弃策略

| 类别 | 定义 |
|---|---|
| 兼容变更 | 新增可选字段、新增端点、新增枚举值 |
| 破坏性变更 | 改字段类型、改语义、删除字段 |
| 通知 | 破坏性变更需在「变更记录」标注并在下一版本说明 |
| 废弃 | 标记 → 保留一个发布周期 → 移除 |

## 附录

| 项 | 内容 |
|---|---|
| 关联文档 | 需求规格 requirement（FR-1 ~ FR-11）、设计规格 spec、测试验收 test |
| 上游契约 | `mio-agent-runtime@0.18.0`：`mio.task.route`、`mio.task.record_outcome`、`mio experience list/confirm` |
| 数据字典 | `RouteDecision`、`MioOutbox`（见 spec 2.1） |

## 变更记录

| 日期 | 契约版本 | 变更内容 | 兼容性 | 影响方 |
|---|---|---|---|---|
| 2026-10-27 | v1.0 | 首版：claim 增加 `route`；新增 pending/confirm/route-decisions/outbox 四个端点；`/runs/{id}/result` 对外结构不变 | 兼容（只增） | 前端 MioRuntimeView、MCP 工具 |
