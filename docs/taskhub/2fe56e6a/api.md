# API 设计 — 想法落地闭环 P0（task 2fe56e6a）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 契约版本 | idea-landing-p0 |
| 状态 | approved |
| 最后更新 | 2026-09-27 15:13 |
| 负责人 | opencode（mio-taskhub） |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结） |
| 需求基线 | docs/taskhub/requirement-idea-landing-p0.md（FR-1~FR-10） |
| 关联任务 | 2fe56e6a（branch task-2fe56e6a） |

## 文档信息与范围

本文是 P0「想法落地闭环」**新增/变更接口**的契约文档，适用于本机单用户部署的 mio-taskhub Web/MCP 客户端。

**明确不含**（防止被当成全量接口清单）：

- 任务/看板/文档链/评审等既有接口（见运行中服务的 OpenAPI：`GET /openapi.json`）；
- 包 B 假设导入/实时分数接口（P1）：`PATCH /ideas/{id}/assumptions/{hid}`、假设列表拉取；
- 包 C 结构化评审接口（P2）：`mode/roles`、行动项 `.../convert`；
- 列表分页、鉴权、限流等横向能力（本产品为本机单用户，见对应章节的不适用说明）。

## 环境与基础地址

| 环境 | Base URL | 说明 |
|---|---|---|
| dev（本机常驻） | `http://127.0.0.1:48620/api/v1` | PyInstaller 打包 hub，SQLite 单库 |
| test（单测） | `http://testserver/api/v1` | FastAPI `TestClient`，进程内直调，无网络 |
| staging | 不适用 | 产品无 staging 部署形态 |
| prod | `http://127.0.0.1:48620/api/v1` | 与 dev 同构（本机单用户，无反向代理） |

无网关路径前缀剥离/重写：所有路径以 `/api/v1` 原样暴露，客户端按上表完整拼接。

## 认证与鉴权

| 项 | 约定 |
|---|---|
| 认证方式 | **无**（本机单用户产品，绑定 127.0.0.1） |
| 凭证来源 | 不适用：不签发 token、不设 cookie |
| 权限模型 | 全部调用方等权（Web UI、MCP、本地脚本同一信任级） |
| 401 与 403 | **本组接口不返回 401/403**（无认证态可言、无越权概念）；若未来挂反向代理引入认证，401=未认证、403=已认证但无权，业务码分别为 `unauthenticated` / `forbidden` |

## 通用请求头

| 头 | 值 | 必填 | 说明 |
|---|---|---|---|
| Content-Type | `application/json; charset=utf-8` | 带请求体时必填 | 缺失时 FastAPI 按 JSON 解析失败 → 422 |
| Accept | `application/json` | 否 | 响应恒为 JSON（UTF-8） |

## 通用响应结构

本组接口为 **REST 直出式**：成功响应即资源本身，**无统一 `{code,msg,data}` 外壳**。此为对「通用外壳」规则的显式例外，逐端点响应字段见 §接口明细。

失败响应统一外壳：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | string | 是 | 人类可读错误说明（FastAPI `HTTPException` 惯例） |

- `data: null` 与 `data: {}` 的区分：本组接口不使用包裹体，故不存在该语义；资源级空集合见各端点「响应字段」表。
- **裸返回例外**：`GET /ideas/{id}/cockpit` 返回顶层对象（`sections` 为 map 而非数组）；`GET /ideas`（列表，非本文档范围）返回 `{count, ideas: [...]}`。

## 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `idea_not_found` | 想法不存在 | 路径 `idea_id` 在 ideas 表无记录 | 核对 id；列表接口重新获取 |
| 409 | `next_action_not_dismissible` | 下一步动作不可忽略 | `rule_id` 不在 5 条规则枚举内（`unknown rule: x`），或该规则当前未命中（`rule x not matched, nothing to dismiss`，条件已满足/已过期） | 重新 GET cockpit 取当前 `next_action.rule_id` 再 dismiss |
| 422 | `rule_id_required` | 缺必填参数 | 请求体 `rule_id` 缺失或为空串 | 补 `rule_id` |
| 422 | `json_field_not_array` | JSON 字段类型违反约束 | `assumptions`/`risks`/`tags` 传入字符串/对象/数字而非数组 | 改为数组（如 `["高风险"]`），清空用 `null` |
| 422 | `validation_error` | 其余请求体校验失败 | FastAPI/pydantic 模型校验不通过 | 按 `detail` 提示修正参数 |
| 500 | `internal_error` | 服务端异常 | 非预期错误（本组接口设计上不应出现：cockpit 单区失败降级为 `sections[x].status=degraded` 不 500） | 重试一次；仍失败查 hub 日志 |

## 字段命名与类型规范

| 项 | 约定 | 反例 |
|---|---|---|
| 命名 | `snake_case` | `successMetric` ✗ → `success_metric` ✓ |
| ID | 8 位十六进制字符串（`27060d9b`） | 数字自增 id ✗ |
| 布尔 | JSON `true/false`，无 `"0"/"1"` | `"true"` ✗ |
| 数组 | JSON 数组；空数组 `[]` 表示「显式清空」 | 逗号分隔字符串 ✗ |
| 枚举值 | 小写蛇形（`missing_goal`） | 中文/驼峰 ✗ |

**字段缺失 vs 显式 null**（PATCH 语义，调用方最易踩）：

| 传入 | 字符串字段（goal 等） | JSON 字段（assumptions/risks/tags） |
|---|---|---|
| 键缺失 | 不修改 | 不修改 |
| `""` | 清空为默认 `""` | 非法 → 422（须传数组或 null） |
| `null` | 视同不修改（模型默认 `""`） | 清空（存储为 `NULL`，读出 `null`） |
| `[]` | 非法 → 422 | 清空（存储 `[]`，读出 `[]`） |

## 枚举全集

| 枚举 | 取值全集 |
|---|---|
| `sections` 键（7 区） | `goal`, `hypotheses`, `mvp`, `tasks`, `risks`, `approvals`, `retrospective` |
| `sections[x].status` | `ok`, `degraded` |
| `next_action.rule_id`（5 级序） | `missing_goal`, `doc_not_approved`, `blocked_task`, `unverified_high_risk_assumption`, `review_missing_action_items` |
| 高风险词表（默认，env `MIO_IDEA_RISK_TAGS` 覆盖） | `高风险`, `合规`, `用户数据`, `花钱` |
| `risks[].level` | `low`, `medium`, `high` |
| `idea.status`（既有） | `new`, `fermenting`, `formed`, `broken_down`, `archived`, `cancelled` |

优先级序可用 env `MIO_NEXT_ACTION_ORDER`（逗号分隔 rule_id）覆盖，服务端按序取首条命中。

## 幂等性与重试

| 接口 | 幂等 | 幂等键/行为 | 重复提交返回 |
|---|---|---|---|
| POST /ideas | 否 | 无幂等键，每次创建新资源 | 每次 201 + 新 id（标题查重属既有模板流程，不在本组） |
| PATCH /ideas/{id} | 是 | 同 payload 重放结果一致（last-write-wins） | 200 + 最新资源；每次变更仍追加 IdeaChange |
| GET .../cockpit | 是 | 纯读，无副作用 | 200 + 当前聚合 |
| POST .../next-action/dismiss | 是 | (idea_id, user, rule_id) 唯一，重放刷新 `dismissed_at`/snapshot | 200（规则仍命中时）；条件已不满足则 409 |

**可安全重试**：GET、PATCH、dismiss（200/409 均为终态判定）。**不可盲目重试**：POST /ideas（会重复创建，先按标题查回）。建议退避：`500` 指数退避（1s/2s/4s），`4xx` 不重试。

## 限流与配额

本机单用户无网关限流，接口不设配额。前端约束：cockpit 建议进入详情页时拉取一次 + 手动刷新，避免轮询（总预算 5s，重复并发会浪费 SQLite 连接与 Mio 调用）。

## 超时与并发

| 项 | 值 |
|---|---|
| cockpit 单区超时 | Mio 假设区 3s；其余区 1s |
| cockpit 总预算 | 5s（超时返回已就绪部分，未就绪区 `degraded`） |
| dismiss / PATCH | 本地 SQLite 同步，典型 <50ms |
| 并发模型 | SQLite WAL；PATCH 并发为 last-write-wins，无乐观锁（单用户可接受，NFR-1） |

## 分页

**本组接口不涉及分页**：无列表端点（`GET /ideas` 列表为既有接口且不在本文档范围）、cockpit 为单资源聚合。若后续新增列表，沿用既有 `?limit&offset` 风格（见 OpenAPI），不在本契约内。

## 时间 / 数值 / 空值约定

| 项 | 约定 |
|---|---|
| 时间 | naive UTC ISO8601（`2026-09-27T05:31:12`，无 `Z`/偏移）；前端 `parseUtc` 补 `Z` 解析后按本地显示 |
| 数值 | 评分/计数为整数；不使用科学计数法 |
| 空值 | 字符串空=`""`；JSON 空=`null`（与 `[]` 区别见「字段命名与类型规范」） |
| 缺失字段 | PATCH 中键缺失=不修改 |

## 文件上传与下载

不适用：本组接口无文件上传/下载（Idea 为纯文本/JSON 结构化字段；附件能力属任务域既有接口）。

## 安全与脱敏

| 项 | 说明 |
|---|---|
| 网络暴露 | 仅绑定 127.0.0.1，不出网 |
| 敏感数据 | P0 字段无凭证/PII；`tags` 高风险词仅用于标记，不触发外部调用 |
| 日志 | 错误 `detail` 可含字段名，不记录字段值全文（防长文本刷日志） |
| 注入 | 全部参数经 pydantic 校验 + SQLAlchemy 绑定参数，无字符串拼接 SQL |

## 版本策略

| 项 | 约定 |
|---|---|
| 路径版本 | `/api/v1`；破坏性变更（改字段类型/删字段/改语义）→ 新增 `/api/v2` 并行 |
| 字段演进 | **字段模型一次定型**（FR-1）：只增不删；新增字段必可空、带默认值 → 旧客户端零破坏 |
| 契约版本 | 文档头「契约版本」随语义变更递增 |

## 兼容性与废弃策略（FR-10）

- 旧 idea 数据（新字段 NULL/`""`）在 POST 读取、PATCH、cockpit 全路径正常返回，前端灰显空字段；
- `mode=free` 讨论、既有 idea 编辑与评审流程行为不变（全量 pytest 883 例回归覆盖）；
- 废弃流程：先在文档标 deprecated 并保留 ≥1 个发布周期，服务端对旧字段继续读写，再随大版本移除。

## 接口清单

| 方法 | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|---|---|---|---|---|---|---|
| POST | `/api/v1/ideas` | 创建想法（含 8 结构化字段） | 无鉴权 | 否 | 无 | 新想法 → `new` |
| PATCH | `/api/v1/ideas/{idea_id}` | 更新想法字段（进 IdeaChange diff） | 无鉴权 | 是 | 无 | 状态不变 |
| GET | `/api/v1/ideas/{idea_id}/cockpit` | 驾驶舱聚合（sections+next_action+high_risk） | 无鉴权 | 是 | 无（建议手动刷新） | 只读 |
| POST | `/api/v1/ideas/{idea_id}/next-action/dismiss` | 服务端忽略下一步动作 | 无鉴权 | 是 | 无 | 7 天过期/条件变化复活 |

## 接口明细

### POST /api/v1/ideas

创建想法。FR-1（8 字段可空）、FR-2（变更进 diff，创建记 initial 版本）。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `title` | body | string | 是 | 非空（既有约束） |
| `description` | body | string | 否 | — |
| `goal` | body | string | 否 | 默认 `""` |
| `success_metric` | body | string | 否 | 默认 `""` |
| `constraints` | body | string | 否 | 默认 `""` |
| `out_of_scope` | body | string | 否 | 默认 `""` |
| `assumptions` | body | array\|null | 否 | 须为数组或 null，否则 422 |
| `risks` | body | array\|null | 否 | 元素建议 `{text,level,mitigation}` |
| `mvp_scope` | body | string | 否 | 默认 `""` |
| `tags` | body | array\|null | 否 | 须为数组或 null，否则 422 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 8 位十六进制 |
| `title` | string | 是 | 回显 |
| `goal` … `tags` | 见 §字段模型 | 是 | 8 字段恒返回（空为 `""`/`null`） |
| `created_at` | string | 是 | naive UTC |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| JSON 字段非数组 | 422 | `assumptions/risks/tags must be a JSON array`（字段名随实际实现） |
| 校验失败 | 422 | pydantic 校验信息 |

#### 示例

```http
POST /api/v1/ideas
{"title": "想法落地闭环", "goal": "给【Agent 用户】解决【推进无抓手】", "tags": ["高风险"]}
```
```json
// 201
{"id": "27060d9b", "title": "想法落地闭环", "goal": "给【Agent 用户】解决【推进无抓手】",
 "success_metric": "", "constraints": "", "out_of_scope": "", "assumptions": null,
 "risks": null, "mvp_scope": "", "tags": ["高风险"], "created_at": "2026-09-27T05:01:19"}
```

### PATCH /api/v1/ideas/{idea_id}

更新任意字段子集；每次成功变更生成 `IdeaChange`（diff 键=字段名，`assumptions` 单条编辑为 `assumptions[hid]`）。FR-1、FR-2、NFR-3。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 8 位十六进制，须存在否则 404 |
| 其余 body 键 | body | 混合 | 否 | 白名单外键忽略；见「字段缺失 vs 显式 null」 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 资源 id |
| 变更后全字段 | 见 §字段模型 | 是 | 返回完整资源（非 diff） |
| `updated_at` | string | 是 | naive UTC，变更后刷新 |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| JSON 字段非数组 | 422 | 同 POST |
| 校验失败 | 422 | pydantic 校验信息 |

#### 示例

```http
PATCH /api/v1/ideas/27060d9b
{"success_metric": "详情页一屏可见下一步动作 0%→100%", "tags": ["高风险", "合规"]}
```
```json
// 200（body 为更新后完整对象；IdeaChange 追加 {success_metric: [...], tags: [...]}）
{"id": "27060d9b", "goal": "...", "success_metric": "详情页一屏可见下一步动作 0%→100%", "tags": ["高风险", "合规"]}
```

### GET /api/v1/ideas/{idea_id}/cockpit

驾驶舱聚合：7 区块 sections（区块级降级）+ `next_action`（5 级序，FR-6）+ `high_risk`（FR-8）+ 任务图（FR-9 内嵌于 `tasks` 区）。FR-3、FR-4。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| `user` | query | string | 否 | 默认 `local`；决定 dismiss 过滤的偏好集 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sections` | map | 是 | 7 键见「枚举全集」；每值 `{status, reason?, cached_at?}` |
| `sections[x].status` | string | 是 | `ok` / `degraded`；**无整包 degraded 字段** |
| `idea` | object | 是 | 想法聚合（8 字段 + 基础字段） |
| `next_action` | object\|null | 是 | `{rule_id, action, reason, snapshot}`；无命中为 `null`（已被 dismiss 且未复活也返回 `null`） |
| `high_risk` | boolean | 是 | tags ∩ 词表非空即 true |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| 单区超时/异常 | 200 | 不报错：该区 `status=degraded` + `reason`（如 `mio_timeout`） |

#### 示例

```http
GET /api/v1/ideas/27060d9b/cockpit
```
```json
// 200
{"sections": {"goal": {"status": "ok"}, "hypotheses": {"status": "degraded", "reason": "mio_timeout", "cached_at": "2026-09-27T06:00:00"},
  "mvp": {"status": "ok"}, "tasks": {"status": "ok"}, "risks": {"status": "ok"},
  "approvals": {"status": "ok"}, "retrospective": {"status": "ok"}},
 "idea": {"id": "27060d9b", "goal": "", "tags": null},
 "next_action": {"rule_id": "missing_goal", "action": "补全目标与成功标准", "reason": "goal 为空", "snapshot": {"rule_id": "missing_goal", "goal_present": false}},
 "high_risk": false}
```

### POST /api/v1/ideas/{idea_id}/next-action/dismiss

服务端忽略当前「下一步动作」：写 `IdeaUserPref{user, rule_id, dismissed_at, condition_snapshot}`；7 天过期、snapshot 关键位变化立即复活。FR-7。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| `rule_id` | body | string | 是 | 非空；必须在 5 条规则枚举内且**当前命中**，否则 409 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean | 是 | 恒 `true` |
| `rule_id` | string | 是 | 回显 |
| `dismissed_at` | string | 是 | 本次落库时间（重复调用刷新） |
| `condition_snapshot` | object | 是 | 结构化布尔位（复活判定依据） |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| `rule_id` 缺失/空 | 422 | `rule_id is required` |
| 规则未知或未命中 | 409 | `unknown rule: x` / `rule x not matched, nothing to dismiss` |

#### 示例

```http
POST /api/v1/ideas/27060d9b/next-action/dismiss
{"rule_id": "missing_goal"}
```
```json
// 200
{"ok": true, "rule_id": "missing_goal", "dismissed_at": "2026-09-27T07:10:00",
 "condition_snapshot": {"rule_id": "missing_goal", "goal_present": false}}
```
```json
// 409（goal 已补全 → 规则不再命中）
{"detail": "rule missing_goal not matched, nothing to dismiss"}
```

## 附录

### FR 追溯

| FR | 端点/章节 |
|---|---|
| FR-1 | §字段模型、POST/PATCH 明细 |
| FR-2 | POST/PATCH 明细（diff 键 `assumptions[hid]`） |
| FR-3 | cockpit 明细（响应结构） |
| FR-4 | cockpit 明细 + §超时与并发（区块级降级，不 500） |
| FR-5 | cockpit `sections` 7 区（渲染顺序属前端，见 spec） |
| FR-6 | cockpit `next_action` + §枚举全集（5 级序） |
| FR-7 | dismiss 明细（服务端偏好 + 复活 + 7 天） |
| FR-8 | cockpit `high_risk` + §枚举全集（词表） |
| FR-9 | cockpit `tasks` 区（一层下游/环降级/>20 折叠） |
| FR-10 | §兼容性与废弃策略 |

## 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-09-27 | v1.0 | P0 初版：4 组端点、区块级降级契约、dismiss 偏好契约 |
