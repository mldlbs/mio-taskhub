> 软件项目文档链 · 7 件套  
> 上游：[状态模型](docs/taskhub/grab-mode/data-model.md) | 下游：[测试验收](docs/taskhub/grab-mode/test.md)  
> 约定：正文**分点 + 换行**（每点一行，`-` 列表）；指引注释处填内容，未用章节删除。

# 接口契约（API Contract）

> **本文件要求事无巨细。** 每个接口的每个参数、每个响应字段、每个错误码都必须有类型、必填性、约束、示例；宁可长，不可省。
> 读到本文件的人应当**无需翻源码**就能正确调用并处理全部异常。

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v0.3 |
| 最后更新 | 2026-10-07 11:25 |
| 状态 | draft |
| 负责人 | 高志谦 |
| 适用范围 | mio-taskhub 抢单模式的全部新增与扩展接口：抢单池、原子抢单、拒单、释放、抢单统计、任务抢单字段扩展 |
| 明确不含 | 存量 100+ 条任务／想法／文档类接口（另行维护）、内部数据库直连、WebSocket 事件推送协议 |

> **v0.3 修订说明**：依据 Spec v0.3 与全量评审结论同步——
> ① 新增 `409 grab.agent_busy`（在途 run 冲突时不再返回不相关 run）；
> ② 新增 `503 db_locked` 系统故障码，并区分业务拒绝／系统故障（§7.1）；
> ③ 明确 `grab.cooldown` 为**软惩罚**（只影响排序，不禁止抢单，§8.2）；
> ④ 新增 §6.3 跨对象不变量表、§6.4 事件可靠性边界；
> ⑤ 统计口径细化（§18.5）。

## 1. 文档信息与范围

| 项 | 值 |
|----|----|
| 契约版本 | v1 |
| 适用范围 | 调用方 = 各 agent 运行时（claim/grab 循环）、Web UI（大厅与统计）、运维脚本；覆盖 6 个接口 |
| 明确不含 | 不含存量 tasks/ideas/documents 等接口的完整契约；不含 WS 推送协议（`/api/v1/ws`）；不含 DB 直连与内部函数调用 |
| 契约基座 | 路径前缀 `/api/v1`，服务默认监听 `127.0.0.1:48620`，由 `mio-taskhub hub` 启动 |

- 本契约描述的是**抢单模式新增面**，与存量接口的兼容性约束见 §19。
- 存量接口若与本契约冲突（如错误体形状），以本契约规定的新增接口为准，存量接口保持原样不改。

## 2. 环境与基础地址

| 环境 | Base URL | 说明 |
|------|----------|------|
| dev | `http://127.0.0.1:48620/api/v1` | 本地单机，`mio-taskhub hub` 直接启动，默认端口 48620 |
| staging | — | 无独立预发环境；需要隔离时用 `MIO_TASKHUB_PORT` 起第二实例（如 48621），数据目录经 `MIO_TASKHUB_DB` 分离 |
| prod | — | 当前以 PyInstaller 绿色版在本机运行，无独立域名与反向代理 |

- 路径前缀：`/api/v1`（固定，版本号在路径第一段）。
- 协议与端口：HTTP，默认 `48620`，可用环境变量 `MIO_TASKHUB_PORT` 覆盖。
- 网关 / 反向代理差异：无。调用方直连 uvicorn，路径前缀不会被剥离或重写。
- 字符编码：UTF-8。
- 内容类型：`application/json; charset=utf-8`。
- 服务端对 JSON 响应会自动补 `charset=utf-8`（中文平台默认编码为 GBK，缺 charset 会导致乱码）。

## 3. 版本策略

- 版本位置：URL 路径（`/api/v1/...`）。不使用请求头或查询参数传版本。
- 当前版本：`v1`。
- 版本号规则：仅主版本号，出现在路径第一段；破坏性变更才升主版本。
- 多版本并存策略：新增 `v2` 时新建独立 router 挂 `/api/v2`，`v1` 继续服务至下线；两端共用同一套 DB 与业务层，不复制实现。
- 旧版本弃用流程：`v1` 标记 deprecated（响应头加 `X-API-Deprecated: true`）→ 保持 30 天 → 下线。
- 版本不体现在响应体内：本服务**不返回** `api_version` 字段，调用方从 URL 自明。

## 4. 认证与鉴权

| 项 | 约定 |
|----|------|
| 认证方式 | 默认**无鉴权**（本地单机回环）；启动加 `--auth` 后为静态 Bearer Token |
| 凭证来源 | `--auth` 启动时由服务端生成并打印到启动日志；无自助签发接口 |
| 传递位置 | `Authorization: Bearer <token>` |
| 有效期 | 长期有效，直到服务端轮换重启 |
| 刷新方式 | 不支持刷新；轮换 = 重启服务并替换客户端配置 |
| 权限模型 | 无 RBAC 与权限点。身份仅两类：`agent`（写操作主体）与 `user`（管理主体）；资源级约束见下 |
| 未认证响应 | HTTP `401` + `detail = "auth.unauthenticated"` |
| 越权响应 | HTTP `403` + `detail = "auth.forbidden"` |

- **资源级约束（越权判定的唯一依据）**：
  - `POST /runs/{run_id}/release` 只允许 `run.agent_name == agent` 调用。
  - `POST /tasks/{task_id}/decline` 退单分支（任务已被本人 `claimed`）只允许占用者调用。
  - 其余抢单读接口不做资源级限制。
- 401 与 403 严格区分：`401` = 令牌缺失或无效（不知道你是谁）；`403` = 身份有效但无权操作该资源（知道你是谁但不许）。
- 每个接口需要的身份见 §17 接口清单「权限」列，两处必须一致。

## 5. 通用请求头

| Header | 必填 | 类型 | 默认 | 说明 |
|--------|------|------|------|------|
| `Authorization` | 仅 `--auth` 开启时必填 | string | — | `Bearer <token>`；未开启鉴权时传了也不校验 |
| `Content-Type` | 写请求（POST/PUT/PATCH）必填 | string | `application/json` | 必须是 `application/json`，写请求按 JSON 解析 body |
| `X-Request-ID` | 否 | string | 服务端生成 UUID4 | 链路追踪。**原样透传回响应头**，日志按它关联 |
| `Accept` | 否 | string | `*/*` | 仅支持 JSON，传别的也返回 JSON |

- 所有响应（含错误响应）都会带 `X-Request-ID`（由 `RequestIDMiddleware` 注入，实测 404 响应亦带）。
- 服务端不解析 `X-Request-ID` 内容，长度与格式不限，但建议 ≤ 64 字符。

## 6. 通用响应结构

**本服务不使用统一信封**（`code`/`message`/`data` 三段式）。契约明确如下：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| （成功响应） | object \| array | — | 直接返回业务对象本体或数组，无外包层；字段定义见 §18 各接口「响应字段」 |
| `detail` | string | 失败时必有 | 错误信息，即业务码（见 §7）；调用方按其**精确字符串**分支 |
| `error` | string | 仅限流失败时 | 值为 `rate_limited`；与 `detail` **互斥**，见下 |
| `retry_after_seconds` | integer | 仅限流失败时 | 建议重试等待秒数，与 `Retry-After` 响应头一致 |

- **失败响应有两种形状，调用方必须都处理**：
  1. 业务错误（`HTTPException` 路径）→ `{"detail": "<业务码>"}`。
  2. 中间件限流（`RateLimitMiddleware`）→ `{"error": "rate_limited", "retry_after_seconds": 18}`。此形状**不含 `detail`**，是历史既有行为，本契约保留不改。
- **响应体例外（必须显式列出）**：
  - `POST /api/v1/tasks/claim` 的 `204` 无响应体、`409` 为裸 `text/plain` 且**不带 `Content-Type`**。本契约的新增接口**不复制这一行为**，一律返回 JSON。
  - 无「裸数组」顶层响应；列表一律包在对象的 `items` 字段内（见 §18）。
- 空对象 `{}` 与 `null` 的差异：返回合法对象但无数据时用 `items: []`（数组为空），**不用** `items: null`；无此字段时用 `null`（见 §9）。

### 6.3 跨对象不变量（v0.3 新增）

调用方（尤其自建抢单循环的 agent）可依赖以下不变量，它们由服务端保证：

| Task.state | Task.stage | 有效执行 Run | 对调用方的含义 |
|---|---|---|---|
| `queued` | `ready` | 无 | 出现在 `GET /tasks/hall`，可抢 |
| `queued` | 非 `ready` | 无 | **不在**大厅，不可抢 |
| `claimed` | `implementing` | 有，唯一持有者 | 已被抢走，`grab` 返 409 |
| 任意终态 | 任意 | **无** | 不会出现在大厅，也不会被后台重新激活 |

- **一个 Agent 同时至多一个有效执行 Run**：若你已持有在途 run，再抢其他任务必得`409 grab.agent_busy`（v0.3 起**不会**返回不相关的 run）。
- **一个任务同时至多一个有效执行 Run**：并发抢同一任务，恰一方成功。
- 历史 run 终结后任务回池重抢，**旧 run 记录保留不被覆盖**。

### 6.4 事件可靠性边界（v0.3 新增）

- 事件与业务状态在**同一事务**提交（不存在「状态改了但事件没写」）。
- **但广播不保证送达**：WS 广播在提交后进行，失败仅记日志（`events.py` 逐事件 try/except），**无后台补发**。
- 因此：**不得把 WS 事件当作唯一事实来源**。客户端必须以 `GET /tasks/{id}` 或 `GET /tasks/hall` 轮询兜底，事件只用于降低轮询频率。
- 消费者必须能容忍**重复**与**丢失**事件。

## 7. 统一错误码

本服务**不返回独立数字业务码**，HTTP 状态码 + `detail` 字符串共同构成错误标识。`detail` 统一采用 `域.动作` token 形式（存量散文式 `detail` 保持不变，调用方对未知值必须兜底）。

| HTTP | 业务码（detail） | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 400 | `request.invalid_param` | 参数非法或缺失 | 必填参数缺失（如 `agent` 为空）、`limit`/`window_hours` 越界或非整数 | 修正参数后重试；不要重试同样请求 |
| 401 | `auth.unauthenticated` | 未认证 | `--auth` 开启且未带 `Authorization`，或令牌无效 | 补 Bearer 令牌；无令牌时联系服务端重启获取 |
| 403 | `auth.forbidden` | 越权 | 操作对象不属于本 `agent`（释放他人的 run、退他人的单） | 换成正确的 `agent` 标识；不要重试 |
| 403 | `grab.agent_offline` | Agent 离线 | 抢单时 `agent.status == offline` 或 agent 未注册 | 先 `POST /agents/heartbeat` 上线再抢 |
| 404 | `grab.task_not_found` | 任务不存在 | `task_id` / `run_id` 查无记录 | 核对 ID（8 位 hex，区分大小写） |
| 409 | `grab.task_taken` | 任务已被抢走 | 条件更新 `WHERE state='queued'` 未命中（他人先抢到） | 照返回的 `winner` 改抢别的任务；**不可重试同一请求** |
| 409 | `grab.agent_busy` | 该 Agent 已有其他在途 Run | 抢单时该 agent 已有 `claimed`/`running` 的 run，且**属于其他任务** | 先完成在手任务；**不会**返回不相关的 run，v0.3 起不再如此 |
| 409 | `grab.not_releasable` | Run 已结束不可释放 | 释放的 run 已处于 `finished` | 刷新任务状态；不要重试 |
| 422 | `grab.not_grab_mode` | 任务不允许抢单 | 目标任务 `grab_mode == dispatch` | 改用 `POST /tasks/claim` 领取，或让发布者改 `grab_mode` |
| 422 | `grab.task_not_ready` | 任务不在可抢状态 | 任务 `state != queued` 或 `stage != ready` | 等任务推进到 `ready`；不要重试 |
| 429 | `grab.cooldown` | Agent 处于抢单冷却（**软惩罚**） | 上次失约后的 `cooldown_seconds` 窗口内 | **软惩罚只影响大厅排序，不禁止抢单**；按 `Retry-After` 等待后重试 |
| 429 | `grab.decline_limit` | 当日拒单额度用尽 | 该 agent 当日 `decline_count_today >= decline_daily_limit` | 等次日 UTC 00:00 额度重置；退单分支不受此限 |
| 429 | `rate_limited`（走 `error` 字段） | 全局限流 | 单 IP 60s 内 `/api/` 请求数超 `MIO_TASKHUB_RATE_LIMIT`（默认 120） | 按 `Retry-After` 退避；批量查询请合并或降频 |
| 500 | `internal.error` | 服务端内部错误 | 未捕获异常、DB 异常 | 携带响应头 `X-Request-ID` 查服务端日志；可安全重试一次 |
| 503 | `db_locked` | SQLite 写锁冲突重试耗尽 | `busy_timeout`（5s）内未获得写锁，有限重试 3 次仍失败 | 退避重试；持续出现须查后台线程是否长事务持锁 |

### 7.1 业务拒绝 vs 系统故障（v0.3 新增，统计口径依赖）

`GrabAttempt.decision` 字段区分两类，**统计时系统故障不计入竞争分母**，否则基础设施抖动会被误读为「大家都在抢」：

| 分类 | `decision` | 涵盖 | 计入 `success_rate` 分母 |
|---|---|---|---|
| 正常竞争 | `won` | 抢单成功（HTTP 200） | 是（分子） |
| 业务拒绝 | `reject` | 400 / 403 / 404 / 409 / 422 / 429 全部业务码 | 是 |
| 系统故障 | `error` | 500 `internal.error`、503 `db_locked`、DB 异常 | **否** |

- 「谁抢不到」是业务信号（`reject`），「服务不可用」是系统信号（`error`）——两者在统计与告警上必须分开。
- HTTP 200 + `won` 与 409 + `taken` 都是**正常竞争**，不视为错误。

- 本表为**汇总**，每个接口实际可能触发的子集见 §18 各接口「错误码」子块，两处必须一致。
- 禁止对 `409 grab.task_taken` 做自动重试——重试只会再次失败并污染抢单统计。

## 8. 分页 / 排序 / 过滤

| 项 | 参数 | 类型 | 默认 | 上限 | 说明 |
|----|------|------|------|------|------|
| 页码 | — | — | — | — | **不支持**偏移分页；抢单池是「当前状态快照」，翻旧页无意义 |
| 每页条数 | `limit` | integer | 50 | 200 | 仅 `GET /tasks/hall` 支持；超上限按 200 截断（截断而非报错） |
| 排序字段 | — | — | 固定 | — | **不接受客户端排序参数**；服务端固定五级排序（见下） |
| 排序方向 | — | — | 固定 | — | 固定：`cooldown_active asc → match_score desc → bounty desc → priority desc → pool_entered_at asc` |
| 过滤条件 | `agent_type` / `project` / `labels` | string / string / string（逗号分隔） | `''` | — | 多值逗号分隔，语义为 OR；空串表示不过滤 |

- 排序五级为**稳定排序**，末级 `pool_entered_at asc` 保证 FIFO，因此同分任务不会抖动。
- `cooldown_active` 是**独立的第一排序维度**（v0.3 修正）：v0.2 曾用 `match_score = -1` 表示冷却，但失约惩罚可低至 −20，导致冷却任务不一定排最后。现在冷却只影响排位，**不禁止抢单**。
- `match_score` 依赖调用方身份（`agent` 决定匹配维度），**同一任务对不同 agent 的顺序可以不同**——这是设计意图，不是不一致。
- 分页响应字段：`total`（过滤后总数）、`pool_depth`（池内总条数，不受 limit 影响）、`limit`（本次生效上限）；**无 `has_next`**，调用方用 `len(items) < total` 判断是否被截断。
- 游标 vs 偏移：两者都不用。理由 = 池是快照，两次查询之间状态会变（新任务入池、被抢走），偏移分页会漏或重。
- 超限行为：`limit > 200` 截断为 200 并在响应 `limit` 字段回显生效值；`limit <= 0` 报 `400 request.invalid_param`。
- 多字段排序优先级规则：严格按上表四级自上而下比较，任一级不等即定序。

## 9. 字段命名与类型规范

| 项 | 规范 |
|----|------|
| 命名风格 | `snake_case`，全小写。**反例**：`grabMode`、`Grab_Mode`、`grabmode` 均为违规 |
| ID 类型 | 一律 `string`，`task_id` / `run_id` 为 8 位小写 hex（如 `3fa41beb`）；调用方不得按整数解析，也不得假定长度永远为 8 |
| 布尔 | JSON `true` / `false`，不用 `0`/`1`/`"true"` |
| 金额 | 本契约**无金额字段**。`bounty` 是**排序权重整数**，不是货币，无单位无小数，不得展示为价格 |
| 数组 | 空集合一律 `[]`，**永不返回 `null`**。`items`、`labels`、`required_reads` 均适用 |
| 可选字段 | **字段缺失** = 本方当前版本不存在该字段（调用方须容错，不得报错）；**显式 `null`** = 字段存在但当前无值（如 `target_agent_type: null`、`claimed_at: null`）。两者语义不同，调用方不得合并处理 |
| 数值类型 | 计数类一律 `integer`，不返回 `"3"` 这类字符串数字 |
| 时间类型 | 一律 `string`，ISO 8601 带时区偏移（见 §10） |

- 新增字段一律可选且**向后兼容**：调用方必须忽略不认识的字段，不得因多余字段报错。

## 10. 时间 / 数值 / 空值约定

- 时间格式：ISO 8601，形如 `2026-10-07T02:30:00+00:00`。日期时间之间用 `T` 分隔。
- 时区：响应一律带偏移量 `+00:00`（UTC）。DB 内部存 naive UTC，读路径统一补 `tzinfo` 后序列化，因此**响应永不带裸时间**。
- 时间精度：秒。不返回毫秒（`latency_ms` 是整数毫秒，不是时间戳）。
- 输入时间的容差：本契约的抢单接口**不接受任何时间入参**（窗口、阶梯、限额全部由服务端时间与配置决定），避免客户端时间不可信。
- 数值范围与溢出处理：`bounty` ∈ `[0, bounty_cap]`（默认上限 100）；`waited_seconds` ≥ 0；`latency_ms` ≥ 0；`window_hours` ∈ `[1, 720]`。越界即 `400`，不静默截断（`limit` 例外，见 §8）。
- 空值语义：
  - `expires_in: null` = 该任务不会过期（`grab` 模式无 deadline）。
  - `dispatchable_at: null` = 不会被派单（`grab` 模式）。
  - `latency_p50_ms: null` = 窗口期内无成功抢单样本（**不得显示为 0**）。
  - `total: 0` 时 `items: []`，不是 `null`。

## 11. 枚举全集

全项目枚举集中登记在此，接口明细只引用不再重复定义。调用方**必须能处理未知取值**（前向兼容）。

| 枚举 | 取值 | 含义 | 出现在哪些接口 |
|------|------|------|---------------|
| `grab_mode` | `grab` | 只挂大厅，**永不派单** | `GET /tasks/hall`、`PATCH /tasks/{id}` |
| | `dispatch` | 只走派单，行为与改造前一致 | 同上 |
| | `hybrid` | 先挂大厅抢单，超 `grab_deadline_at` 才可派单 | 同上 |
| `GrabAttempt.outcome` | `won` | 抢单成功 | `GET /grab/stats` |
| | `lost` | 抢单落空（多因 `taken`） | 同上 |
| | `declined` | 显式拒单 | 同上 |
| | `missed` | 抢中后未开工导致释放 | 同上 |
| `Task.state` | `queued` / `claimed` / `running` / `retrying` / `completed` / `failed` / `cancelled` / `blocked_failed` | 任务执行状态（存量枚举，语义不变） | 全部接口 |
| `Task.stage` | `brainstorming` / `design` / `planning` / `ready` / `implementing` / `review` / `done` / `cancelled` | 研发阶段（存量枚举）。仅 `queued + ready` 可被抢 | 全部接口 |
| `AgentStatus` | `online` / `offline` / `busy` / `idle` | Agent 在线状态 | `POST /tasks/grab` |
| `GrabAttempt.reason` | `taken` / `not_found` / `not_grab_mode` / `task_not_ready` / `cooldown` / `decline_limit` / `offline` / `agent_busy` / `internal_error` / `db_locked` / `''` | 失败原因；`won` 时为空串 | `GET /grab/stats` |
| `GrabAttempt.decision` | `won` / `reject` / `error` | 业务拒绝 vs 系统故障（见 §7.1）；`error` 不计入竞争分母 | `GET /grab/stats` |
| `detail` 业务码 | 见 §7 全集 | 错误标识 | 全部接口错误响应 |

- 新增枚举值 = 兼容变更；删除取值或改变含义 = 破坏性变更（见 §19）。
- 调用方遇到未知 `grab_mode` 时，应按 `dispatch` 处理（最保守，不影响存量语义）。
- **冷却是软惩罚**：`agent` 处于冷却期时，`GET /tasks/hall` 仍会返回任务（只是排位靠后），`POST /tasks/grab` **仍然允许抢成功**。若你在其他入口再做硬拦截，会与本契约矛盾。

## 12. 幂等性与重试

| 接口 / 场景 | 幂等 | 幂等键 | 重试建议 |
|------------|------|--------|---------|
| `POST /tasks/grab` | 是 | `(task_id, agent)` | 可重试。若该 agent 已有在途 run，直接返回该 run（`200`），**不重复消耗**、不重复落痕 |
| `POST /tasks/grab` 落空后 | 否（返回 409） | — | **禁止重试**。重试必然再失败，且会污染竞争人数与统计 |
| `POST /tasks/{id}/decline`（queued 分支） | 是 | `(task_id, agent, UTC 日期)` | 可重试；同一天重复拒同一任务返回 `200` 且**不重复扣额度** |
| `POST /runs/{id}/release` | 是 | `run_id` | 可重试；已释放的 run 再调返回 `200`（幂等），已正常结束的返回 `409` |
| `GET /tasks/hall`、`GET /grab/stats` | 是 | — | 可任意重试 |

- 安全重试的 HTTP 状态码：`429`、`500`、`503`。
- 绝不可重试的状态码：`400`、`403`、`404`、`409`、`422`（除上表特批的幂等路径）。
- 重试间隔与退避策略：指数退避，起始 1s，倍数 2，上限 30s，最多 5 次；`429` 必须优先遵守 `Retry-After`。
- 幂等键的生成规则与有效期：无需调用方生成，由服务端按上表字段组合判定；有效期 = 资源存活期（run 结束、任务被抢走即失效）。
- 重复提交的响应：见上表「重试建议」列，均为 `200` 而非错误码（唯一的 409 例外见 `release`）。

## 13. 限流与配额

| 范围 | 阈值 | 窗口 | 超限响应 |
|------|------|------|---------|
| 全局（按客户端 IP） | `MIO_TASKHUB_RATE_LIMIT`，默认 120 次 | 滑动 60 秒 | HTTP `429` + `Retry-After` + `{"error":"rate_limited","retry_after_seconds":N}` |
| 拒单（按 agent） | `MIO_GRAB_DECLINE_DAILY_LIMIT`，默认 20 次 | UTC 自然日 | HTTP `429 grab.decline_limit` + `Retry-After`（到次日 UTC 00:00 的秒数） |
| 抢单冷却（按 agent） | `MIO_GRAB_COOLDOWN_SECONDS`，默认 120 秒 | 自上次失约起算 | HTTP `429 grab.cooldown` + `Retry-After`（剩余秒数） |

- 全局限流作用于全部 `/api/` 路径（`/healthz`、`/readyz`、`/metrics` 豁免）。
- 全局限流计数按 IP，**多 agent 同机共享额度**——批量抢单脚本必须自行串行化并留余量。
- 拒单配额与抢单冷却**不叠加**：冷却期内的抢单请求先命中 `grab.cooldown`。
- 配额以服务端 UTC 时间为准，不接受客户端声明的日期。

## 14. 超时与并发

- 服务端处理超时：无显式业务超时（uvicorn 默认无请求超时）。抢单为单行条件更新，实测本地 P95 < 50ms。
- 建议客户端超时：`10s`（`GET /grab/stats` 在窗口大、样本多时可放宽到 `30s`）。
- 乐观锁 / 版本号字段：**无版本号字段**。并发控制改用**条件更新（CAS）**：`UPDATE task SET state='claimed' WHERE id=:id AND state='queued'`，以 `rowcount` 判定胜出者。
- 并发冲突响应：`409 grab.task_taken`，响应体带 `winner`（抢中者 agent 名）与 `grabbed_at`（抢中时刻），供落空方溯源。
- 长任务的处理方式：抢单**同步返回** `run_id`，实际执行异步进行；调用方拿 `run_id` 后走 `POST /runs/{run_id}/heartbeat` 心跳与 `POST /runs/{run_id}/result` 提交。
- 并发保底语义：同一任务在任意时刻**至多一个** `claimed` run。释放回池后重新可抢，历史 `GrabAttempt` 全部保留，不覆盖。

## 15. 文件上传与下载

| 项 | 约定 |
|----|------|
| 上传方式 | 不适用。抢单接口不接受文件上传 |
| 单文件上限 | 不适用 |
| 允许类型 | 不适用 |
| 下载方式 | 不适用。抢单接口不返回文件流 |
| 鉴权方式 | 不适用 |

- 本组接口不涉及文件传输。任务文档与内嵌图片由既有 `/tasks/{id}/doc`、`/tasks/{id}/raw` 端点提供，**不在本契约范围内**。
- 本节保留是为了让契约读者明确「确实没有」而非「漏写」。

## 16. 安全与脱敏

- 敏感字段清单及脱敏规则：本组接口**无用户隐私字段**（无姓名、手机、邮箱、凭证）。`agent_name`、`project`、`workspace` 属内部标识，按原样返回，不做脱敏。
- 传输要求：默认 HTTP 明文，**仅适用于本机回环（127.0.0.1）**。跨机部署必须在前面加 TLS 终结（反向代理或 https 监听），并对 `Authorization` 头做保护。
- 日志禁止记录的字段：`Authorization` 头内容。其余字段（含 `task_id`、`agent`）可入日志用于排障。
- 越权与注入防护：
  - 越权：`release` / `decline` 退单分支强制校验 `run.agent_name == agent`，不匹配即 `403 auth.forbidden`。
  - 注入：所有 DB 访问经 SQLModel/SQLAlchemy 参数绑定，不拼接 SQL；`agent`、`task_id` 均按参数传递。
  - 状态伪造：所有写接口**不接受** `state` / `stage` 入参，状态只能由服务端状态机推导，防止调用方绕过生命周期门控。
  - 路径逃逸：本组接口不接收路径参数形式的文件路径，不存在 `../` 逃逸面。

## 17. 接口清单

| Method | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|--------|------|------|------|------|------|---------|
| GET | `/api/v1/tasks/hall` | 抢单池列表（含悬赏、等待时长、竞争数、匹配分、过期倒计时） | 读，无资源级限制 | 是 | 计全局限流 | 只返回 `state=queued` 且 `stage=ready` |
| POST | `/api/v1/tasks/grab` | 原子抢单，成功创建 run | 写，需已注册且非 offline 的 agent | 是（键 `task_id,agent`） | 全局 + 冷却 `grab.cooldown` | `queued → claimed` |
| POST | `/api/v1/tasks/{task_id}/decline` | 拒单（未占用）或退单（已占用） | 写；退单分支限占用者 | 是（按 UTC 日期） | 全局 + 每日 `grab.decline_limit` | `queued` 不变；退单时 `claimed → queued` |
| POST | `/api/v1/runs/{run_id}/release` | 主动释放占用，任务回池 | 写；限 `run.agent_name` | 是（键 `run_id`） | 计全局限流 | `claimed → queued`，run `→ finished` |
| GET | `/api/v1/grab/stats` | 抢单统计与排行榜 | 读，无资源级限制 | 是 | 计全局限流 | 只读聚合，不改状态 |
| PATCH | `/api/v1/tasks/{task_id}` | 扩展：写入抢单模式与悬赏参数 | 写（沿用存量任务管理语义） | 是 | 计全局限流 | 不改 `state` / `stage` |

- 清单与 §18 明细**一一对应**，不存在只在清单出现而无明细的接口。

## 18. 接口明细

### GET /api/v1/tasks/hall

- 用途：取抢单池快照，按调用方身份算匹配分并排序
- 权限：读，无资源级限制（`--auth` 开启时需登录）
- 幂等：是
- 副作用：无（不写库、不落痕、不加价）
- 关联状态：只返回 `state=queued` 且 `stage=ready` 且 `grab_mode != dispatch` 的任务

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| `agent` | query | string | 是 | — | 非空，建议 ≤ 64 字符 | 抢单方身份；决定匹配分与冷却判定 | `codex-a` |
| `agent_type` | query | string | 否 | `''` | — | 能力类型；空则回查已注册 agent 的 `agent_type` | `developer` |
| `project` | query | string | 否 | `''` | 逗号分隔 | 领取范围过滤（OR 语义），只返回该范围内的任务 | `mio-taskhub,csps-app-fxyj` |
| `labels` | query | string | 否 | `''` | 逗号分隔 | 标签过滤（OR 语义） | `scheduler,grab-mode` |
| `limit` | query | integer | 否 | 50 | `1..200` | 返回条数上限；超 200 截断为 200，`<=0` 报 400 | `20` |

- 大厅**不返回已被本 agent 抢中的任务**，也不返回本 agent 当日已拒的任务。

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `total` | integer | 是 | 过滤后的任务总数 | `3` |
| `pool_depth` | integer | 是 | 池内总条数（所有 agent 视角），不受 `limit` 影响 | `7` |
| `limit` | integer | 是 | 本次生效的条数上限（回显截断后的值） | `50` |
| `now` | string | 是 | 服务端当前时间，ISO 8601 UTC | `2026-10-07T02:30:00+00:00` |
| `items` | array | 是 | 任务快照数组，按四级规则排序；无数据时为 `[]` | 见下 |
| `items[].id` | string | 是 | 任务 ID（8 位 hex） | `3fa41beb` |
| `items[].title` | string | 是 | 任务标题 | `mio-taskhub 抢单模式改造` |
| `items[].grab_mode` | string | 是 | 调度模式，取值见 §11 | `hybrid` |
| `items[].bounty` | integer | 是 | 当前悬赏权重（非货币） | `10` |
| `items[].base_bounty` | integer | 是 | 初始悬赏，用于判断是否已加价 | `0` |
| `items[].priority` | integer | 是 | 任务优先级 `0..3` | `2` |
| `items[].target_agent_type` | string \| null | 是 | 目标 agent 类型；显式 `null` 表示不限 | `null` |
| `items[].project` | string | 是 | 关联项目；无则为 `''` | `mio-taskhub` |
| `items[].labels` | array | 是 | 标签数组；无标签为 `[]` | `["scheduler"]` |
| `items[].est_duration_min` | integer | 是 | 预估耗时（分钟） | `240` |
| `items[].waited_seconds` | integer | 是 | 已等待秒数，自 `pool_entered_at` 起算 | `640` |
| `items[].pool_entered_at` | string | 是 | 入池时刻，ISO 8601 UTC | `2026-10-07T02:19:20+00:00` |
| `items[].expires_in` | integer \| null | 是 | 距转派单的剩余秒数；`null` = 永不过期（`grab` 模式） | `260` |
| `items[].dispatchable_at` | string \| null | 是 | 可被派单的时刻；`null` = 永不可派 | `2026-10-07T02:34:20+00:00` |
| `items[].competitors` | integer | 是 | 近 `hall_competitor_window`（默认 300s）内对该任务落过 `GrabAttempt` 的不同 agent 数 | `2` |
| `items[].match_score` | integer | 是 | 本 agent 视角的匹配分，`0..100`（v0.3 起不再出现负分；失约惩罚截断到 0）。**冷却中的 agent 分数照常计算**，只是排位靠后 | `75` |
| `items[].cooldown_active` | boolean | 是 | 该 agent 是否处于失约冷却期（`true` 时本条排位靠后，**但仍可抢**） | `false` |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 400 | `request.invalid_param` | 参数非法 | `agent` 缺失或为空串；`limit` 非整数或 `<= 0` | 补 `agent`，修正 `limit` |
| 401 | `auth.unauthenticated` | 未认证 | `--auth` 开启且未带有效 Bearer | 补令牌 |
| 429 | `rate_limited` | 全局限流 | 本 IP 60s 内超阈值 | 按 `Retry-After` 退避后重试 |
| 500 | `internal.error` | 内部错误 | 聚合查询异常 | 带 `X-Request-ID` 查日志 |

#### 示例

请求：

```bash
curl -s "http://127.0.0.1:48620/api/v1/tasks/hall?agent=codex-a&limit=20" \
  -H "X-Request-ID: hall-probe-1"
```

响应（200）：

```json
{
  "total": 1,
  "pool_depth": 1,
  "limit": 20,
  "now": "2026-10-07T02:30:00+00:00",
  "items": [
    {
      "id": "3fa41beb",
      "title": "mio-taskhub 抢单模式（grab mode）调度改造",
      "grab_mode": "hybrid",
      "bounty": 10,
      "base_bounty": 0,
      "priority": 2,
      "target_agent_type": null,
      "project": "mio-taskhub",
      "labels": ["mio-taskhub", "scheduler", "grab-mode"],
      "est_duration_min": 240,
      "waited_seconds": 640,
      "pool_entered_at": "2026-10-07T02:19:20+00:00",
      "expires_in": 260,
      "dispatchable_at": "2026-10-07T02:34:20+00:00",
      "competitors": 2,
      "match_score": 75
    }
  ]
}
```

### POST /api/v1/tasks/grab

- 用途：原子抢单。条件更新抢到即创建 run；抢不到返回抢中者
- 权限：写。调用方须为**已注册且非 offline** 的 agent
- 幂等：是，幂等键 `(task_id, agent)`——该 agent 已有在途 run 时直接返回该 run
- 副作用：`Task.state → claimed`、`Task.stage → implementing`、`Task.attempt += 1`、创建 `Run`、写 `GrabAttempt(outcome=won)`、发 `task_grabbed` 事件
- 关联状态：要求任务 `state=queued` 且 `stage=ready` 且 `grab_mode != dispatch`

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| `agent` | query | string | 是 | — | 非空；须已注册 | 抢单方身份 | `codex-a` |
| `task_id` | query | string | 是 | — | 8 位 hex，须存在 | 目标任务；**抢单必须指定任务，不支持盲领** | `3fa41beb` |
| `agent_type` | query | string | 否 | `''` | — | 能力类型；空则回查注册值 | `developer` |
| `project` | query | string | 否 | `''` | — | 领取上下文；仅当任务 `project` 为空时回填 | `mio-taskhub` |
| `workspace` | query | string | 否 | `''` | — | 领取上下文；仅当任务 `workspace` 为空时回填 | `E:/work/code/agent-dev/mio-taskhub` |

- 抢单**不接受**请求体；全部参数走 query，与存量 `claim` 一致。

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `id` | string | 是 | Run ID（8 位 hex） | `9c1f0a44` |
| `task_id` | string | 是 | 任务 ID | `3fa41beb` |
| `state` | string | 是 | Run 状态，新建时为 `claimed` | `claimed` |
| `agent_name` | string | 是 | 抢中者 agent 名 | `codex-a` |
| `attempt` | integer | 是 | 该任务的第几次尝试（含本次） | `1` |
| `bounty_at_grab` | integer | 是 | 抢中时的悬赏值，供复盘 | `10` |
| `latency_ms` | integer | 是 | 本次抢单耗时（毫秒） | `12` |
| `branch` | string | 是 | 建议的 git 分支名（`task-<task_id>`），由 `build_claim_context` 生成 | `task-3fa41beb` |
| `required_reads` | array | 是 | 动手前必须读取的文档 kind 列表 | `["spec", "api", "requirement"]` |
| `required_fr` | array | 是 | 需求文档中抽到的 FR 编号 | `["FR-1", "FR-2"]` |
| `documents` | object | 是 | 内联文档预览（`kind → 内容片段`）。**内联预览不等于已读** | `{"spec": "..."}` |
| `winner` | string \| null | 是 | 仅落空场景出现在错误响应中；成功时为 `null` | `null` |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 400 | `request.invalid_param` | 参数非法 | `agent` 或 `task_id` 缺失/空串 | 补全参数后重试 |
| 401 | `auth.unauthenticated` | 未认证 | 未带有效 Bearer | 补令牌 |
| 403 | `grab.agent_offline` | Agent 离线或未注册 | `agent` 无注册记录，或 `status == offline` | 先调 `POST /agents/heartbeat` 上线 |
| 404 | `grab.task_not_found` | 任务不存在 | `task_id` 查无记录 | 核对 ID |
| 409 | `grab.task_taken` | 已被抢走 | 条件更新未命中（他人先抢到） | 读 `winner` 后改抢其他任务；**禁止重试** |
| 409 | `grab.agent_busy` | 该 agent 已有其他在途 Run | 该 agent 已有 `claimed`/`running` 的 run 且属于**其他任务** | 先完成在手任务；**不会**返回不相关的 run |
| 422 | `grab.not_grab_mode` | 任务不可抢 | 目标任务 `grab_mode == dispatch` | 改用 `POST /tasks/claim` |
| 422 | `grab.task_not_ready` | 任务不在可抢状态 | `state != queued` 或 `stage != ready` | 等任务推进到 `ready` |
| 429 | `grab.cooldown` | 处于冷却期 | 上次失约后未满 `cooldown_seconds` | 按 `Retry-After` 等待 |

#### 示例

请求（抢到）：

```bash
curl -s -X POST "http://127.0.0.1:48620/api/v1/tasks/grab?agent=codex-a&task_id=3fa41beb"
```

响应（200）：

```json
{
  "id": "9c1f0a44",
  "task_id": "3fa41beb",
  "state": "claimed",
  "agent_name": "codex-a",
  "attempt": 1,
  "bounty_at_grab": 10,
  "latency_ms": 12,
  "branch": "task-3fa41beb",
  "required_reads": ["spec", "api", "requirement"],
  "required_fr": ["FR-1", "FR-2"],
  "documents": {"spec": "# 模块 Spec ..."},
  "winner": null
}
```

响应（落空，409）：

```json
{
  "detail": "grab.task_taken",
  "winner": "claude-b",
  "grabbed_at": "2026-10-07T02:30:01+00:00"
}
```

### POST /api/v1/tasks/{task_id}/decline

- 用途：拒单（任务未被本人占用）或退单（任务已被本人占用）
- 权限：写。退单分支要求 `agent` 是该任务的 `claimed` run 的持有者
- 幂等：是，幂等键 `(task_id, agent, UTC 日期)`——同日重复拒同一任务返回 `200` 且不重复扣额度
- 副作用：queued 分支写 `GrabAttempt(outcome=declined)` 并记 `agent.decline_count_today += 1`，任务状态**不变**；退单分支额外把 run 置 `finished`、任务回 `queued/ready`，且**不记失约、不扣拒单额度**
- 关联状态：queued 分支任务保持 `queued`；退单分支 `claimed → queued`

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| `task_id` | path | string | 是 | — | 8 位 hex，须存在 | 目标任务 | `3fa41beb` |
| `agent` | query | string | 是 | — | 非空；须已注册 | 拒单方身份 | `codex-a` |
| `run_id` | query | string | 否 | `''` | 有值时必须存在且属于该 agent | 退单分支必填；queued 分支留空 | `9c1f0a44` |
| `reason` | body | string | 否 | `''` | ≤ 500 字符 | 拒单理由，仅记录便于复盘 | `不熟悉该模块` |

- 请求体可为空对象 `{}`。`reason` 超过 500 字符报 `400`。

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `task_id` | string | 是 | 目标任务 ID | `3fa41beb` |
| `outcome` | string | 是 | 本次结果，固定为 `declined` | `declined` |
| `mode` | string | 是 | `skip`（拒单未占用）或 `release`（退单已占用） | `skip` |
| `declines_today` | integer | 是 | 该 agent 当日已用拒单次数（含本次）；退单分支不递增 | `3` |
| `decline_limit` | integer | 是 | 当日上限（配置值回显） | `20` |
| `released_run_id` | string \| null | 是 | 退单分支被释放的 run ID；拒单分支为 `null` | `null` |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 400 | `request.invalid_param` | 参数非法 | `agent` 缺失；`reason` 超长 | 修正后重试 |
| 401 | `auth.unauthenticated` | 未认证 | 未带有效 Bearer | 补令牌 |
| 403 | `auth.forbidden` | 越权退单 | `run_id` 对应的 run 不属于该 `agent` | 换成占用者身份 |
| 404 | `grab.task_not_found` | 任务不存在 | `task_id` 查无记录 | 核对 ID |
| 409 | `grab.task_taken` | 退单时任务已被他人抢走 | 退单分支条件更新未命中 | 无需处理，拒单未生效且不扣额度 |
| 429 | `grab.decline_limit` | 当日额度用尽 | `declines_today >= decline_limit`（仅拒单分支） | 等次日 UTC 00:00；紧急退单走 `release` 分支不受限 |

#### 示例

请求：

```bash
curl -s -X POST "http://127.0.0.1:48620/api/v1/tasks/3fa41beb/decline?agent=codex-a" \
  -H "Content-Type: application/json" \
  -d '{"reason": "不熟悉该模块"}'
```

响应（200）：

```json
{
  "task_id": "3fa41beb",
  "outcome": "declined",
  "mode": "skip",
  "declines_today": 3,
  "decline_limit": 20,
  "released_run_id": null
}
```

### POST /api/v1/runs/{run_id}/release

- 用途：主动释放已占用的 run，把任务交回池子
- 权限：写。要求 `agent == run.agent_name`
- 幂等：是，幂等键 `run_id`——已释放的 run 再调返回 `200`；已正常结束的返回 `409`
- 副作用：`run.state → finished`、`run.result = 'released_by_agent'`、任务回 `state=queued` / `stage=ready`、`pool_entered_at` 重置为当前时刻、写 `GrabAttempt(outcome=declined)`、发事件 `grab_released`
- 关联状态：`claimed → queued`。**不记失约、不消耗 `attempt`、不触发冷却**（显式释放区别于失约）

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| `run_id` | path | string | 是 | — | 8 位 hex，须存在 | 要释放的 run | `9c1f0a44` |
| `agent` | query | string | 是 | — | 须等于 `run.agent_name` | 释放方身份 | `codex-a` |
| `reason` | query | string | 否 | `''` | ≤ 200 字符 | 释放理由 | `上游依赖未就绪` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `run_id` | string | 是 | 被释放的 run ID | `9c1f0a44` |
| `task_id` | string | 是 | 关联任务 ID | `3fa41beb` |
| `run_state` | string | 是 | 释放后 run 状态，固定 `finished` | `finished` |
| `task_state` | string | 是 | 释放后任务执行状态，固定 `queued` | `queued` |
| `task_stage` | string | 是 | 释放后任务阶段，固定 `ready` | `ready` |
| `released_at` | string | 是 | 释放时刻，ISO 8601 UTC | `2026-10-07T02:35:00+00:00` |
| `already_released` | boolean | 是 | 本次是否为重复释放（幂等命中） | `false` |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 400 | `request.invalid_param` | 参数非法 | `agent` 缺失；`reason` 超长 | 修正后重试 |
| 401 | `auth.unauthenticated` | 未认证 | 未带有效 Bearer | 补令牌 |
| 403 | `auth.forbidden` | 越权释放 | `agent != run.agent_name` | 换正确身份 |
| 404 | `grab.task_not_found` | Run 不存在 | `run_id` 查无记录 | 核对 ID |
| 409 | `grab.not_releasable` | Run 已结束不可释放 | run 已因正常完成/失败而结束 | 刷新任务状态，无需重试 |

#### 示例

请求：

```bash
curl -s -X POST "http://127.0.0.1:48620/api/v1/runs/9c1f0a44/release?agent=codex-a&reason=%E4%BE%9D%E8%B5%96%E6%9C%AA%E5%B0%B1%E7%BB%AA"
```

响应（200）：

```json
{
  "run_id": "9c1f0a44",
  "task_id": "3fa41beb",
  "run_state": "finished",
  "task_state": "queued",
  "task_stage": "ready",
  "released_at": "2026-10-07T02:35:00+00:00",
  "already_released": false
}
```

### GET /api/v1/grab/stats

- 用途：抢单统计与排行榜，供运维判断池子健康度
- 权限：读，无资源级限制
- 幂等：是
- 副作用：无
- 关联状态：只读聚合，不改任何状态

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| `window_hours` | query | integer | 否 | 24 | `1..720` | 统计时间窗（小时），基于 `GrabAttempt.created_at` | `24` |
| `agent` | query | string | 否 | `''` | — | 有值时只统计该 agent 并入 `me` 字段 | `codex-a` |
| `top` | query | integer | 否 | 10 | `1..50` | 排行榜条数 | `5` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `window_hours` | integer | 是 | 生效的统计窗口 | `24` |
| `pool_depth` | integer | 是 | 当前池内可抢任务数（`queued + ready`） | `7` |
| `avg_wait_seconds` | integer | 是 | 池内任务平均等待秒数 | `1240` |
| `attempts_total` | integer | 是 | 窗口内抢单尝试总数 | `31` |
| `wins` | integer | 是 | 抢中次数 | `9` |
| `losses` | integer | 是 | 落空次数 | `18` |
| `declined` | integer | 是 | 拒单与释放次数 | `2` |
| `missed` | integer | 是 | 失约（抢中未开工）次数 | `2` |
| `success_rate` | number | 是 | 成功率 = `wins / attempts_total`，四位小数 | `0.2903` |
| `miss_rate` | number | 是 | 失约率 = `missed / wins`；`wins=0` 时为 `0` | `0.2222` |
| `latency_p50_ms` | integer \| null | 是 | 成功抢单耗时 P50；无样本时为 `null` | `11` |
| `latency_p95_ms` | integer \| null | 是 | 成功抢单耗时 P95；无样本时为 `null` | `38` |
| `leaderboard` | array | 是 | 排行榜，按 `wins desc` 排序，无数据为 `[]` | 见下 |
| `leaderboard[].agent_name` | string | 是 | agent 名 | `codex-a` |
| `leaderboard[].wins` | integer | 是 | 抢中次数 | `5` |
| `leaderboard[].losses` | integer | 是 | 落空次数 | `9` |
| `leaderboard[].declined` | integer | 是 | 拒单次数 | `1` |
| `leaderboard[].misses` | integer | 是 | 失约次数 | `0` |
| `leaderboard[].win_rate` | number | 是 | 胜率 = `wins / (wins + losses)`；分母为 0 时为 `0` | `0.3571` |
| `leaderboard[].avg_latency_ms` | integer \| null | 是 | 平均抢单耗时；无样本为 `null` | `14` |
| `me` | object \| null | 是 | 仅当传入 `agent` 时返回，结构同 `leaderboard` 单条 | `null` |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 400 | `request.invalid_param` | 参数非法 | `window_hours` 不在 `1..720`；`top` 不在 `1..50` | 修正后重试 |
| 401 | `auth.unauthenticated` | 未认证 | 未带有效 Bearer | 补令牌 |
| 429 | `rate_limited` | 全局限流 | 本 IP 超阈值 | 退避后重试 |
| 500 | `internal.error` | 内部错误 | 聚合查询异常 | 带 `X-Request-ID` 查日志 |

#### 示例

请求：

```bash
curl -s "http://127.0.0.1:48620/api/v1/grab/stats?window_hours=24&agent=codex-a"
```

响应（200）：

```json
{
  "window_hours": 24,
  "pool_depth": 7,
  "avg_wait_seconds": 1240,
  "attempts_total": 31,
  "wins": 9,
  "losses": 18,
  "declined": 2,
  "missed": 2,
  "success_rate": 0.2903,
  "miss_rate": 0.2222,
  "latency_p50_ms": 11,
  "latency_p95_ms": 38,
  "leaderboard": [
    {
      "agent_name": "codex-a",
      "wins": 5,
      "losses": 9,
      "declined": 1,
      "misses": 0,
      "win_rate": 0.3571,
      "avg_latency_ms": 14
    }
  ],
  "me": {
    "agent_name": "codex-a",
    "wins": 5,
    "losses": 9,
    "declined": 1,
    "misses": 0,
    "win_rate": 0.3571,
    "avg_latency_ms": 14
  }
}
```

#### 统计口径（v0.3 明确）

| 指标 | 口径 |
|---|---|
| 统计窗口 | `GrabAttempt.created_at` 落在 `[now - window_hours, now]`，**UTC 闭区间** |
| 分母来源 | `decision IN ('won','reject')` 的行；**`decision='error'`（系统故障）不计入分母**（见 §7.1） |
| `attempts_total` | 窗口内全部留痕行数，含 `declined`/`missed`，不含 `decision='error'` |
| `wins` / `losses` | `outcome='won'` / `outcome='lost'` |
| `success_rate` | `wins / (wins + losses)`，分母 0 → `0` |
| `miss_rate` | `missed / wins`，分母 0 → `0` |
| 响应时长 | `(claimed_at - pool_entered_at)` 换算毫秒，**只统计 `won`** |
| 分位点 | P50 / P95 用**最近邻法**：`P = sorted[ceil(q/100 × N) - 1]` |
| 去重规则 | 同一 `(task_id, agent)` 24h 内多次 `won` **各自计入**（重试是真实行为）；`lost` 不进分子 |
| 排行榜排序 | `wins DESC → win_rate DESC → avg_latency_ms ASC`，并列按 `agent_name` 字典序（保证稳定） |
| 空样本 | 计数与比率返回 `0`，分位返回 `null`（**不是 0ms**） |

### PATCH /api/v1/tasks/{task_id}

- 用途：存量接口的**抢单字段扩展**——写入调度模式与悬赏参数
- 权限：写，沿用存量任务管理语义（无资源级限制）
- 幂等：是（同请求体重复提交结果一致）
- 副作用：更新 `Task` 指定字段、发 `task_updated` 事件；若 `grab_mode` 由非 `grab` 改为 `grab` 且任务当前 `queued`，`pool_entered_at` 立即补齐
- 关联状态：**不接受** `state` / `stage` 入参，不改任务状态

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| `task_id` | path | string | 是 | — | 8 位 hex，须存在 | 目标任务 | `3fa41beb` |
| `grab_mode` | body | string | 否 | 不传则不变 | 枚举，见 §11 | 调度模式 | `grab` |
| `base_bounty` | body | integer | 否 | 不传则不变 | `0..10000` | 初始悬赏；同时把 `bounty` 重置为该值 | `5` |
| `bounty_step` | body | integer | 否 | 不传则不变 | `1..1000` | 每阶梯涨幅 | `10` |
| `bounty_cap` | body | integer | 否 | 不传则不变 | `0..10000`，须 ≥ `base_bounty` | 悬赏封顶 | `200` |
| `surge_interval` | body | integer | 否 | 不传则不变 | `30..86400` | 阶梯间隔秒 | `600` |
| `grab_window_seconds` | body | integer | 否 | 不传则不变 | `30..86400` | 抢中后开工窗口秒 | `900` |
| `grab_deadline_at` | body | string | 否 | 不传则不变 | ISO 8601；`null` 显式清除 | hybrid 可转派时刻 | `2026-10-07T03:00:00+00:00` |

- 字段全部可选，**未传的字段保持不变**（局部更新语义）；显式传 `null` 仅对 `grab_deadline_at` 有效（清除）。
- 存量 `editable` 字段（`title`、`priority`、`labels` 等）行为不变，本表只列新增项。

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `id` | string | 是 | 任务 ID | `3fa41beb` |
| `grab_mode` | string | 是 | 更新后的调度模式 | `grab` |
| `bounty` | integer | 是 | 当前悬赏 | `5` |
| `base_bounty` | integer | 是 | 初始悬赏 | `5` |
| `bounty_step` | integer | 是 | 每阶梯涨幅 | `10` |
| `bounty_cap` | integer | 是 | 悬赏封顶 | `200` |
| `surge_interval` | integer | 是 | 阶梯间隔秒 | `600` |
| `grab_window_seconds` | integer | 是 | 开工窗口秒 | `900` |
| `grab_deadline_at` | string \| null | 是 | 转派时刻；`null` 表示不可派 | `2026-10-07T03:00:00+00:00` |
| `pool_entered_at` | string \| null | 是 | 入池时刻；未入池为 `null` | `2026-10-07T02:19:20+00:00` |
| `state` | string | 是 | 任务执行状态（**只读**，本接口不可写） | `queued` |
| `stage` | string | 是 | 任务阶段（**只读**，本接口不可写） | `ready` |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 400 | `request.invalid_param` | 参数非法 | 字段类型错误、枚举非法、数值越界、`bounty_cap < base_bounty` | 按「约束」列修正 |
| 401 | `auth.unauthenticated` | 未认证 | 未带有效 Bearer | 补令牌 |
| 404 | `grab.task_not_found` | 任务不存在 | `task_id` 查无记录 | 核对 ID |
| 422 | `request.readonly_field` | 试图写只读字段 | 请求体含 `state` 或 `stage` | 移除该字段；状态只能经状态机接口推进 |

#### 示例

请求：

```bash
curl -s -X PATCH "http://127.0.0.1:48620/api/v1/tasks/3fa41beb" \
  -H "Content-Type: application/json" \
  -d '{"grab_mode": "grab", "base_bounty": 5, "bounty_step": 10, "bounty_cap": 200}'
```

响应（200）：

```json
{
  "id": "3fa41beb",
  "grab_mode": "grab",
  "bounty": 5,
  "base_bounty": 5,
  "bounty_step": 10,
  "bounty_cap": 200,
  "surge_interval": 600,
  "grab_window_seconds": 900,
  "grab_deadline_at": null,
  "pool_entered_at": "2026-10-07T02:19:20+00:00",
  "state": "queued",
  "stage": "ready"
}
```

## 19. 兼容性与废弃策略

- 什么算破坏性变更：
  - 字段删除、重命名、类型收窄（`integer` → `string`）。
  - 枚举删除取值或改变既有取值含义（如让 `hybrid` 变成「立即派单」）。
  - 可选参数变必填；`limit` 等上限下调。
  - 状态码改义（如把 `409 grab.task_taken` 改成 `200` 带 `success: false`）。
  - 错误体形状变化（`detail` 改名为 `message`；限流体的 `error` 字段改动）。
- 什么算兼容变更：
  - 新增可选请求参数、新增响应字段、新增枚举取值、新增接口。
  - 新增错误码（调用方按 §7 要求对未知 `detail` 兜底）。
  - 内部排序微调（四级次序不变时）。
- 变更通知方式与提前期：破坏性变更提前 14 天在 taskhub 建 `milestone` 文档广播；新增接口类变更随发随记 §20。
- 废弃流程：
  1. 标记阶段（≥ 14 天）：`v1` 响应头加 `X-API-Deprecated: true`，文档 §20 标注。
  2. 双写/双读阶段（≥ 14 天）：`v2` 与 `v1` 并存，同 DB 同业务层，数据一致。
  3. 下线阶段：`v1` 返回 `410 Gone`，保留 7 天后移除路由代码。
- 契约校验方式：
  - 单元测试对响应字段集合做快照断言（字段名与类型），少字段即失败。
  - 每日对 `GET /tasks/hall`、`GET /grab/stats` 的响应体做 schema diff，变化即告警。

## 20. 变更记录

| 日期 | 契约版本 | 变更内容 | 类型 | 影响方 |
|------|---------|---------|------|--------|
| 2026-10-07 | v1 | 初版：抢单池、原子抢单、拒单、释放、统计、任务抢单字段扩展共 6 个接口 | 兼容（全部为新增或新增字段） | 各 agent 运行时、Web UI、运维脚本 |
| 2026-10-07 | v1.1 | 评审后修订：新增 `409 grab.agent_busy` 与 `503 db_locked`；`match_score` 由 `-20..100` 收窄为 `0..100` 并新增 `cooldown_active` 字段；明确冷却为软惩罚；补 §6.3 不变量、§6.4 事件可靠性边界、统计口径 | **破坏性**（`match_score` 值域收窄、`items[]` 新增字段） | 自建抢单循环的 agent 运行时 |

- v1.1 的破坏性变更说明：`match_score` 不再返回负数，依赖「负分表示冷却」的调用方需改读新增的 `cooldown_active` 布尔字段。

## 21. 附录

- HTTP 状态码全集（本契约实际使用）：`200`、`400`、`401`、`403`、`404`、`409`、`422`、`429`、`500`。
- 业务错误码全集：与 §7 一致——`request.invalid_param`、`request.readonly_field`、`auth.unauthenticated`、`auth.forbidden`、`grab.agent_offline`、`grab.task_not_found`、`grab.task_taken`、`grab.not_releasable`、`grab.not_grab_mode`、`grab.task_not_ready`、`grab.cooldown`、`grab.decline_limit`、`rate_limited`、`internal.error`。
- 枚举全集：与 §11 一致。
- 数据字典 / 术语表：

| 术语 | 含义 |
|------|------|
| 抢单池 / 大厅 | `state=queued` 且 `stage=ready` 且 `grab_mode != dispatch` 的任务集合 |
| 悬赏（bounty） | 任务在池中停留越久越高的**整数权重**，用于排序与展示，不是货币 |
| 匹配分（match_score） | 调用方视角的任务相关性打分，只影响顺序，不参与任何准入判定 |
| 竞争人数（competitors） | 近期对该任务发起过抢单的不同 agent 数 |
| 失约（missed） | 抢中后在开工窗口内无任何心跳，被系统释放回池 |
| 退单 | 已占用的 run 被主动释放（`decline` 退单分支或 `release`），不计失约 |
| 派单让位 | 抢单模式下任务不出现在派单候选集中，避免被自动指派截胡 |
| CAS | Compare-And-Set，用条件更新 `WHERE state='queued'` 的 `rowcount` 判定抢单胜负 |

- 相关文档：
  - 状态模型与字段定义：[状态模型](docs/taskhub/grab-mode/data-model.md)
  - 测试与验收用例：[测试验收](docs/taskhub/grab-mode/test.md)
