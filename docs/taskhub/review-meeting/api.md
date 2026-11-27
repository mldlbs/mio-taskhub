# 接口契约（API Contract）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-11-26 16:06 |
| 状态 | approved |
| 负责人 | 产品管理专家团 |
| 适用范围 | 研发评审会的 8 个 REST 端点；调用方为 Web 前端与 agent（经 HTTP） |
| 明确不含 | MCP 工具签名（见 mcp_server.py）、内部 DB 直连、既有 decisions/reviews 端点本体 |

> 每个接口的每个参数、每个响应字段、每个错误码都给出位置/类型/必填/默认/约束/示例。
> 错误响应遵循本项目现行两套业务码并存约定：`detail.code`（runs 系）与 `detail.error`（policy_guard 系），
> 本模块新增端点统一用 `detail.code`，前端 ApiError 两者皆收、code 优先。

## 1. 文档信息与范围

| 项 | 值 |
|----|----|
| 契约版本 | v1.0 |
| 适用范围 | 评审点提取、列表/详情、裁剪、裁决、会议开闭、回溯；REST 前缀 `/api/v1` |
| 明确不含 | 文档质量门端点、stage gate 端点、既有 `/reviews/queue`、`/{task_id}/reviews`（本契约复用但不重定义） |

## 2. 环境与基础地址

| 环境 | Base URL | 说明 |
|------|----------|------|
| dev | `http://127.0.0.1:48620/api/v1` | 本机 SQLite，默认端口 |
| staging | — | 本项目无独立 staging 环境 |
| prod | `http://127.0.0.1:48620/api/v1` | 本机 exe 部署，端口同上 |

- 路径前缀：`/api/v1`
- 协议与端口：HTTP，48620；仅监听 127.0.0.1
- 网关 / 反向代理差异：无网关；路径前缀不被重写
- 字符编码：UTF-8
- 内容类型：`application/json; charset=utf-8`

## 3. 版本策略

- 版本位置：URL 路径前缀（`/api/v1`）
- 当前版本：v1
- 版本号规则：`v` + 整数，破坏性变更才升位
- 多版本并存策略：当前仅 v1；后续 v2 并存时 v1 至少保留一个迭代
- 旧版本弃用流程：公告 → 双版本并存 → 下线，各阶段 ≥2 周

## 4. 认证与鉴权

| 项 | 约定 |
|----|------|
| 认证方式 | 本机信任域（环回）+ 可选 admin token |
| 凭证来源 | 环境变量或 `--auth` 启动参数；未配置则不校验 |
| 传递位置 | `Authorization: Bearer <token>`（配置时）；或 `X-TaskHub-Admin: 1`（环回管理面） |
| 有效期 | 不设过期（本机 token） |
| 刷新方式 | 不适用 |
| 权限模型 | 资源级：任务归属者/主审可写，其他人只读 |
| 未认证响应 | HTTP 401 + `detail.code=AUTH_REQUIRED`（仅配置 token 时） |
| 越权响应 | HTTP 403 + `detail.code=FORBIDDEN` |

- 写操作（PATCH/POST）校验顺序固定：**404 → 所有权 → 状态**，不得颠倒。
- 配置 admin token 时，`X-TaskHub-Admin: 1` 仅在环回地址被接受，非环回一律 403。

## 5. 通用请求头

| Header | 必填 | 类型 | 默认 | 说明 |
|--------|------|------|------|------|
| `Authorization` | 条件必填 | string | — | 配置 admin token 时必填，格式 `Bearer <token>` |
| `Content-Type` | 写请求必填 | string | `application/json` | 所有 POST/PATCH 必填 |
| `X-TaskHub-Admin` | 否 | string | — | 环回管理面凭据，值为 `1` |
| `X-Request-Id` | 否 | string | 服务端生成 | 链路追踪，原样透传回响应 |

## 6. 通用响应结构

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `code` | integer | 是 | 业务码，0 表示成功；本项目部分端点走裸结构，见下 |
| `message` | string | 是 | 人类可读信息，仅供展示，不得用于分支判断 |
| `data` | object \| null | 否 | 业务数据；失败时为 null |
| `request_id` | string | 是 | 与请求头一致，排障时提供 |

- 本模块新增端点沿用现有 FastAPI 实际返回：**成功返回业务对象裸结构**（非统一外壳），**失败返回 `{"detail": {...}}`**。
- `data` 为 null 与字段缺失的语义：列表端点返回 `{count, items}`，`items=[]` 表示无匹配；单例端点 404 表示不存在。
- 例外：`POST /reviews/extract` 返回 `{task_id, version, points, skipped}` 裸对象，不包 `data`。

## 7. 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 400 | BAD_REQUEST | 请求格式错误 | JSON 解析失败 | 检查 body 是否为合法 JSON |
| 401 | AUTH_REQUIRED | 未认证 | 配置 token 但未提供/无效 | 补 Bearer token |
| 403 | FORBIDDEN | 越权 | 非任务归属者写操作 | 检查归属或切换用户 |
| 404 | TASK_NOT_FOUND / POINT_NOT_FOUND / DISCUSSION_NOT_FOUND | 资源不存在 | id 查无记录（校验顺序第一步） | 核对 id |
| 409 | CONFLICT | 状态冲突 | 重复关会 / 第二条 active 会议 / 终态再裁决 | 查 GET 确认历史，勿重试 |
| 422 | VALIDATION_ERROR | 参数校验失败 | 必填缺失、理由为空、字段格式错 | 按 detail.message 的人话提示修正 |
| 429 | RATE_LIMITED | 限流 | 超过 600 req/min/IP | 退避重试，写操作建议 ≥0.4s 间隔 |
| 500 | INTERNAL_ERROR | 服务内部错误 | 未捕获异常 | 查服务端日志并附 request_id |

- detail 结构：`{"message": "<人话提示>", "code": "<业务码>", "quality": {...}}`（质量相关时含 quality）。
- 422 的 `message` 必须含"你要做的是：..."句式，禁止只写字段名。

## 8. 分页 / 排序 / 过滤

| 项 | 参数 | 类型 | 默认 | 上限 | 说明 |
|----|------|------|------|------|------|
| 页码 | `page` | integer | 1 | 1000 | 从 1 起 |
| 每页条数 | `size` | integer | 50 | 200 | 超过 200 截断为 200 |
| 排序字段 | `sort` | string | `risk_level` | — | 取 `risk_level`/`seq`/`created_at` |
| 排序方向 | `order` | string | `desc` | — | `asc`/`desc` |
| 过滤条件 | `status` / `cluster_id` / `kind` | string | — | — | 可组合，AND 语义 |

- 分页响应字段：`total`（匹配总数）、`page`、`size`、`items`（当前页数组）。
- 采用偏移分页；深翻页（page×size>10000）返回 422。
- 超限行为：size 超 200 截断为 200，不报错；page 超范围返回空 items。
- 多字段排序优先级：先 risk_level 降序，再 cluster_id 升序，再 seq 升序。

## 9. 字段命名与类型规范

| 项 | 规范 |
|----|------|
| 命名风格 | snake_case，禁 camelCase；反例 `reviewPointId` 违规 |
| ID 类型 | string；任务 8 位 hex，评审点 `RP-{task8}-{seq}`， Discussion/Decision 为 UUID |
| 布尔 | JSON true/false，禁 "true"/1 |
| 数组 | 空数组 `[]` 表示"无"，`null` 表示"未提供/不适用"，二者语义不同 |
| 可选字段 | 缺失表示调用方不指定（走默认）；显式 null 表示清空 |

## 10. 时间 / 数值 / 空值约定

- 时间格式：ISO 8601，如 `2026-11-26T08:00:15.309878+00:00`
- 时区：UTC（服务端统一），前端本地化展示
- 时间精度：微秒
- 数值范围与溢出处理：`decisions`/`action_items` 计数为非负整数，超 2^31 不现实，无需特殊处理
- 空值语义：`why_it_matters=""` 表示 LLM 未提供；`source_doc.version=""` 表示提取时无版本信息（异常态）

## 11. 枚举全集

| 枚举 | 取值 | 含义 | 出现在哪些接口 |
|------|------|------|---------------|
| review_point.status | candidate / shortlisted / discussing / resolved / rejected / deferred | 评审点生命周期 | 全部点接口 |
| review_point.kind | section_gap / fr_contract / api_contract / data_rule / risk_assumption / decision_point / premise | 评审点类型 | extract / points |
| risk_level | high / medium / low | 风险分级 | extract / points |
| origin | rule / llm | 候选点来源 | extract / points |
| suggested_action | confirm / clarify / decide / test / out_of_scope | LLM 建议动作 | extract / points / decide |
| decision | approve / reject / comment | 关会结论 | close |
| Discussion.status | open / closed | 会议状态 | open / close |

- 调用方必须能处理未知枚举值（前向兼容）：未知 status 按只读展示，不得据此改写本地状态机。

## 12. 幂等性与重试

| 接口 / 场景 | 幂等 | 幂等键 | 重试建议 |
|------------|------|--------|---------|
| POST /reviews/extract | 是 | `(task_id, version, dedup_key)` | 可安全重试，命中跳过不新增 |
| PATCH /points/{id}/shortlist | 是 | point_id + 目标状态 | 可重试 |
| POST /points/{id}/decide | 否 | — | 状态机防重：终态再操作 409，勿盲目重试 |
| POST /meetings/open | 部分 | task_id + 单 active 约束 | 重复创建返回既有会议或 409 |
| POST /meetings/{id}/close | 否 | discussion_id | 重复关会 409，不可重试 |
| GET 类 | 是 | — | 可重试 |

- 安全重试的状态码：429 / 500（GET 与 extract 可重试）
- 绝不可重试：409（冲突）、已终态的 decide/close
- 退避策略：指数退避，起始 0.4s，上限 8s
- 幂等键有效期：跟随文档版本；版本变更则重新计算

## 13. 限流与配额

| 范围 | 阈值 | 窗口 | 超限响应 |
|------|------|------|---------|
| 全站 IP | 600 req/min | 60s 滑动 | HTTP 429 + `Retry-After` |
| extract | 10 req/min/task | 60s | HTTP 429 |
| 写操作建议客户端间隔 | ≥0.4s | — | 避免批量触发 429 |

- `/runs/*/heartbeat|result`、`/tasks/claim` 免限流；本契约端点不限免。

## 14. 超时与并发

- 服务端处理超时：extract 30s（含 LLM 超时 8s 降级）；其余端点 5s
- 建议客户端超时：extract 35s，其余 10s
- 乐观锁 / 版本号字段：评审点 `updated_at` 作并发冲突判定
- 并发冲突响应：409 + `detail.code=CONFLICT`
- 长任务处理方式：extract 同步阻塞返回；LLM 超时走降级路径不转异步任务

## 15. 文件上传与下载

| 项 | 约定 |
|----|------|
| 上传方式 | 无文件上传接口 |
| 单文件上限 | 不适用 |
| 允许类型 | 不适用 |
| 下载方式 | 文档正文经现有 `/tasks/{id}/doc?kind=` GET 获取 |
| 鉴权方式 | 同本契约 §4 |

- 本模块不引入新的文件传输接口；文档读取复用既有端点。

## 16. 安全与脱敏

- 敏感字段清单及脱敏规则：本模块不存储凭据；`source_doc.path` 为本地路径，响应原样返回（本机信任域）
- 传输要求：HTTP 环回；不对外暴露
- 日志禁止记录的字段：admin token、Bearer 凭证
- 越权与注入防护：写操作 404→所有权→状态 顺序校验；SQLite 参数化查询；路径经 path_guard 约束

## 17. 接口清单

| Method | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|--------|------|------|------|------|------|---------|
| POST | /api/v1/reviews/extract | 触发评审点提取 | 主审 | 是 | 10/min/task | S0→S1 |
| GET | /api/v1/reviews/points | 评审点列表 | 归属者只读 | 是 | 600/min | S1~S5 |
| GET | /api/v1/reviews/points/{point_id} | 单点详情 | 归属者只读 | 是 | 600/min | 任意 |
| PATCH | /api/v1/reviews/points/{point_id}/shortlist | 勾选/取消 shortlist | 主审 | 是 | 600/min | S1→S2 |
| POST | /api/v1/reviews/points/{point_id}/decide | 六动作裁决 | 主审 | 否 | 600/min | S3 |
| POST | /api/v1/reviews/meetings/open | 创建 review 讨论容器 | 主审 | 部分 | 600/min | S2→S3 |
| POST | /api/v1/reviews/meetings/{discussion_id}/close | 关会门控+汇总 | 主审 | 否 | 600/min | S4→S5 |
| GET | /api/v1/reviews/meetings/{discussion_id}/trace | 全链回溯（P1） | 归属者只读 | 是 | 600/min | 任意 |

- 清单与 §18 明细一一对应，8 项均有独立小节。

## 18. 接口明细

### 18.1 POST /api/v1/reviews/extract —— 触发评审点提取

- 用途：对指定任务的指定文档类型运行规则提取 + LLM 增强，返回候选评审点
- 权限：主审（任务归属者）
- 幂等：是（同版本同 dedup_key 跳过）
- 副作用：写入 review_point 行；不改文档状态
- 关联状态：S0→S1

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| task_id | body | string | 是 | — | 8 位 hex，任务须存在 | 目标任务 ID | a20f3f9f |
| kinds | body | array[string] | 否 | 四类全取 | 子集 of requirement/spec/api/decision | 要提取的文档类型 | ["spec","api"] |
| include_llm | body | boolean | 否 | true | — | 是否调 LLM 增强 | false |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| task_id | string | 是 | 同请求 | a20f3f9f |
| version | string | 是 | 文档版本 sha256 | sha256:9f3a... |
| points | array | 是 | 候选点数组，每项含 15 个 P0 字段 | [{"id":"RP-a20f3f9f-01"}] |
| skipped | integer | 是 | 幂等跳过的重复点数 | 0 |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | TASK_NOT_FOUND | 任务不存在 | task_id 查无记录 | 核对 id |
| 403 | FORBIDDEN | 非归属者 | 越权调用 | 检查归属 |
| 422 | VALIDATION_ERROR | kinds 含不支持类型 | 传了 plan/design | 只看四类文档 |
| 409 | CONFLICT | 文档质量门未过 | errors>0 或分数<80 | 先修文档质量 |

#### 示例

请求：

```bash
curl -s -X POST "http://127.0.0.1:48620/api/v1/reviews/extract" \
  -H "Content-Type: application/json" \
  -d '{"task_id":"a20f3f9f","kinds":["api"],"include_llm":false}'
```

响应（200，节选）：

```json
{"task_id":"a20f3f9f","version":"sha256:9f3a...","points":[{"id":"RP-a20f3f9f-01","kind":"api_contract","risk_level":"high","status":"candidate"}],"skipped":0}
```

### 18.2 GET /api/v1/reviews/points —— 评审点列表

- 用途：按 task 列出评审点，支持状态/簇/类型筛选与分页
- 权限：任务归属者只读
- 幂等：是
- 副作用：无
- 关联状态：S1~S5

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| task_id | query | string | 是 | — | 8 位 hex | 任务 ID | a20f3f9f |
| status | query | string | 否 | — | 枚举 status | 按状态筛选 | shortlisted |
| cluster_id | query | string | 否 | — | — | 按簇筛选 | C1 |
| kind | query | string | 否 | — | 枚举 kind | 按类型筛选 | api_contract |
| page | query | integer | 否 | 1 | ≥1 | 页码 | 1 |
| size | query | integer | 否 | 50 | 1-200 | 每页条数 | 50 |
| sort | query | string | 否 | risk_level | risk_level/seq/created_at | 排序字段 | seq |
| order | query | string | 否 | desc | asc/desc | 排序方向 | asc |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| total | integer | 是 | 匹配总数 | 11 |
| page | integer | 是 | 当前页 | 1 |
| size | integer | 是 | 每页条数 | 50 |
| items | array | 是 | 评审点数组，每项含 15 P0 字段 + decision_id | [{"id":"RP-a20f3f9f-01"}] |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | TASK_NOT_FOUND | 任务不存在 | task_id 查无记录 | 核对 id |
| 422 | VALIDATION_ERROR | 分页参数越界 | page>1000 或 size>200 | 调小参数 |

#### 示例

请求：`curl -s "http://127.0.0.1:48620/api/v1/reviews/points?task_id=a20f3f9f&status=shortlisted"`

响应（200）：`{"total":6,"page":1,"size":50,"items":[{"id":"RP-a20f3f9f-01","status":"shortlisted"}]}`

### 18.3 GET /api/v1/reviews/points/{point_id} —— 单点详情

- 用途：查看单条评审点完整字段，含 quote/anchor/关联簇
- 权限：任务归属者只读
- 幂等：是
- 副作用：无
- 关联状态：任意

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| point_id | path | string | 是 | — | `RP-{task8}-{seq}` | 评审点 ID | RP-a20f3f9f-01 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| id | string | 是 | 点 ID | RP-a20f3f9f-01 |
| source_doc | object | 是 | {kind,path,version,status} | {"kind":"api","version":"sha256:9f3a..."} |
| quote | string | 是 | 原文逐字引用（≤200 字） | decider 自报，无角色区分 |
| anchor | string | 是 | 标题链+行号+表格行键 | §4 L58 |
| why_it_matters | string | 是 | 重要性与风险说明 | 无鉴权导致越权 |
| decision_id | string \| null | 是 | 转 Decision 时回填 | — |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | POINT_NOT_FOUND | 点不存在 | point_id 查无记录 | 核对 id |
| 403 | FORBIDDEN | 非归属者 | 越权 | 检查归属 |

#### 示例

请求：`curl -s "http://127.0.0.1:48620/api/v1/reviews/points/RP-a20f3f9f-01"`

响应（200）：`{"id":"RP-a20f3f9f-01","source_doc":{"kind":"api","version":"sha256:9f3a..."},"quote":"decider 自报，无角色区分","anchor":"§4 L58","why_it_matters":"无鉴权导致越权","decision_id":null}`

### 18.4 PATCH /api/v1/reviews/points/{point_id}/shortlist —— 勾选/取消 shortlist

- 用途：将候选点纳入或移出待评清单
- 权限：主审
- 幂等：是（重复设同一状态返回成功）
- 副作用：更新 review_point.status；不改会议容器
- 关联状态：S1→S2

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| point_id | path | string | 是 | — | `RP-{task8}-{seq}` | 评审点 ID | RP-a20f3f9f-01 |
| selected | body | boolean | 是 | — | — | true=入会，false=移出 | true |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| id | string | 是 | 点 ID | RP-a20f3f9f-01 |
| status | string | 是 | 更新后状态 | shortlisted |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | POINT_NOT_FOUND | 点不存在 | 校验顺序第一步 | 核对 id |
| 403 | FORBIDDEN | 非主审 | 越权 | 检查归属 |
| 409 | CONFLICT | 点已终结 | status ∈ 终态仍改 shortlist | 查 GET 确认历史 |
| 422 | VALIDATION_ERROR | selected 缺失 | body 无该字段 | 补布尔值 |

#### 示例

请求：`curl -s -X PATCH ".../points/RP-a20f3f9f-01/shortlist" -H "Content-Type: application/json" -d '{"selected":true}'`

响应（200）：`{"id":"RP-a20f3f9f-01","status":"shortlisted"}`

### 18.5 POST /api/v1/reviews/points/{point_id}/decide —— 六动作裁决

- 用途：对单点执行采纳/修改/驳回/转Decision/转TC/暂缓
- 权限：主审
- 幂等：否（终态再操作 409）
- 副作用：更新点状态 + 写 DiscussionMessage + 可能的 Decision/Subtask
- 关联状态：S3

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| point_id | path | string | 是 | — | `RP-{task8}-{seq}` | 评审点 ID | RP-a20f3f9f-01 |
| action | body | string | 是 | — | confirm/amend/reject/decide/test/defer | 裁决动作 | reject |
| rationale | body | string | 条件 | — | reject/defer 必填，≥8 字 | 理由 | 该问题已被认证层覆盖 |
| options | body | array | 条件 | — | action=decide 时必填，每项 {label,note} | 决策选项 | [{"label":"接入认证"}] |
| role | body | string | 否 | product | 会议 roles 之一 | 标注角色 | tech |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| id | string | 是 | 点 ID | RP-a20f3f9f-01 |
| status | string | 是 | 裁决后状态 | rejected |
| decision_id | string \| null | 否 | 转 Decision 时返回 | — |
| message_id | string | 是 | 写入的 DiscussionMessage ID | msg-3f2a |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | POINT_NOT_FOUND | 点不存在 | 校验顺序第一步 | 核对 id |
| 403 | FORBIDDEN | 非主审 | 越权 | 检查归属 |
| 409 | CONFLICT | 点已终态 | 重复裁决 | 查 GET 确认历史 |
| 422 | VALIDATION_ERROR | 理由为空 | reject/defer 未填 rationale | 你要做的是：填写至少 8 字的驳回理由 |

#### 示例

请求：`curl -s -X POST ".../points/RP-a20f3f9f-01/decide" -H "Content-Type: application/json" -d '{"action":"reject","rationale":"该问题已被现有认证层覆盖，无需改动"}'`

响应（200）：`{"id":"RP-a20f3f9f-01","status":"rejected","decision_id":null,"message_id":"msg-3f2a"}`

### 18.6 POST /api/v1/reviews/meetings/open —— 创建 review 讨论容器

- 用途：为已完成 shortlist 的任务创建一条 Discussion(mode=review) 作为会议容器
- 权限：主审
- 幂等：部分（已有 active 会议返回该会议或 409）
- 副作用：创建 Discussion 行；把 shortlist 点置 discussing 并写消息
- 关联状态：S2→S3

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| task_id | body | string | 是 | — | 8 位 hex | 任务 ID | a20f3f9f |
| roles | body | array[string] | 否 | ["product"] | 子集 of product/tech/redteam/qa | 参与角色 | ["product","tech","redteam"] |
| prompt_version | body | string | 否 | current | RolePrompt 版本 | 提示词快照版本 | v2 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| discussion_id | string | 是 | 会议容器 ID | 7c1e... |
| mode | string | 是 | 恒为 review | review |
| roles | array | 是 | 角色快照 | ["product","tech"] |
| point_count | integer | 是 | 纳入的点数 | 6 |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | TASK_NOT_FOUND | 任务不存在 | 校验顺序第一步 | 核对 id |
| 409 | CONFLICT | 已有 active 会议 | 同 task 存在 open review Discussion | 用现有会议，勿重复创建 |
| 422 | VALIDATION_ERROR | 无 shortlist 点 | 未勾选任何点就开会 | 先勾选 5-10 个点 |

#### 示例

请求：`curl -s -X POST ".../meetings/open" -H "Content-Type: application/json" -d '{"task_id":"a20f3f9f","roles":["product","tech","redteam"]}'`

响应（200）：`{"discussion_id":"7c1e...","mode":"review","roles":["product","tech","redteam"],"point_count":6}`

### 18.7 POST /api/v1/reviews/meetings/{discussion_id}/close —— 关会门控与汇总

- 用途：校验门控、写五段 review、追加 TaskReview、生成 Subtask
- 权限：主审
- 幂等：否（重复关会 409）
- 副作用：Discussion 关闭 + TaskReview 追加 + Subtask 生成；**不改文档状态**
- 关联状态：S4→S5

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| discussion_id | path | string | 是 | — | UUID | 会议 ID | 7c1e... |
| decision | body | string | 是 | — | approve/reject/comment | 关会结论 | approve |
| summary | body | string | 条件 | — | decision=approve/reject 时 ≥8 字 | 纪要摘要 | 六点裁决完成，一项转决策 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| discussion_id | string | 是 | 会议 ID | 7c1e... |
| status | string | 是 | closed | closed |
| task_review_id | string | 是 | 新增的 TaskReview ID | tr-8b2c |
| subtask_ids | array | 是 | 生成的 Subtask ID 列表 | ["st-1","st-2"] |
| gaps | array | 否 | 门控缺口（仅 409 时） | ["还有 2 点未终态"] |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | DISCUSSION_NOT_FOUND | 会议不存在 | 校验顺序第一步 | 核对 id |
| 409 | CONFLICT | 门控未过或重复关会 | 点未终态 / decisions<2 / action_items<1 / 已 closed | 看 gaps 逐项补齐 |
| 422 | VALIDATION_ERROR | summary 太短 | <8 字 | 你要做的是：写至少 8 字的纪要摘要 |

#### 示例

请求：`curl -s -X POST ".../meetings/7c1e.../close" -H "Content-Type: application/json" -d '{"decision":"approve","summary":"六点裁决完成，一项转决策待拍板"}'`

响应（200）：`{"discussion_id":"7c1e...","status":"closed","task_review_id":"tr-8b2c","subtask_ids":["st-1","st-2"]}`

### 18.8 GET /api/v1/reviews/meetings/{discussion_id}/trace —— 全链回溯（P1）

- 用途：按会议查所有评审点的完整溯源链（原文位置 + 版本 + 裁决 + 关联 Decision/Subtask）
- 权限：任务归属者只读
- 幂等：是
- 副作用：无
- 关联状态：任意

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| discussion_id | path | string | 是 | — | UUID | 会议 ID | 7c1e... |
| point_id | query | string | 否 | — | `RP-{task8}-{seq}` | 只看某点 | RP-a20f3f9f-07 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| discussion_id | string | 是 | 会议 ID | 7c1e... |
| trace | array | 是 | 溯源链数组，每项含 RP-id/quote/anchor/version/status/decision_id/subtask_ids | [{"id":"RP-a20f3f9f-07"}] |
| count | integer | 是 | trace 条数 | 6 |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | DISCUSSION_NOT_FOUND / POINT_NOT_FOUND | 资源不存在 | id 查无记录 | 核对 id |
| 403 | FORBIDDEN | 非归属者 | 越权 | 检查归属 |

#### 示例

请求：`curl -s ".../meetings/7c1e.../trace?point_id=RP-a20f3f9f-07"`

响应（200）：`{"discussion_id":"7c1e...","trace":[{"id":"RP-a20f3f9f-07","quote":"decider 自报，无角色区分","anchor":"§4 L58","version":"sha256:9f3a...","status":"deferred","decision_id":"d-1a2b","subtask_ids":[]}],"count":1}`

## 19. 兼容性与废弃策略

- 破坏性变更：字段删除/改名、类型收窄、枚举删值、必填变严、状态码改义
- 兼容变更：新增可选字段、新增枚举值、新增端点
- 变更通知方式与提前期：变更记录表 + 发版说明，破坏性变更提前 ≥2 周
- 废弃流程：标记 deprecated → 双实现并存 → 下线，各阶段 ≥2 周
- 契约校验方式：快照测试 + 每日 diff；api.md 与实现不一致时以契约为准修实现

## 20. 变更记录

| 日期 | 契约版本 | 变更内容 | 类型 | 影响方 |
|------|---------|---------|------|--------|
| 2026-11-26 | v1.0 | 首版：8 端点、15 P0 字段、六动作、关会门控 | 新增 | Web 前端、agent MCP |

## 21. 附录

- HTTP 状态码全集：200 / 400 / 401 / 403 / 404 / 409 / 422 / 429 / 500
- 业务错误码全集：见 §7，按 `detail.code` 读取；前端 ApiError 兼容 `detail.error`
- 枚举全集：见 §11
- 数据字典：review_point=评审点；shortlist=待评清单；RP-id=评审点 ID；五段=risks/divergences/suggestions/decisions/action_items
- 相关文档：状态模型 `docs/taskhub/review-meeting/data-model.md`、测试验收 `docs/taskhub/review-meeting/test.md`
