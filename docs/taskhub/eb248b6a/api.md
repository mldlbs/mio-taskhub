# API 设计 — P4 验收清单全量核对（task eb248b6a）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.2 |
| 契约版本 | idea-landing-p4-audit |
| 状态 | approved |
| 最后更新 | 2026-09-27 23:37 |
| 负责人 | opencode（mio-taskhub） |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.3 冻结） |
| 需求基线 | docs/taskhub/eb248b6a/requirement.md（FR-30~FR-33） |
| 关联任务 | eb248b6a（branch task-eb248b6a） |
| 前序契约 | docs/taskhub/d4bdf530/api.md（P3）；P2 见 docs/taskhub/286dd112/api.md、P1 见 docs/taskhub/8442e38d/api.md、P0 见 docs/taskhub/2fe56e6a/api.md |

## 文档信息与范围

本文是 P4「v1.3 验收清单 19 项全量核对」的契约文档。**本任务不新增、不修改任何端点、字段与状态码**：核对以只读为主，仅有的写操作是「活体探测件的创建/归档」与「探测讨论的创建/关闭」。本文因此锁定**核对所涉既有端点的调用契约快照**（参数/响应/错误码/示例），作为「契约未被核对过程破坏」的对照基准。

**明确不含**（防止被当成全量接口清单）：

- 新增业务端点：**零新增**（FR-30 只核对不开发）；
- 字段模型变更：**零变更**（v1.3 冻结；成功标准结构化已裁决不做）；
- 前端接口封装层（`web/src/api.js`）的内部函数签名：不属于 HTTP 契约；
- 复核性端点（Docs 文档链、任务看板 API）：不在本次核对范围。

## 环境与基础地址

| 环境 | Base URL | 说明 |
|---|---|---|
| dev（本机常驻） | `http://127.0.0.1:48620/api/v1` | PyInstaller 打包 hub，SQLite 单库（本次活体核对目标） |
| test（单测） | `http://testserver/api/v1` | FastAPI ASGITransport 进程内直调，无网络（定点批次走此环境） |
| staging | 不适用 | 产品无 staging 部署形态 |
| prod | `http://127.0.0.1:48620/api/v1` | 与 dev 同构（本机单用户，无反向代理） |

无网关路径前缀剥离/重写：路径以 `/api/v1` 原样暴露。MCP 客户端经 `mio_taskhub/mcp_server.py` 映射到同一 base URL 的 HTTP 端点。

## 认证与鉴权

| 项 | 约定 |
|---|---|
| 认证方式 | **无**（本机单用户产品，绑定 127.0.0.1） |
| 凭证来源 | 不适用：不签发 token、不设 cookie |
| 权限模型 | 全部调用方等权（Web UI、MCP、本地脚本同一信任级） |
| 401 与 403 | **本组接口不返回**；若未来挂反向代理引入认证，401=未认证、403=已认证但无权 |

## 通用请求头

| 头 | 值 | 必填 | 说明 |
|---|---|---|---|
| Content-Type | `application/json; charset=utf-8` | 带请求体时必填 | 缺失时 FastAPI 按 JSON 解析失败 → 422 |
| Accept | `application/json` | 否 | 响应恒为 JSON（UTF-8） |

## 通用响应结构

本组接口为 **REST 直出式**：成功响应即资源本身，**无统一 `{code,msg,data}` 外壳**（与 P0~P3 一致）。逐端点响应字段见 §接口明细。

失败响应统一外壳：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | string | 是 | 人类可读错误说明（FastAPI `HTTPException` 惯例）|

例：`{"detail": "review is required when mode=review (missing review payload)"}`、`{"detail": "review gate: risks：风险清单至少 1 条；decisions：决策选项至少 2 个；action_items：行动项至少 1 条"}`（均为本次活体实际返回）。

- `GET .../cockpit` 返回顶层对象（`sections` 为 map）；单区异常不改结构：仍 200，仅该区 `status=degraded`；
- `GET /ideas` 返回 `{count, ideas}` 包装体（键名实测为 `count`/`ideas`，非 `items`/`total`）；`GET /ideas/{id}` 与各写端点返回单一 idea 资源（裸对象）。

## 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `idea_not_found` | 想法不存在 | 路径 `idea_id` 在 ideas 表无记录 | 核对 id；列表接口重新获取 |
| 404 | `discussion_not_found` | 讨论不存在 | 路径 `discussion_id` 无记录 | 核对 id |
| 422 | `review_required` | review 模式关闭缺结构化 review | `mode=review` 且 body 无 `review`（本条为本次活体实测 detail 原文） | 补齐五段结构化 review 再关闭 |
| 422 | `review_gate_incomplete` | 条目不足 | `risks<1` / `decisions<2` / `action_items<1`（detail 逐项指明） | 按 detail 补齐缺项 |
| 422 | `roles_required` | review 会议缺 roles | `mode=review` 且 `roles` 缺失或空 | 传非空 roles 列表 |
| 422 | `validation_error` | 其余参数校验失败 | FastAPI/pydantic 校验不通过 | 按 `detail` 修正 |
| 200（区内降级） | `section_degraded` | 驾驶舱单区降级 | `_build_*` 抛异常被 SectionDegraded 捕获 | 该区灰显，其余区块正常 |
| 500 | `internal_error` | 服务端异常 | 非预期错误（设计上不应出现） | 重试一次；仍失败查 hub 日志 |

## 字段命名与类型规范

| 项 | 约定 | 反例 |
|---|---|---|
| 命名 | `snake_case` | `ideaId` ✗ → `idea_id` ✓ |
| ID | idea/task/run/discussion 为 8 位十六进制 | 自造 uuid ✗ |
| 布尔 | JSON `true/false` | `"folded": "1"` ✗ |
| 数组 | JSON 数组；空数组 `[]` 表示「无数据/显式清空」 | 逗号分隔字符串 ✗ |
| 可选字段 | `null` = 未设置（前端按空态渲染）；字段缺失 = 端点不返回该键 | 「缺失」与「null」混用 ✗ |

## 枚举全集

| 枚举 | 取值全集 |
|---|---|
| `sections` 键（7 区） | `goal`, `hypotheses`, `mvp`, `tasks`, `risks`, `approvals`, `retrospective` |
| `sections[x].status` | `ok`, `degraded` |
| `idea.status` | `new`, `fermenting`, `formed`, `broken_down`, `archived`, `cancelled` |
| `Discussion.mode` | `free`, `review` |
| `RolePrompt.role` | `产品`, `技术`, `商业`, `合规`, `红队` |
| `action_items[].status` | `pending`, `doing`, `done` |

## 幂等性与重试

| 接口 | 幂等 | 幂等键/行为 | 重复提交返回 |
|---|---|---|---|
| GET 类全部 | 是 | 纯读 | 200 + 当前资源 |
| POST /discussions | 否 | 每次创建新会话（核对侧只创建一次探测会话） | 200 + 新资源 |
| POST /discussions/{id}/close | 否（终态） | 已 closed 再关闭按既有实现处理；核对流程仅关闭一次 | 200 / 422 按门控 |
| PATCH /ideas/{id}（探测件归档） | 是 | 归档为终态，重放 no-op | 200 + 最新资源 |

**可安全重试**：GET 全部、PATCH 归档。**不可重试**：POST 创建类（会造重复对象）。**建议退避**：`500` 指数退避（1s/2s/4s），`4xx` 不重试。

## 限流与配额

本机单用户无网关限流，接口不设配额。核对侧自律：探测件 ≤1 个 idea + ≤1 个讨论（核对完毕归档/关闭，避免污染看板）。

## 超时与并发

| 项 | 值 |
|---|---|
| cockpit 总预算 | 5s（P0 骨架不变）；单区异常 → 该区 `degraded`，接口不 500 |
| 假设区跨服务预算 | Mio 3s（FR-16，P4 不涉及真实 Mio 调用） |
| 各区块预算 | Mio 3s / 其余 1s（P0 `SECTION_TIMEOUTS`，P3/P4 未改） |
| 并发模型 | SQLite WAL；核对侧无并发写（定点批次内并发用例由测试夹具构造） |
| 客户端超时 | 活体核对用 10~15s（Invoke-WebRequest `-TimeoutSec`），超过视为核对失败需重试 |

## 分页

**本组接口不涉及分页**：`GET /ideas` 返回全量（本机量级小）；cockpit/讨论均为单资源读取。

## 时间 / 数值 / 空值约定

| 项 | 约定 |
|---|---|
| 时间 | ISO8601 UTC；`created_at`/`updated_at`/`ended_at` 等为 naive UTC `isoformat()`，未完成时为 `null` |
| 数值 | `total` 等为非负整数；`exit_code` 整数（0=成功） |
| 空值 | 字符串空=`""`；JSON 空=`null`；无数据数组 `[]`；`GET /ideas` 无数据 → `{count: 0, ideas: []}` |
| 归一化 | 旧数据 `NULL` 新字段在读取端归一为 `""`（字符串类）或 `[]`（数组类），保证前端空态可渲染（FR-1） |

## 文件上传与下载

不适用：本组接口无文件上传/下载（核对数据均为库内结构化对象）。

## 安全与脱敏

| 项 | 说明 |
|---|---|
| 网络暴露 | 仅绑定 127.0.0.1，不出网 |
| 数据本地 | 核对只用本机数据；不拉取外部（Mio 真实调用不在本次核对范围，用测试夹具替代） |
| 敏感数据 | 探测件正文仅写核对用途标记，不含真实业务/个人信息；不记录日志外传 |
| 注入 | 全部参数经 pydantic 校验 + SQLAlchemy 绑定参数，无拼接 SQL |

## 版本策略

| 项 | 约定 |
|---|---|
| 路径版本 | `/api/v1`；破坏性变更 → `/api/v2` 并行 |
| 字段演进 | P4 **零变更**；本文仅为既有契约的快照对照 |
| 契约版本 | `idea-landing-p4-audit`；仅当核对发现契约回退时才需变更本文 |

## 兼容性与废弃策略

- 本任务承诺：核对过程**不改变任何既有端点契约**（FR-32）；写操作限于探测件创建/归档与探测讨论创建/关闭，且均已清理；
- 核对结论「契约未回退」的依据：定点批次中 P0~P3 契约用例全绿（含 `test_import_and_patch_dual_write_p1_contract_unchanged` 的逐键一致断言）+ 活体响应片段与本文示例一致；
- 废弃流程：本任务无废弃对象；未来废弃遵循「标 deprecated → 双写/双读 → 下线」。

## 接口清单

| 方法 | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/ideas` | 想法列表（核对寻找探测对象/确认清理） | 无鉴权 | 是 | 无 | 只读 |
| GET | `/api/v1/ideas/{idea_id}` | 单想法读取（NULL 归一、归档状态回读） | 无鉴权 | 是 | 无 | 只读 |
| POST | `/api/v1/ideas` | 创建探测 idea（最小字段，核对后归档） | 无鉴权 | 否 | 无 | 新建 → `new` |
| PATCH | `/api/v1/ideas/{idea_id}` | 探测件归档（`status=archived`）；P0~P3 语义不变 | 无鉴权 | 是 | 无 | → `archived` |
| GET | `/api/v1/ideas/{idea_id}/cockpit` | 7 区聚合（核对空数据 status=ok、summary 全 0） | 无鉴权 | 是 | 无 | 只读；单区可 degraded |
| GET | `/api/v1/ideas/{idea_id}/assumption-links` | 假设关联行读取（FR-28 核对面） | 无鉴权 | 是 | 无 | 只读 |
| POST | `/api/v1/discussions` | 创建讨论/评审会（`mode`/`roles`，FR-18/FR-23） | 无鉴权 | 否 | 无 | 新建 → `open` |
| POST | `/api/v1/discussions/{id}/close` | 关闭讨论（review 门控 FR-21） | 无鉴权 | 否 | 无 | `open` → `closed` |
| POST | `/api/v1/discussions/{id}/convert` | 行动项幂等转任务（FR-22） | 无鉴权 | 是 | 无 | 行动项 → 带 `task_id` |

## 接口明细

### GET /api/v1/ideas

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|---|
| — | — | — | — | — | 无参数（GET 全量） |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|
| `count` | integer | 是 | 条数（实测键名，非 `total`） | 36 |
| `ideas[]` | array | 是 | 想法列表；空库为 `[]`（实测键名，非 `items`） | `[{"id":"f6304130","status":"archived"}]` |
| `ideas[].id` | string | 是 | 8 位十六进制 | f6304130 |
| `ideas[].status` | string | 是 | 取值见 §枚举全集 | archived |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 500 | `internal_error` | 服务端异常 | 非预期错误 | 重试；查 hub 日志 |

#### 示例

```http
GET /api/v1/ideas
```
```json
// 200（实测：本机存量 36 个想法，含 8 月创建的旧数据 f0dcab69）
{"count": 36, "ideas": [{"id": "f0dcab69", "title": "…", "status": "cancelled", "version": 1}]}
```

### POST /api/v1/ideas

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|---|---|---|---|---|---|---|---|
| `title` | body | string | 是 | — | 非空 | 标题 | P4 核对临时-NULL归一活体 |
| `description` | body | string | 否 | `""` | — | 描述 | p4-audit-probe |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|
| `id` | string | 是 | 新建 id | f6304130 |
| `status` | string | 是 | 初始 `new` | new |
| `version` | integer | 是 | 初始 1 | 1 |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 422 | `validation_error` | 参数校验失败 | `title` 缺失/空 | 补 `title` |

#### 示例

```http
POST /api/v1/ideas
{"title": "P4 核对临时-NULL归一活体", "description": "p4-audit-probe"}
```
```json
// 200
{"id": "f6304130", "title": "P4 核对临时-NULL归一活体", "status": "new", "version": 1}
```

### PATCH /api/v1/ideas/{idea_id}

核对用途：把探测件归档（`status=archived`）。P0~P3 既有语义不变。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 | 探测件 id |
| `status` | body | string | 否 | 白名单枚举 | 目标 `archived` |
| `change_reason` | body | string | 否 | — | 审计留痕，如 `p4-audit-probe-cleanup` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|
| `id` | string | 是 | 回显 | f6304130 |
| `status` | string | 是 | `archived` | archived |
| `version` | integer | 是 | 真实变更 +1 | 2 |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `idea_not_found` | 想法不存在 | id 查无 | 核对 id |
| 422 | `validation_error` | 状态非法 | `status` 不在白名单 | 用合法枚举值 |

#### 示例

```http
PATCH /api/v1/ideas/f6304130
{"status": "archived", "change_reason": "p4-audit-probe-cleanup"}
```
```json
// 200
{"id": "f6304130", "status": "archived", "version": 2}
```

### GET /api/v1/ideas/{idea_id}/cockpit

核对用途：空数据 7 区 `status=ok`、`retrospective.summary` 全 0（FR-3/FR-27 活体证据）。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 | 探测件 id |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|
| `sections` | object | 是 | 7 区 map（键见 §枚举全集） | `{"goal": {...}}` |
| `sections[x].status` | string | 是 | `ok`/`degraded` | ok |
| `sections[x].reason` | string\|null | 是 | 降级原因 | null |
| `sections.retrospective.data.summary` | object | 是 | `{success, failure, pending, total}`；空数据全 0 | `{"total":0}` |
| `next_action` | object\|null | 是 | 下一步动作（P0 FR-6） | null |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `idea_not_found` | 想法不存在 | id 查无 | 核对 id |
| 200 | `section_degraded` | 单区降级（非错误） | 某区构建异常 | 该区灰显，其余正常 |

#### 示例

```http
GET /api/v1/ideas/f6304130/cockpit
```
```json
// 200（节选：本次活体实测 7 区全 ok，复盘 summary 全 0）
{"sections": {
  "goal": {"status": "ok", "data": {}},
  "tasks": {"status": "ok", "data": {"items": [], "graph": null, "has_cycle": false, "cycles": [], "truncated": false, "total": 0, "folded": false}},
  "retrospective": {"status": "ok", "data": {"summary": {"success": 0, "failure": 0, "pending": 0, "total": 0}, "items": [], "reviews": []}}}}
```

### POST /api/v1/discussions

核对用途：`mode=review` + `roles` 建会并回读 `mode`/`roles`/`prompt_snapshot`（FR-18/FR-19/FR-23 活体证据）。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|---|---|---|---|---|---|---|---|
| `task_id` | body | string | 否 | `""` | 与 `idea_id` 至少一个 | 绑定任务 | eb248b6a |
| `idea_id` | body | string | 否 | `""` | 与 `task_id` 至少一个 | 绑定想法 | — |
| `topic` | body | string | 是 | — | 非空、≤200 | 主题 | P4 活体核对 |
| `mode` | body | string | 否 | `free` | `free`/`review`，非法 422 | 讨论模式 | review |
| `roles` | body | array[string] | 否 | `[]` | `mode=review` 时非空否则 422 | 评审角色 | `["产品","技术","红队"]` |
| `stage` | body | string | 否 | `brainstorming` | — | 研发阶段 | review |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|
| `id` | string | 是 | 讨论 id | f69ff749 |
| `mode` | string | 是 | 回显模式 | review |
| `roles` | array[string] | 是 | 回显角色（逐字） | `["产品","技术","红队"]` |
| `prompt_snapshot` | object | 是 | 创建时快照：`{roles, prompts:{角色:{version,prompt}}, captured_at}` | `{"captured_at":"2026-09-27T14:56:04"}` |
| `status` | string | 是 | 初始 `open` | open |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 422 | `roles_required` | review 缺 roles | `mode=review` 且 roles 空 | 传非空 roles |
| 422 | `validation_error` | mode 非法/缺 topic | 参数不合规 | 按 detail 修正 |

#### 示例

```http
POST /api/v1/discussions
{"task_id":"eb248b6a","topic":"P4 活体核对","mode":"review","roles":["产品","技术","红队"],"stage":"review"}
```
```json
// 200（节选：本次活体实测回显）
{"id": "f69ff749", "mode": "review", "roles": ["产品", "技术", "红队"], "status": "open",
 "prompt_snapshot": {"roles": ["产品","技术","红队"],
   "prompts": {"红队": {"version": 1, "prompt": "以红队视角评审：…"}},
   "captured_at": "2026-09-27T14:56:04.006229+00:00"}}
```

### POST /api/v1/discussions/{id}/close

核对用途：review 门控 422 两连（缺 review / 条目不足）+ 合法结构化 review 关闭（FR-21 活体证据）。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|---|
| `discussion_id` | path | string | 是 | 须存在否则 404 | 讨论 id |
| `conclusions` | body | string | 是 | 非空 | 结论 |
| `summary` | body | string | 否 | — | 摘要 |
| `review` | body | object | `mode=review` 时是 | 五段齐全且达标：`risks≥1`、`divergences` 非空、`suggestions` 非空、`decisions≥2`、`action_items≥1`（每条含 id/owner/action/due/status，task_id 可空） | 结构化评审 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|
| `id` | string | 是 | 讨论 id | f69ff749 |
| `status` | string | 是 | `closed` | closed |
| `review` | object | 是 | 落库的 review 原样可回读 | `{"risks":["..."]}` |
| `ended_at` | string | 是 | 关闭时刻 | 2026-09-27T14:56:47 |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 422 | `review_required` | 缺结构化 review | `mode=review` 且无 `review` | 补五段 review |
| 422 | `review_gate_incomplete` | 条目不足 | risks<1 / decisions<2 / action_items<1 | 按 detail 补缺项 |
| 404 | `discussion_not_found` | 讨论不存在 | id 查无 | 核对 id |

#### 示例

```http
POST /api/v1/discussions/f69ff749/close
{"conclusions":"探测通过","summary":"门控探测","review":{"risks":["残留污染"],"divergences":"无分歧","suggestions":"无需跟进","decisions":["关闭本讨论","证据入报告"],"action_items":[{"id":"a1","owner":"opencode","action":"写报告","due":"2026-09-27","status":"pending","task_id":"eb248b6a"}]}}
```
```json
// 200（节选：本次活体实测）
{"id": "f69ff749", "status": "closed", "ended_at": "2026-09-27T14:56:47.324050"}
```
```json
// 422（实测原文）
{"detail": "review is required when mode=review (missing review payload)"}
{"detail": "review gate: risks：风险清单至少 1 条；decisions：决策选项至少 2 个；action_items：行动项至少 1 条"}
```

## 附录

### FR 追溯

| FR | 本文落点 |
|---|---|
| FR-30 | §文档信息与范围（零新增/零变更声明）、§接口清单（核对所涉端点快照） |
| FR-31 | §接口明细各「示例」段（活体实测响应片段即报告证据来源） |
| FR-32 | §兼容性与废弃策略（核对不改变契约 + 探测件清理承诺） |
| FR-33 | §统一错误码（review 门控 422 原文）、§兼容性与废弃策略（契约未回退依据） |

### 相关文档

- 核对报告：`docs/taskhub/acceptance-audit-p4.md`
- 设计基线：`docs/taskhub/design-idea-landing.md`（v1.3）
- 前序契约：`docs/taskhub/d4bdf530/api.md`（P3）、`docs/taskhub/286dd112/api.md`（P2）、`docs/taskhub/8442e38d/api.md`（P1）、`docs/taskhub/2fe56e6a/api.md`（P0）

## 变更记录

| 版本 | 日期 | 变更 | 类型 | 作者 |
|---|---|---|---|---|
| v1.0 | 2026-09-27 | 初版：锁定核对所涉既有端点契约快照，声明零新增/零变更 | 兼容 | opencode |
