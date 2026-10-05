# API 设计 — 想法落地闭环 P2 结构化评审（task 286dd112）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 契约版本 | idea-landing-p2 |
| 状态 | approved |
| 最后更新 | 2026-09-27 20:45 |
| 负责人 | opencode（mio-taskhub） |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结·包 C） |
| 需求基线 | docs/taskhub/requirement-idea-landing-p2.md（FR-18~FR-25） |
| 关联任务 | 286dd112（branch task-286dd112） |
| 前序契约 | docs/taskhub/8442e38d/api.md（P1，idea-landing-p1）；本文只写 P2 新增/变更 |

## 文档信息与范围

本文是 P2「想法落地闭环·包 C 结构化评审」**新增/变更接口**的契约文档，适用于本机单用户部署的 mio-taskhub Web 客户端与 MCP 工具面。P0/P1 端点既有语义以前序契约为准，本文只描述 P2 引入的差异（新端点 + 既有端点的只增键回显）。

**明确不含**（防止被当成全量接口清单）：

- P0/P1 既有端点全量语义（见 docs/taskhub/2fe56e6a/api.md、8442e38d/api.md）；
- 评审内容**质量**评估——门控只验五段结构完整（NFR-3），不验风险是否真实、决策是否正确；
- prompt 的可视化编辑器前端（本契约只含配置端点，UI 走后续迭代）；
- P3 任务拓扑/复盘接口。

## 环境与基础地址

| 环境 | Base URL | 说明 |
|---|---|---|
| dev（本机常驻） | `http://127.0.0.1:48620/api/v1` | PyInstaller 打包 hub，SQLite 单库 |
| test（单测） | `http://testserver/api/v1` | FastAPI ASGITransport，进程内直调，无网络 |
| staging | 不适用 | 产品无 staging 部署形态 |
| prod | `http://127.0.0.1:48620/api/v1` | 与 dev 同构（本机单用户，无反向代理） |

无网关路径前缀剥离/重写：所有路径以 `/api/v1` 原样暴露，客户端按上表完整拼接。

## 认证与鉴权

| 项 | 约定 |
|---|---|
| 认证方式 | **无**（本机单用户产品，绑定 127.0.0.1） |
| 凭证来源 | 不适用：不签发 token、不设 cookie |
| 权限模型 | 全部调用方等权（Web UI、MCP、本地脚本同一信任级） |
| 401 与 403 | **本组接口不返回 401/403**；若未来挂反向代理引入认证，401=未认证、403=已认证但无权，业务码分别为 `unauthenticated` / `forbidden` |

## 通用请求头

| 头 | 值 | 必填 | 说明 |
|---|---|---|---|
| Content-Type | `application/json; charset=utf-8` | 带请求体时必填 | 缺失时 FastAPI 按 JSON 解析失败 → 422 |
| Accept | `application/json` | 否 | 响应恒为 JSON（UTF-8） |

## 通用响应结构

本组接口为 **REST 直出式**：成功响应即资源本身，**无统一 `{code,msg,data}` 外壳**（与 P0/P1 一致的显式例外）。逐端点响应字段见 §接口明细。

失败响应统一外壳：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | string | 是 | 人类可读错误说明（FastAPI `HTTPException` 惯例）；门控类错误以 `review gate: <缺项>` 开头 |

- `POST .../convert` 返回包装体 `{results: [...]}`（操作结果而非资源本身，属显式例外）；
- `GET /config/role-prompts` 返回配置快照对象（`prompts` 为 map 而非数组）；
- 既有读面（`GET /discussions*`、`GET /ideas/{id}`、`GET /tasks/{id}/discussions`）为对象/数组直出，P2 **只增键**（`mode`/`roles`/`review`/`prompt_snapshot`），不改旧键。

## 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `discussion_not_found` | 讨论不存在 | 路径 `discussion_id` 无记录 | 核对 id；刷新列表 |
| 404 | `idea_not_found` | 想法不存在 | 创建讨论传 `idea_id` 无记录 | 核对 id |
| 404 | `task_not_found` | 任务不存在 | 创建讨论传 `task_id` 无记录 | 核对 id |
| 422 | `mode_invalid` | 讨论模式非法 | `mode` 不在 `free/review`（`mode must be one of free/review`） | 改为枚举值 |
| 422 | `roles_invalid` | 角色列表形状非法 | `roles` 非数组或含空白字符串（`roles must be a list of non-empty strings`） | 改为 `["产品", …]` |
| 422 | `roles_required` | 评审会缺视角 | `mode=review` 且 `roles` 缺失/空（`roles is required when mode=review`） | 至少勾选一个评审视角 |
| 422 | `review_missing` | 评审关闭缺结构体 | `mode=review` 的 close 未带 `review`（含创建即关闭场景）（`review is required when mode=review …`） | 按 §接口明细 review 结构补齐 |
| 422 | `review_gate` | 五段门控拒绝 | 五段缺任一/条目结构违规（`review gate: risks：…；decisions：…`，detail 指明段名与条目下标） | 按 detail 提示补段后重试；**讨论保持 open** |
| 422 | `no_action_items` | 无可转换行动项 | convert 打在 free 讨论或未关闭评审上（`no action items to convert …`） | 先完成结构化评审关闭 |
| 422 | `unknown_item_ids` | 行动项 id 不存在 | `item_ids` 引用不存在的条目（`unknown action item ids: …`） | 按当前 review 回读核对 id |
| 422 | `config_invalid` | 配置入参非法 | `prompts` 空对象/空白文本；`words` 非数组或含空白串 | 按 detail 修正入参 |
| 422 | `validation_error` | 其余请求体校验失败 | FastAPI/pydantic 校验不通过 | 按 `detail` 修正参数 |
| 500 | `internal_error` | 服务端异常 | 非预期错误（本组接口设计上不应出现） | 重试一次；仍失败查 hub 日志 |

## 字段命名与类型规范

| 项 | 约定 | 反例 |
|---|---|---|
| 命名 | `snake_case` | `actionItems` ✗ → `action_items` ✓ |
| ID | 讨论/任务/想法为 8 位十六进制；行动项 `id` 为会话内唯一字符串（前端生成 `ai_<时间戳>_<序号>`） | 转任务后用任务 id 覆盖行动项 id ✗ |
| 布尔 | JSON `true/false`，无 `"0"/"1"` | `"created": "1"` ✗ |
| 数组 | JSON 数组；`roles` 空 `[]` 仅对 free 合法 | 逗号分隔字符串 ✗ |
| 枚举值 | 小写（`free`/`review`/`pending`） | 中文/驼峰 ✗ |

**字段缺失 vs 显式 null**（本组最易踩的三处）：

| 字段 | 键缺失 | 传 `null` | 传 `""` / `[]` |
|---|---|---|---|
| `mode`（创建） | 等价 `free` | 非法 → 422（非字符串） | 非法 → 422 |
| `roles`（创建） | free 合法（存 `[]`）；review → 422 | 同键缺失 | `[]` → review 422 |
| `review`（close） | free 忽略；review → 422 | free 忽略；review → 422 | 非 dict → 422 |
| `item_ids`（convert） | 缺省=全部未转条目 | 同缺省 | `[]` → 成功返回空 results（不建任务） |

`review.action_items[].task_id` 附加约定：未转为 `null`（显式），已转为任务 id 字符串；**不得**用键缺失表示未转（服务端归一化后恒带该键）。

## 枚举全集

| 枚举 | 取值全集 |
|---|---|
| `mode` | `free`, `review` |
| `roles`（前端种子=后端种子） | `产品`, `技术`, `商业`, `合规`, `红队` |
| `action_items[].status` | `pending`, `doing`, `done` |
| `action_items[].due` | 严格 `YYYY-MM-DD`（前端用 `<input type=date>`） |
| `risk_vocab_source` | `env`（`MIO_IDEA_RISK_TAGS`）, `db`（AppConfig 行）, `default`（常量） |
| `discussion.status`（既有） | `open`, `closed` |

## 幂等性与重试

| 接口 | 幂等 | 幂等键/行为 | 重复提交返回 |
|---|---|---|---|
| POST /discussions | 否 | 每次创建新讨论（自然非幂等） | 新资源（无重复保护语义） |
| POST .../close | 部分 | 已 closed 讨论再关：free 直接覆盖 conclusion 并保持 closed；review 重新过门控并覆盖 `review` | 200 + 最新资源 |
| POST .../convert | **是** | 行动项 `task_id` 非空即跳过（返回原 id、`created:false`），不重复建任务 | 200 + 全部 `created:false` |
| GET /config/role-prompts | 是 | 纯读 | 200 + 当前配置 |
| PUT /config/role-prompts | 部分 | 文本相同重放 → 不 bump `version`（变化才 +1） | 200 + 当前全量 |
| PUT /config/risk-vocab | 是 | 同列表重放 → 覆盖写同值 | 200 + 当前词表 |

**可安全重试**：convert、两个 GET、两个 PUT。**绝不可盲目重试**：POST /discussions（会重复开会）。**建议退避**：`500` 指数退避（1s/2s/4s），`4xx` 不重试。

## 限流与配额

本机单用户无网关限流，接口不设配额。前端约束：评审关闭表单为一次性提交（失败保留表单内容供修正）；「一键转任务」按钮转换中禁用防连点（服务端幂等兜底）。

## 超时与并发

| 项 | 值 |
|---|---|
| 服务端处理 | 纯 SQLite 本地读写，典型 <50ms；无跨服务调用 |
| convert 事务 | 单事务整体回滚：任一行动项建任务失败，已 flush 的任务全部撤销（无半截数据） |
| 并发模型 | SQLite WAL；close/convert 读-改-写在单请求事务内完成；多端并发 close 以最后提交为准（last-write-wins，单用户可接受） |
| JSON 列变更检测 | `review` 整体重新赋值触发 SQLAlchemy 变更（无 Mutable 跟踪，调用方不可见） |
| prompt 缓存 | 进程内缓存；PUT 后失效，下一次读取重新加载（热更新对新会话生效、对进行中会话无感——快照隔离） |

## 分页

**本组接口不涉及分页**：讨论列表沿用既有 `ref_type/ref_id` 过滤（单想法/单任务讨论量个位数）；配置端点为单对象；行动项内嵌于单讨论。若后续讨论量超限，沿用既有 `?limit&offset` 风格，不在本契约内。

## 时间 / 数值 / 空值约定

| 项 | 约定 |
|---|---|
| 时间 | ISO8601 UTC；`prompt_snapshot.captured_at` 带 `T` 与微秒（`2026-09-27T11:30:00.123456`，naive UTC，同库内惯例）；`Discussion.started_at/ended_at` 同 P0 |
| 日期 | `action_items[].due` 为 `YYYY-MM-DD` 纯日期字符串（转 `Task.due_at` 时按 UTC 零点解释） |
| 数值 | `prompts[角色].version` 为整数，从 1 起、有变化才 +1 |
| 空值 | 字符串空=`""`；JSON 空=`null`（`review`/`prompt_snapshot` 未设为 `null`）；空数组 `[]` 表示「显式无」（free 讨论的 `roles`） |
| 缺失字段 | 读面四键恒出现（旧数据回显默认值），**不是**键缺失 |

## 文件上传与下载

不适用：本组接口无文件上传/下载（评审结果为结构化 JSON，随讨论资源存储于 SQLite）。

## 安全与脱敏

| 项 | 说明 |
|---|---|
| 网络暴露 | 仅绑定 127.0.0.1，不出网 |
| 敏感数据 | 评审内容（风险/分歧/行动项）为用户输入的自由文本，无凭证/PII 托管；prompt 配置同理 |
| 日志 | 门控 422 的 `detail` 含段名与条目下标，不记录评审正文全文 |
| 注入 | 全部参数经 pydantic 校验 + SQLAlchemy 绑定参数，无字符串拼接 SQL |

## 版本策略

| 项 | 约定 |
|---|---|
| 路径版本 | `/api/v1`；破坏性变更（改字段类型/删字段/改语义）→ 新增 `/api/v2` 并行 |
| 字段演进 | `mode/roles/review/prompt_snapshot` 为可空新列（NFR-1：旧数据零迁移）；读面只增键，旧客户端零破坏 |
| 契约版本 | `idea-landing-p2`；语义变更时递增并更新「变更记录」 |

## 兼容性与废弃策略（FR-25）

- `mode` 不传 ≡ `free`：P0/P1 既有调用方（API/MCP/前端/既有测试）**零行为变化**——free 创建/关闭分支代码路径未动，全量 pytest 回归覆盖（基线 905 例只增不减，P2 新增 10 例）；
- 旧讨论数据四列为可空（`mode` 读取端 `d.mode or "free"` 兜底），创建时间早于 P2 的记录读取回显 `mode=free`、`roles=[]`、`review=null`、`prompt_snapshot=null`；
- 读面只增键：`GET /discussions*`、`GET /ideas/{id}` 的 `discussions[]`、`GET /tasks/{id}/discussions` 均新增四键，旧前端忽略新键不受影响；
- `cockpit.high_risk` 语义不变（词表迁 DB 不改判定：`tags ∩ 词表` 非空即 true，优先级 env > DB > 默认）；
- MCP 工具为**新增可选参数**（`mode`/`roles`/`review`），不传即旧行为；工具 schema 变更需重启 opencode MCP 进程后生效；
- 废弃流程：先在文档标 deprecated 并保留 ≥1 个发布周期，服务端对旧字段继续读写，再随大版本移除。

## 接口清单

| 方法 | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|---|---|---|---|---|---|---|
| POST | `/api/v1/discussions` | 创建讨论（支持 `mode`/`roles`，FR-18） | 无鉴权 | 否 | 无 | 新讨论 open（或带结论即 closed） |
| GET | `/api/v1/discussions` | 讨论列表（增键回显，FR-18） | 无鉴权 | 是 | 无 | 只读 |
| GET | `/api/v1/discussions/{discussion_id}` | 讨论单条（增键回显，FR-18） | 无鉴权 | 是 | 无 | 只读 |
| POST | `/api/v1/discussions/{discussion_id}/close` | 关闭讨论；review 五段门控（FR-21） | 无鉴权 | 部分 | 无 | `status→closed`，review 落库 |
| POST | `/api/v1/discussions/{discussion_id}/convert` | 行动项幂等转任务（FR-22） | 无鉴权 | 是 | 无 | 建任务 + `task_id` 回写 |
| GET | `/api/v1/config/role-prompts` | 读角色 prompt + 高风险词表（FR-19/20） | 无鉴权 | 是 | 无 | 只读 |
| PUT | `/api/v1/config/role-prompts` | 批量改 prompt（version+1、缓存失效，FR-19） | 无鉴权 | 部分 | 无 | 配置更新，新会话生效 |
| PUT | `/api/v1/config/risk-vocab` | 改高风险词表（写 DB，FR-20） | 无鉴权 | 是 | 无 | 配置更新，读取优先级 env>DB>默认 |

## 接口明细

### POST /api/v1/discussions

创建讨论并回显（含 P2 新键）。`mode=review` 必填 `roles`；带 `conclusions` 即「创建即关闭」，review 模式下同走五段门控防绕过。FR-18、FR-19。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `topic` | body | string | 是 | 主题文本（原契约） |
| `idea_id` / `task_id` | body | string | 二选一 | 须存在否则 404；两者皆空 → 422 |
| `mode` | body | string | 否 | `free`/`review`，缺省 `free`；非法值 → 422 |
| `roles` | body | array[string] | review 必填 | 非空非空白字符串；review 缺/空 → 422 |
| `conclusions` | body | string | 否 | 非空即置 `closed`；review 时必须同时带 `review` 否则 422 |
| `review` | body | object | 创建即关闭时必填 | 五段结构（见 close 明细） |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | string | 是 | `free`/`review` |
| `roles` | array[string] | 是 | 已存角色（free 为 `[]`） |
| `review` | object\|null | 是 | 创建时为 `null`（关闭后填充） |
| `prompt_snapshot` | object\|null | 是 | review 创建时捕获 `{roles, prompts:{角色:{version,prompt}}, captured_at}`；free 为 `null` |
| 既有键 | — | 是 | `id/topic/agent/status/summary/conclusions/stage/started_at/ended_at/messages` 原样 |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| idea_id/task_id 均空 | 422 | `idea_id or task_id is required` |
| 想法/任务不存在 | 404 | `idea not found` / `task not found` |
| mode 非法 | 422 | `mode must be one of free/review` |
| roles 形状非法 | 422 | `roles must be a list of non-empty strings` |
| review 缺 roles | 422 | `roles is required when mode=review` |
| 创建即关闭缺 review | 422 | `review is required when mode=review (missing review payload)` |

#### 示例

```http
POST /api/v1/discussions
{"topic": "支付方案评审", "idea_id": "27060d9b", "mode": "review", "roles": ["产品", "红队"]}
```
```json
// 200
{"id": "3a9c1f2e", "topic": "支付方案评审", "status": "open", "mode": "review",
 "roles": ["产品", "红队"], "review": null,
 "prompt_snapshot": {"roles": ["产品","红队"],
   "prompts": {"产品": {"version": 1, "prompt": "以产品视角评审：…"},
               "红队": {"version": 1, "prompt": "以红队视角评审：…"}},
   "captured_at": "2026-09-27T12:00:00.000000"},
 "messages": []}
```
```json
// 422（review 未带 roles）
{"detail": "roles is required when mode=review"}
```

### GET /api/v1/discussions

讨论列表（`ref_type=idea|task` 过滤可选）。P2 只增键回显。FR-18。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `ref_type` | query | string | 否 | `idea` 或 `task`；缺省返回全部 |
| `ref_id` | query | string | 否 | 配合 `ref_type` 过滤 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `count` | integer | 是 | 命中条数 |
| `discussions[]` | array | 是 | 每项同单条结构（含 `mode`/`roles`/`review`/`prompt_snapshot` 四新键 + 既有键 + `messages`） |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| ref_type 非法值 | 200 | 不报错：视同缺省返回全部（过滤参数非契约强校验项） |

#### 示例

```http
GET /api/v1/discussions?ref_type=idea&ref_id=27060d9b
```
```json
// 200（旧讨论回显默认值）
{"count": 2, "discussions": [
  {"id": "3a9c1f2e", "mode": "review", "roles": ["红队"], "review": null, "prompt_snapshot": {...}, "...": "..."},
  {"id": "9b11ee04", "mode": "free", "roles": [], "review": null, "prompt_snapshot": null, "...": "..."}]}
```

### GET /api/v1/discussions/{discussion_id}

讨论单条（含消息）。评审关闭后可从此回读 `review`（前端行动项数据源）。FR-18、FR-22。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `discussion_id` | path | string | 是 | 须存在否则 404 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `mode` | string | 是 | `free`/`review` |
| `roles` | array[string] | 是 | 创建时选定视角 |
| `review` | object\|null | 是 | 关闭后为五段结构（归一化后 `task_id` 键恒在） |
| `prompt_snapshot` | object\|null | 是 | 创建时快照（进行中会话不受热更新影响） |
| `messages[]` | array | 是 | 消息列表（原契约） |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 讨论不存在 | 404 | `discussion not found` |

#### 示例

```http
GET /api/v1/discussions/3a9c1f2e
```
```json
// 200（评审已关闭且两条行动项均已转任务）
{"id": "3a9c1f2e", "mode": "review", "status": "closed",
 "review": {"risks": ["权限模型未定"], "divergences": "无分歧，已达成一致",
   "suggestions": "先做只读视图", "decisions": ["方案A", "方案B"],
   "action_items": [
     {"id": "ai_x1", "owner": "张三", "action": "出权限矩阵", "due": "2026-10-01",
      "status": "pending", "task_id": "c0ffee01"}]},
 "...": "..."}
```

### POST /api/v1/discussions/{discussion_id}/close

关闭讨论。`mode=free` 行为与 P0/P1 完全一致；`mode=review` 必须带 `review` 且过五段门控（缺任一段 422、讨论保持 open）。FR-21、FR-18。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `discussion_id` | path | string | 是 | 须存在否则 404 |
| `conclusions` | body | string | 否 | 结论文本（原契约） |
| `summary` | body | string | 否 | 摘要（缺省保留原值） |
| `review` | body | object | review 必填 | 见下表五段；free 传入则忽略 |
| `review.risks` | body.review | array[string] | 是 | ≥1 非空条 |
| `review.divergences` | body.review | string | 是 | 非空（「无分歧」须写依据） |
| `review.suggestions` | body.review | string | 是 | 非空 |
| `review.decisions` | body.review | array[string] | 是 | ≥2 非空条 |
| `review.action_items[]` | body.review | array[object] | 是 | ≥1 条；每条含 `id`（会话内唯一）/`owner`/`action`/`due`(`YYYY-MM-DD`)/`status`(`pending\|doing\|done`)/`task_id`(string\|null) |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `status` | string | 是 | 恒 `closed` |
| `review` | object\|null | 是 | 归一化后的五段（free 为 `null`） |
| `ended_at` | string | 是 | 关闭时间（ISO8601 UTC） |
| 既有键 | — | 是 | `conclusions`/`summary`/`messages` 等原样 |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 讨论不存在 | 404 | `discussion not found` |
| review 模式未带 review | 422 | `review is required when mode=review (missing review payload)` |
| 五段任一缺失/不足 | 422 | `review gate: risks：风险清单至少 1 条` 等（中文段名，可多段并列） |
| 行动项结构违规 | 422 | `review gate: action_items[0]：due 必须为 YYYY-MM-DD` / `…缺少 owner` / `…id 重复（ai_x1）` |

#### 示例

```http
POST /api/v1/discussions/3a9c1f2e/close
{"conclusions": "评审完成", "summary": "一轮",
 "review": {"risks": ["权限模型未定"], "divergences": "无分歧，已达成一致",
   "suggestions": "先做只读视图", "decisions": ["方案A：先只读", "方案B：直接上写"],
   "action_items": [{"id": "ai_x1", "owner": "张三", "action": "出权限矩阵",
     "due": "2026-10-01", "status": "pending", "task_id": null}]}}
```
```json
// 200
{"id": "3a9c1f2e", "status": "closed", "review": {"risks": ["权限模型未定"], "...": "..."},
 "conclusions": "评审完成", "ended_at": "2026-09-27T12:10:00.000000"}
```
```json
// 422（决策选项不足 2 个；讨论保持 open）
{"detail": "review gate: decisions：决策选项至少 2 个"}
```

### POST /api/v1/discussions/{discussion_id}/convert

行动项幂等转任务：缺省转换全部未转条目；`task_id` 非空条目跳过；单事务整体回滚。FR-22。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `discussion_id` | path | string | 是 | 须存在否则 404；无 `action_items`（free/未关闭）→ 422 |
| `item_ids` | body | array[string] | 否 | 缺省=全部未转条目；未知 id → 422；`[]` → 成功空 results |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `results[]` | array | 是 | 每项一个行动项的转换结果 |
| `results[].item_id` | string | 是 | 行动项 id |
| `results[].task_id` | string | 是 | 对应任务 id（新建或已存在） |
| `results[].created` | boolean | 是 | `true`=本次新建；`false`=幂等跳过返回原任务 |

任务创建字段约定：`title=action`、`acceptance_criteria=action`、`description` 含 owner/due/来源讨论、`due_at=due`（UTC 零点）、`idea_id=讨论.idea_id`、`stage=ready`、`state=queued`。

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 讨论不存在 | 404 | `discussion not found` |
| 无行动项可转 | 422 | `no action items to convert (close a mode=review discussion first)` |
| item_ids 非字符串数组 | 422 | `item_ids must be a list of strings` |
| 未知行动项 id | 422 | `unknown action item ids: nope` |
| 事务内任一失败 | 500 | `internal_error`（整体回滚，无残留任务） |

#### 示例

```http
POST /api/v1/discussions/3a9c1f2e/convert
{"item_ids": ["ai_x1"]}
```
```json
// 200
{"results": [{"item_id": "ai_x1", "task_id": "c0ffee01", "created": true}]}
```
```http
POST /api/v1/discussions/3a9c1f2e/convert
{}
```
```json
// 200（重复调用：全部幂等）
{"results": [{"item_id": "ai_x1", "task_id": "c0ffee01", "created": false}]}
```

### GET /api/v1/config/role-prompts

读角色 prompt 全量、可用角色列表与高风险词表（含来源）。FR-19、FR-20。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| — | — | — | — | 无参数 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompts` | object | 是 | `{角色: {prompt, version, updated_at}}`；种子 5 角色 |
| `roles` | array[string] | 是 | 当前全部角色名（含自定义新增） |
| `risk_vocab` | array[string] | 是 | 生效词表（按 env>DB>默认解析后的结果） |
| `risk_vocab_source` | string | 是 | `env`/`db`/`default` |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| — | — | 本端点无错误路径（纯读） |

#### 示例

```http
GET /api/v1/config/role-prompts
```
```json
// 200
{"prompts": {"红队": {"prompt": "以红队视角评审：…", "version": 2,
   "updated_at": "2026-09-27T12:05:00.000000"}, "...": "..."},
 "roles": ["产品", "技术", "商业", "合规", "红队"],
 "risk_vocab": ["高风险", "合规", "用户数据", "花钱"],
 "risk_vocab_source": "default"}
```

### PUT /api/v1/config/role-prompts

批量更新角色 prompt：有变化才 `version+1`，写库后缓存失效（热更新）。FR-19。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `prompts` | body | object[string] | 是 | 非空对象；值须为非空白字符串，否则 422 |
| `prompts.<role>` | body | string | 是 | 不存在的角色名 → 新建（version=1）；已有角色文本变化 → version+1 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompts` | object | 是 | 更新后的全量（同 GET） |
| `roles` | array[string] | 是 | 更新后角色列表 |
| `risk_vocab` / `risk_vocab_source` | array / string | 是 | 同 GET（回显） |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| prompts 空/非对象 | 422 | `prompts must be a non-empty object {role: text}` |
| 某角色文本空白 | 422 | `prompt for role '产品' must be a non-empty string` |

#### 示例

```http
PUT /api/v1/config/role-prompts
{"prompts": {"红队": "新红队提示词 v2"}}
```
```json
// 200（version 1→2；此后新评审快照即为新版）
{"prompts": {"红队": {"prompt": "新红队提示词 v2", "version": 2, "updated_at": "..."}},
 "roles": ["产品", "技术", "商业", "合规", "红队"],
 "risk_vocab": ["高风险", "合规", "用户数据", "花钱"], "risk_vocab_source": "default"}
```

### PUT /api/v1/config/risk-vocab

写入高风险词表（DB 层）。读取优先级 env `MIO_IDEA_RISK_TAGS` > DB > 默认常量；未配置 DB 行时读取回落默认（来源报 `default`，不预埋行）。FR-20。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `words` | body | array[string] | 是 | 非空字符串列表（空白串 → 422）；元素将 strip |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `words` | array[string] | 是 | 归一化后写入的词表 |
| `source` | string | 是 | 写入后的**读取来源**回显（env 覆盖时为 `env`，否则 `db`） |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| words 非数组/含空白串 | 422 | `words must be a list of non-empty strings` |

#### 示例

```http
PUT /api/v1/config/risk-vocab
{"words": ["A类风险", "B类风险"]}
```
```json
// 200
{"words": ["A类风险", "B类风险"], "source": "db"}
```

## 附录

### MCP 工具变更（FR-23）

| 工具 | 变更 | 兼容性 |
|---|---|---|
| `taskhub_open_discussion` | 新增可选 `mode`（缺省 `free`）、`roles`（可选数组） | 不传即旧行为；`mode=review` 时 roles 必填（服务端 422 原样回传） |
| `taskhub_close_discussion` | 新增可选 `review`（对象） | 不传对 free 讨论无影响；review 讨论不传 → 422 `detail` 原样回传 |
| 其余工具 | 无变更 | — |

> 工具参数 schema 变更需**重启 opencode MCP 进程**后生效（进程内 schema 缓存）。

### 前端行为契约（FR-24）

| 行为 | 约定 |
|---|---|
| 模板下拉 | 「讨论会（自由）/ 评审会（结构化）」，缺省值=推荐值 |
| 阶段预选 | `formed` 或已有非空 `goal` → 推荐 `review`；否则 `free`（推荐可被下拉覆盖） |
| 红队默认 | `cockpit.high_risk=true` → 角色默认勾选「红队」，可取消；评审会 0 角色时禁开并提示 |
| 关闭表单 | free 沿用原 prompt 行为；review 五段表单（结论+五段+行动项行编辑），422 `detail` 直接展示 |
| 行动项 | 状态 chip；未转 → 「一键转任务」（单条 convert，转换中禁用）；已转 → 「查看任务」（跳任务详情） |

### FR 追溯

| FR | 端点/章节 |
|---|---|
| FR-18 | POST /discussions 明细、GET 列表/单条明细、§兼容性（只增键） |
| FR-19 | GET/PUT /config/role-prompts 明细、§超时与并发（缓存/快照隔离）、POST /discussions（`prompt_snapshot`） |
| FR-20 | PUT /config/risk-vocab 明细、§枚举全集（`risk_vocab_source`）、§兼容性（high_risk 不变） |
| FR-21 | POST .../close 明细（五段结构、门控 422、创建即关闭防绕过） |
| FR-22 | POST .../convert 明细（幂等、事务、任务字段约定） |
| FR-23 | §附录·MCP 工具变更 |
| FR-24 | §附录·前端行为契约 |
| FR-25 | §兼容性与废弃策略（free 零变化 + 全量回归） |

## 变更记录

| 日期 | 契约版本 | 变更内容 | 兼容/破坏 | 影响方 |
|---|---|---|---|---|
| 2026-09-27 | idea-landing-p2 v1.0 | P2 初版：讨论双模式、五段门控、行动项 convert、prompt/词表配置端点、MCP 透传、前端行为契约 | 兼容（新增端点 + 可空新列 + 读面只增键；`mode` 不传 ≡ free，P0/P1 零行为变化） | Web 客户端讨论/评审区；MCP 调用方（open/close 工具） |
