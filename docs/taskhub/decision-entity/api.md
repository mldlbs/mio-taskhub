> 软件项目文档链 · 7 件套  
> 上游：[模块 Spec](docs/taskhub/decision-entity/spec.md) | 下游：[测试验收](docs/taskhub/decision-entity/test.md)  
> 任务：89a90af6（Decision 实体）

# 接口契约（API Contract）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-11-04 13:36 |
| 状态 | approved |
| 负责人 | agent:workbuddy |
| 适用范围 | /api/v1/decisions 全部 5 个端点 + /tasks/inbox 的 decide 队列扩展 |

## 1. 文档信息与范围

| 项 | 值 |
|----|----|
| 契约版本 | v1 |
| 适用范围 | Decision 实体登记/列表/详情/裁决/作废；inbox decide 队列决策条目 |
| 明确不含 | 任务状态机接口（/tasks/{id}/stage 等）、文档链接口、MCP 传输层（仅描述工具到 REST 的映射） |

- 调用方：Web 前端（fetch /api/v1）、MCP 工具（httpx 内环回）、用户脚本（curl / urllib）
- 本地单机服务，无跨机调用方

## 2. 环境与基础地址

| 环境 | Base URL | 说明 |
|------|----------|------|
| dev（源码 hub） | `http://127.0.0.1:48620/api/v1` | uvicorn 源码直跑 |
| prod（PyInstaller exe） | `http://127.0.0.1:48620/api/v1` | exe supervisor + worker，端口同源码版 |

- 路径前缀：`/api/v1`（全部端点）
- 协议与端口：HTTP / 48620（仅绑定 127.0.0.1）
- 网关 / 反向代理差异：无（Web 前端经 vite dev proxy 与同源静态托管直连，路径不重写）
- 字符编码：UTF-8
- 内容类型：`application/json; charset=utf-8`

## 3. 版本策略

- 版本位置：URL 路径（`/api/v1`）
- 当前版本：v1
- 版本号规则：路径段整体升级（v1 → v2），不做单接口版本化
- 多版本并存策略：单机本地服务，不做并存；升级随 exe 一起发布
- 旧版本弃用流程：小版本字段追加按「兼容与废弃策略」节处理，路径级弃用在变更记录公告

## 4. 认证与鉴权

| 项 | 约定 |
|----|------|
| 认证方式 | 无（127.0.0.1 信任域） |
| 凭证来源 | 不适用 |
| 传递位置 | 不适用 |
| 有效期 | 不适用 |
| 刷新方式 | 不适用 |
| 权限模型 | 无角色区分；decider 字段由调用方自报（默认 user） |
| 未认证响应 | 不适用（无认证层） |
| 越权响应 | 不适用 |

- 信任边界：服务仅监听 127.0.0.1，本机进程即受信（与既有 /tasks 等全部接口同口径）
- MCP 侧风控：POST /decisions、POST /decisions/{id}/resolve 归类 write 级（mcp_risk），非 destructive，默认放行

## 5. 通用请求头

| Header | 必填 | 类型 | 默认 | 说明 |
|--------|------|------|------|------|
| `Content-Type` | POST 是 | string | — | `application/json` |
| `Accept` | 否 | string | `*/*` | 建议传 `application/json` |

- GET 请求无必填头；本服务无 `Authorization` / 自定义头

## 6. 通用响应结构

| 端点形态 | 结构 | 说明 |
|----------|------|------|
| 单实体（create/get/resolve/supersede） | Decision JSON（15 字段，见 §16 枚举与字段） | 200 直接返回对象 |
| 列表 | `{"count": int, "status": str, "items": [Decision]}` | count 为本次返回条数 |
| 错误 | FastAPI 默认 `{"detail": "人话中文错误"}` | detail 为字符串，非对象 |

- 成功无包装信封（无 code/data 包装）；失败 HTTP 状态即语义
- 时间字段一律 ISO 8601 UTC 字符串（`2026-11-04T05:33:02.123456+00:00`），无值时 null

## 7. 统一错误码

| HTTP | 触发场景 | detail 要点 | 恢复建议（写死在 detail 里） |
|------|----------|-------------|------------------------------|
| 422 | question 缺失 / options 形态错 / deadline 非 ISO / choice 缺失 / status 非法 / successor_id 不存在 | 带字段级定位 | 每条含「你要做的是：…」具体动作 |
| 404 | decision_id 不存在 | `decision {id} 不存在` | 检查 id 来源（inbox / GET /decisions） |
| 409 | 终态再 resolve / 终态再 supersede | 显示当前终态与已选 choice | 查 GET 详情确认历史，或新开决策 |
| 409 | （限流触发时）请求过密 | 通用限流文案 | 退避重试（见 §13 限流） |

- 全部 detail 为中文人话（P0-2 口径），非机器码

## 8. 字段命名与类型规范

- 命名：JSON 字段 snake_case，与 Python 模型字段一一对应（无 camelCase 转换层）
- 类型：字符串 / 整数 / 布尔 / 数组 / null；时间 = ISO 8601 字符串（不做 epoch）
- 空值：未填写一律返回空串 `""`（字符串）或 `null`（时间/数组），不用 `""` 冒充时间
- options 数组元素：`{"label": str, "note": str}`（note 可缺省，序列化后恒为两键）

## 9. 幂等性与重试

| 端点 | 幂等 | 说明 |
|------|------|------|
| POST /decisions | 否 | 每次调用产生新 id；重复登记会得到两条（调用方按 question 自查） |
| GET /decisions、GET /decisions/{id} | 是 | 纯读 |
| POST /decisions/{id}/resolve | 否（但有护栏） | 首次 200，重复返回 409——终态校验天然防双写 |
| POST /decisions/{id}/supersede | 否（但有护栏） | 同上，终态 409 |

- 重试建议：resolve/supersede 失败后先 GET 详情判终态再决定重试；create 失败可直接重试但可能重复

## 10. 分页

- `GET /decisions` 仅支持 `limit`（1–200，默认 50），无 offset/cursor
- 排序固定 `created_at` 降序（最新在前）；调用方要全量自行调大 limit
- `GET /tasks/inbox` 各队列固定 LIMIT 20，全量数字在 summary（本任务不新增分页能力）

## 11. 时间 / 数值 / 空值约定

- 时间：请求可传 `2026-11-14T12:00`（naive，服务端补 UTC）或带 `Z` / `+00:00`；响应恒为完整 ISO 8601 UTC
- 数值：无浮点字段；age_days 为整数天（不足 1 天计 0；created_at 缺失计 -1）
- 空值：choice/rationale/decider 在 open 态为 `""`；deadline/resolved_at 未发生为 `null`

## 12. 枚举全集

| 字段 | 枚举值 | 说明 |
|------|--------|------|
| Decision.status | `open` / `resolved` / `superseded` | open 可裁决可作废；后两者为终态 |
| 列表查询 status | 同上 + 必须传其一 | 传其他值 → 422 |
| inbox 条目 kind | `task` / `decision` | decide 队列两种来源；task 条目带 title，decision 条目 title=question |

## 13. 限流与配额

- 沿用全局 middleware 令牌桶（与既有 /tasks 同池），本任务不新增决策专属限流
- 触发时返回 409 限流文案；Web 轮询周期 30s 远低于阈值，正常不会触发

## 14. 超时与并发

- 服务端处理：单次决策读写 P95 < 50ms（requirement 非功能需求，SQLite 单机索引查询）
- 客户端超时：MCP 层 httpx TIMEOUT 常量；Web fetch 不设显式超时（本地环回）
- 并发写：同一 decision 并发 resolve 由终态校验保证只有一个 200，其余 409（先到先得）

## 15. 文件上传与下载

- 不适用：本契约全部为 JSON 端点，无文件传输

## 16. 接口清单

| # | 方法 | 路径 | 用途 | 权限 | 幂等 |
|---|------|------|------|------|------|
| 1 | POST | `/api/v1/decisions` | 登记决策 | 无（write 级） | 否 |
| 2 | GET | `/api/v1/decisions?status=&limit=` | 列表（默认 open） | 无（read） | 是 |
| 3 | GET | `/api/v1/decisions/{decision_id}` | 详情 | 无（read） | 是 |
| 4 | POST | `/api/v1/decisions/{decision_id}/resolve` | 落裁决 | 无（write 级） | 否（409 护栏） |
| 5 | POST | `/api/v1/decisions/{decision_id}/supersede` | 作废并指后继 | 无（write 级） | 否（409 护栏） |
| 6 | GET | `/api/v1/tasks/inbox` | 收件箱（decide 队列扩展 open 决策） | 无（read） | 是 |

- MCP 工具映射：taskhub_add_decision → #1；taskhub_resolve_decision → #4；taskhub_my_inbox → #6

## 17. 接口明细

### 17.1 POST /api/v1/decisions —— 登记决策

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|------|------|------|------|------|------|------|
| question | body | string | 是 | — | trim 后非空 | `大屏骨架依赖已取消，G1 换前置还是放弃？` |
| context | body | string | 否 | `""` | 无 | `前置 bpre0001 已 cancelled` |
| options | body | array | 否 | `[]` | 元素为 `{label, note}` 或纯字符串；label trim 非空 | `[{"label":"换前置","note":"保住大屏线"}]` |
| recommendation | body | string | 否 | `""` | 无（只是建议） | `建议换前置` |
| deadline | body | string(datetime) | 否 | null | ISO 8601；naive 补 UTC | `2026-11-14T12:00` |
| task_id | body | string | 否 | `""` | 弱关联，不校验存在性 | `89a90af6` |
| source | body | string | 否 | `""` | 无 | `agent:workbuddy` |

#### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 8 位十六进制，服务端生成 |
| status | string | 恒为 `open` |
| created_at | string | ISO 8601 UTC |
| 其余 12 字段 | — | 同 §16 清单（choice/rationale 等为初始空值） |

#### 错误码

| HTTP | 场景 | detail 示例 |
|------|------|-------------|
| 422 | question 缺失 | `question is required. 你要做的是：传「需要决策的问题」文本…` |
| 422 | options 非 list | `options 必须是数组 [{label, note}]。你要做的是：…` |
| 422 | options[i] 缺 label | `options[0] 缺 label。你要做的是：…` |
| 422 | deadline 非 ISO | `deadline 必须是 ISO 8601 时间串（如 2026-11-14T12:00）` |

#### 示例

```json
// 请求
{"question": "前端走 A 还是 B？",
 "options": [{"label": "A", "note": "快"}, "B"],
 "task_id": "89a90af6", "source": "agent:workbuddy"}
// 响应 200
{"id": "21e43365", "question": "前端走 A 还是 B？", "context": "",
 "options": [{"label": "A", "note": "快"}, {"label": "B", "note": ""}],
 "recommendation": "", "status": "open", "choice": "", "rationale": "",
 "decider": "", "deadline": null, "task_id": "89a90af6",
 "successor_id": "", "source": "agent:workbuddy",
 "created_at": "2026-11-04T05:33:02.123456+00:00", "resolved_at": null}
```

### 17.2 GET /api/v1/decisions —— 列表

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|------|------|------|------|------|------|------|
| status | query | string | 否 | `open` | open / resolved / superseded | `resolved` |
| limit | query | int | 否 | 50 | 1 ≤ limit ≤ 200 | `20` |

#### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| count | int | 本次返回条数（≤ limit） |
| status | string | 回显查询的 status |
| items | array[Decision] | created_at 降序（最新在前） |

#### 错误码

| HTTP | 场景 | detail 示例 |
|------|------|-------------|
| 422 | status 非法 | `status 必须是 open/resolved/superseded，收到 'nope'` |
| 422 | limit 越界 | FastAPI Query ge/le 校验（英文默认文案） |

#### 示例

```json
// 请求：GET /api/v1/decisions?status=resolved&limit=3
// 响应 200
{"count": 1, "status": "resolved",
 "items": [{"id": "21e43365", "question": "验证：…", "status": "resolved",
            "choice": "闭环", "rationale": "实弹验证通过", "decider": "user", …}]}
```

### 17.3 GET /api/v1/decisions/{decision_id} —— 详情

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|------|------|------|------|------|------|------|
| decision_id | path | string | 是 | — | 8 位十六进制 | `21e43365` |

#### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| （15 字段） | — | 单 Decision 对象，全字段见 §16 |

#### 错误码

| HTTP | 场景 | detail 示例 |
|------|------|-------------|
| 404 | id 不存在 | `decision 21e43365 不存在` |

#### 示例

```json
// 请求：GET /api/v1/decisions/21e43365
// 响应 200：同 §17.1 响应结构；已裁决则 status=resolved、choice/rationale/resolved_at 有值
```

### 17.4 POST /api/v1/decisions/{decision_id}/resolve —— 落裁决

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|------|------|------|------|------|------|------|
| decision_id | path | string | 是 | — | 存在且 status=open | `21e43365` |
| choice | body | string | 是 | — | trim 非空；可来自 options.label 或自由作答 | `闭环` |
| rationale | body | string | 否 | `""` | 留档审计，建议放用户原话 | `实弹验证通过（2026-11-04）` |
| decider | body | string | 否 | `user` | 自报标识 | `user` |

#### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | `resolved` |
| choice / rationale / decider / resolved_at | — | 本次写入；question/options 原文保留（审计只增不改） |

#### 错误码

| HTTP | 场景 | detail 示例 |
|------|------|-------------|
| 404 | id 不存在 | `decision … 不存在` |
| 409 | 终态再裁决 | `decision 已是终态 'resolved'（choice=闭环），不能重复裁决。你要做的是：…` |
| 422 | choice 缺失 | `choice is required. 你要做的是：从选项里挑一个或自由作答（可选：A、B）` |

#### 示例

```json
// 请求
{"choice": "闭环", "rationale": "实弹验证通过（2026-11-04）"}
// 响应 200
{"id": "21e43365", "status": "resolved", "choice": "闭环",
 "rationale": "实弹验证通过（2026-11-04）", "decider": "user",
 "resolved_at": "2026-11-04T05:35:10.001234+00:00",
 "question": "验证：…（原文保留）", "options": [（原文保留）], …}
```

### 17.5 POST /api/v1/decisions/{decision_id}/supersede —— 作废

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|------|------|------|------|------|------|------|
| decision_id | path | string | 是 | — | 存在且 status=open | `b2c31e02` |
| successor_id | body | string | 否 | `""` | 传了必须存在（422 否则） | `b2d99f04` |

#### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | `superseded` |
| successor_id | string | 指向替代决策（可空） |
| resolved_at | string | 作废时间 |

#### 错误码

| HTTP | 场景 | detail 示例 |
|------|------|-------------|
| 404 | id 不存在 | `decision … 不存在` |
| 409 | 终态再作废 | `decision 已是终态 '…'，不能作废` |
| 422 | successor_id 不存在 | `successor_id 'nope' 不存在。你要做的是：先创建替代决策，或省略 successor_id 直接作废` |

#### 示例

```json
// 请求
{"successor_id": "b2d99f04"}
// 响应 200
{"id": "b2c31e02", "status": "superseded", "successor_id": "b2d99f04",
 "resolved_at": "2026-11-04T05:36:00+00:00", …}
```

### 17.6 GET /api/v1/tasks/inbox —— 收件箱（decide 队列扩展）

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|------|------|------|------|------|------|------|
| （无） | — | — | — | — | — | — |

#### 响应字段

| 字段 | 类型 | 说明 |
|------|------|------|
| summary.decide / review / failed | int | 队列总数（语义不变） |
| summary.decide_tasks / decide_decisions | int | 新增：decide 队列分解 |
| decide[] 条目 | array | kind=task（依赖阻塞）或 kind=decision（open 决策） |
| decision 条目专属字段 | — | question/title(=question)/context/options/recommendation/task_id/deadline/age_days/action |
| 排序 | — | decide 整队 age_days 降序（最老的在前） |

#### 错误码

| HTTP | 场景 | detail 示例 |
|------|------|-------------|
| （无新增） | — | 既有 inbox 语义不变 |

#### 示例

```json
// 响应 200（节选）
{"summary": {"decide": 2, "decide_tasks": 1, "decide_decisions": 1,
             "review": 51, "failed": 18, "total": 71},
 "decide": [
   {"kind": "decision", "id": "21e43365", "title": "验证：…",
    "question": "验证：…", "options": [{"label": "闭环", "note": "…"}],
    "age_days": 0, "action": "POST /api/v1/decisions/{id}/resolve {\"choice\": ...}（或 supersede 作废）"},
   {"kind": "task", "id": "b010001", "title": "后置：统计卡",
    "blocked_by": ["bpre0001"], "action": "重开前置 / 换前置 / 砍后置…"}]}
```

## 18. 安全与脱敏

- 信任域：仅 127.0.0.1 监听，无外网暴露面；无凭证存储
- 脱敏：不适用（rationale/decision 内容由调用方自填，本服务不做内容过滤）
- 注入面：SQLite 经 SQLModel 参数化查询，无拼接

## 19. 兼容性与废弃策略

- inbox 旧消费方（Web / MCP taskhub_my_inbox）：decide 条目新增 `kind` 字段、summary 新增两个分解键——均为**纯追加**，旧字段语义不变，不构成破坏
- decision 条目 title=question：为兼容既有「所有条目须有 title」的断言而双写，后续版本不得移除其一
- POST /decisions 路由前缀与 /tasks 无冲突；main.py 注册位置在 task_diff 之后（无通配路由抢占问题）

## 20. 变更记录

| 版本 | 日期 | 变更 | 负责人 |
|------|------|------|--------|
| v1 | 2026-11-04 | 初版：5 个决策端点 + inbox decide 队列扩展 + MCP 两工具映射 | agent:workbuddy |

## 21. 附录

- 实现：`mio_taskhub/api/decisions.py`（REST）、`mio_taskhub/models.py`（Decision 表）、`mio_taskhub/api/inbox.py`（集成）
- 测试：`tests/test_decisions.py`（15 用例，含 inbox 集成与终态护栏）
- 实弹验证记录：2026-11-04 源码 hub（48620）六步链路——create → inbox 出现 → resolve → 队列消失 → 原文回溯 → 终态 409
