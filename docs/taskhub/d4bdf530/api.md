# API 设计 — 想法落地闭环 P3 拓扑/复盘/假设关联表（task d4bdf530）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 契约版本 | idea-landing-p3 |
| 状态 | approved |
| 最后更新 | 2026-09-27 21:56 |
| 负责人 | opencode（mio-taskhub） |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.3 冻结·包 D） |
| 需求基线 | docs/taskhub/requirement-idea-landing-p3.md（FR-26~FR-29） |
| 关联任务 | d4bdf530（branch task-d4bdf530） |
| 前序契约 | docs/taskhub/8442e38d/api.md（P1）；P0 见 docs/taskhub/2fe56e6a/api.md，P2 见 docs/taskhub/286dd112/api.md（如适用） |

## 文档信息与范围

本文是 P3「想法落地闭环·包 D 任务拓扑完整版 + 复盘 + 假设关联表」的契约文档，适用于本机单用户部署的 mio-taskhub Web 客户端。P0~P2 端点的既有语义以前序契约为准，本文只描述 P3 引入的差异与新增端点。

**明确不含**（防止被当成全量接口清单）：

- 驾驶舱**新端点/新分区**：不存在——tasks/retrospective 都在既有 `sections` 内扩展（NFR-3 只增键不改键）；
- 成功标准结构化 `{metric, from, to, deadline}` 字段端点：**本期裁决不纳入**（FR-29，见设计稿 v1.3 裁决条目）；
- 关联表**写端点**：不存在——行的增删改全部经 import/PATCH/迁移双写，读只有 `GET .../assumption-links`；
- 跨项目/全局拓扑视图、DAG 布局服务：不存在（前端沿用现有列表/树渲染）。

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
| 401 与 403 | **本组接口不返回 401/403**；若未来挂反向代理引入认证，401=未认证、403=已认证但无权 |

## 通用请求头

| 头 | 值 | 必填 | 说明 |
|---|---|---|---|
| Content-Type | `application/json; charset=utf-8` | 带请求体时必填 | 缺失时 FastAPI 按 JSON 解析失败 → 422 |
| Accept | `application/json` | 否 | 响应恒为 JSON（UTF-8） |

## 通用响应结构

本组接口为 **REST 直出式**：成功响应即资源本身，**无统一 `{code,msg,data}` 外壳**（与 P0~P2 一致）。逐端点响应字段见 §接口明细。

失败响应统一外壳：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | string | 是 | 人类可读错误说明（FastAPI `HTTPException` 惯例） |

- `GET .../cockpit` 返回顶层对象（`sections` 为 map）；**跨服务/单区异常不改结构**：仍 200，仅该区 `status=degraded`（P3 tasks/retrospective 全本地 SQL，降级主要防未知异常兜底）；
- `GET .../assumption-links` 返回包装体 `{idea_id, links, total}`（资源列表非单一资源，带聚合字段）。

## 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `idea_not_found` | 想法不存在 | 路径 `idea_id` 在 ideas 表无记录 | 核对 id；列表接口重新获取 |
| 422 | `ids_malformed` | 导入 ids 形状非法（P1 语义不变） | `ids` 非数组或含非字符串/空串元素 | 改为 `["hyp-id", ...]` |
| 422 | `unknown_hypothesis_ids` | 引用 Mio 不存在的 id（P1 不变） | 不在当前假设库 | 刷新假设列表后重选 |
| 422 | `assumption_not_found` | 单条假设不存在（P1 不变） | `assumptions` 无匹配 hid | 先 GET idea 核对 |
| 422 | `nothing_to_update` | 回写 body 无有效字段（P1 不变） | 三字段都未传 | 至少传一个 |
| 200（区内降级） | `section_degraded` | 驾驶舱单区降级 | `_build_tasks`/`_build_retrospective` 抛异常被 SectionDegraded 捕获 | 该区灰显，其余区块正常 |
| 500 | `internal_error` | 服务端异常 | 非预期错误（设计上不应出现） | 重试一次；仍失败查 hub 日志 |

## 字段命名与类型规范

| 项 | 约定 | 反例 |
|---|---|---|
| 命名 | `snake_case` | `hypothesisId` ✗ → `hypothesis_id` ✓ |
| ID | idea/task/run/discussion 为 8 位十六进制；Mio hypothesis id 原样透传 | taskhub 自造假设 id ✗ |
| 布尔 | JSON `true/false` | `"has_cycle": "1"` ✗ |
| 数组 | JSON 数组；空数组 `[]` 表示「显式清空/无数据」 | 逗号分隔字符串 ✗ |
| 枚举值 | 小写蛇形（`direct`、`upstream`） | 中文/驼峰 ✗ |

**P3 只增键承诺（NFR-1）**：`sections.tasks.data` 在 P0 键（`items/graph/has_cycle/warning/total/folded`）之上新增 `cycles/truncated/upstream_total/downstream_total` 与 `items[].kind`；`items[].downstream` 布尔**保留**（P0 语义：非 direct 即 false 之外的判定不变——实现为 kind==downstream）；`sections.retrospective.data` 由 P0 空壳 `{items: []}` 演进为 `{summary, items, reviews}`（`items` 键保留且仍为数组）。既有键零删除、零改型。

## 枚举全集

| 枚举 | 取值全集 |
|---|---|
| `sections` 键（7 区） | `goal`, `hypotheses`, `mvp`, `tasks`, `risks`, `approvals`, `retrospective` |
| `sections[x].status` | `ok`, `degraded` |
| `items[].kind` / `graph.nodes[].kind`（P3 新增） | `direct`（idea 直接关联）、`upstream`（depends_on 传递闭包）、`downstream`（反向边传递闭包） |
| `Run.state`（复盘 pending 判定源） | `claimed`, `running`, `retrying`, `finished` |
| `assumption_links.status` | 默认 `unverified`；写入值同本地假设 `status`（`open`/`active`/`validated`/`rejected`，服务端不强枚举） |
| `idea.status`（既有） | `new`, `fermenting`, `formed`, `broken_down`, `archived`, `cancelled` |

## 幂等性与重试

| 接口 | 幂等 | 幂等键/行为 | 重复提交返回 |
|---|---|---|---|
| GET .../cockpit | 是 | 纯读；tasks/retrospective 本地 SQL 无缓存（与假设区 5min 缓存不同） | 200 + 当前聚合 |
| GET .../assumption-links | 是 | 纯读，按 `hypothesis_id` 排序保证稳定序 | 200 + 当前行集 |
| POST .../hypotheses/import（P3 行为变更） | 是 | 集合合并不变；关联表行幂等创建（已有行跳过，不覆盖回写字段） | 200 + `added: []` |
| PATCH .../assumptions/{hid}（P3 行为变更） | 是 | JSON 缓存与关联表**同事务双写**；值全同重放为 no-op | 200 + 最新资源 |
| PATCH /ideas/{id}（hypotheses 变更） | 是 | `_sync_link_rows` 集合 reconcile（新增建行/移除删行/已有保留） | 200 + 最新资源 |

**可安全重试**：以上全部。**建议退避**：`500` 指数退避（1s/2s/4s），`4xx` 不重试。

## 限流与配额

本机单用户无网关限流，接口不设配额。前端约束：任务图/复盘区随详情页拉一次 cockpit，不轮询（BFS/聚合均为本地 SQL，但节点上限 100、明细上限 5 已防大想法）。

## 超时与并发

| 项 | 值 |
|---|---|
| tasks/retrospective 区块预算 | **1s**（P0 `SECTION_TIMEOUTS` 不变；P3 为本地 SQL 聚合，正常毫秒级） |
| 节点上限 | tasks 区 BFS **100 节点**截断 → `truncated=true`（FR-26） |
| 明细上限 | 复盘 run 明细 **5 条**、评审记录 **5 条**（NFR-2） |
| 环检测上限 | `_find_cycles` 至多返回 **5 个**去重环路径（`cycles` 可能是子集，足以展示） |
| cockpit 总预算 | 5s（不变）；单区异常 → 该区 `degraded`，接口不 500（NFR-3） |
| 并发模型 | SQLite WAL；关联表双写在既有请求事务内，与 P1 进程内互斥锁语义一致（FR-15 用例回归锁定） |
| 其余 PATCH | last-write-wins（P0 不变） |

## 分页

**本组接口不涉及分页**：assumption-links 单想法行数 ≤ 假设数（百级内）；cockpit 为单资源聚合，节点/明细上限见 §超时与并发。

## 时间 / 数值 / 空值约定

| 项 | 约定 |
|---|---|
| 时间 | ISO8601 UTC；`links[].updated_at`、`items[].finished_at`、`reviews[].ended_at` 为 naive UTC `isoformat()`（可能为 `null`：run 未完成/评审未结束） |
| 排序时间归一 | 服务端 `_ts()` 把 aware datetime 转 naive UTC 再比较，避免混排 TypeError |
| 数值 | `exit_code` 为整数（`0`=成功）；`decision_count` 等为非负整数 |
| 空值 | 字符串空=`""`；JSON 空=`null`；无数据数组用 `[]` |
| result 截断 | `result_excerpt` = `run.result[:200]`（按字符，不加省略号） |

## 文件上传与下载

不适用：本组接口无文件上传/下载（拓扑/复盘/关联行均为库内结构化数据）。

## 安全与脱敏

| 项 | 说明 |
|---|---|
| 网络暴露 | 仅绑定 127.0.0.1，不出网 |
| 数据本地 | FR-27 明确数据源全 hub 本地（Run/Discussion），无跨服务调用面 |
| 敏感数据 | `result_excerpt` 可能含任务执行输出——只截断不脱敏（本机单用户）；不记录假设正文全文到日志 |
| 注入 | 全部参数经 pydantic 校验 + SQLAlchemy 绑定参数；环检测/闭包在 Python 内存图上做，无拼接 SQL |

## 版本策略

| 项 | 约定 |
|---|---|
| 路径版本 | `/api/v1`；破坏性变更（改字段类型/删字段/改语义）→ `/api/v2` 并行 |
| 字段演进 | P3 全部为**只增键**（NFR-1），旧客户端零破坏；关联表为新增存储，读端点为新增路径 |
| 契约版本 | `idea-landing-p3`；语义变更时递增并更新「变更记录」 |

## 兼容性与废弃策略（FR-29）

- P0/P1/P2 全部既有行为不受 P3 影响：cockpit 其余区块、free/review 讨论、convert 幂等、next_action、P1 import/PATCH **响应逐键一致**（回归用例锁定）；
- `sections.tasks.data` / `sections.retrospective.data` 只增键：`items[].downstream`、`graph:null` 降级、`folded` 语义全部保留（P0 验收「有环降级列表+警告；>20 折叠」不回退）；
- 关联表启动迁移幂等（可重复执行，已有行跳过）；旧 idea（无 hypotheses/assumptions）迁移后 `links: []`；
- 全量 pytest 基线 **915 passed + 1 skipped 只增不减** + `npm run build` 绿；
- 废弃流程：先在文档标 deprecated 并保留 ≥1 个发布周期，再随大版本移除。

## 接口清单

| 方法 | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/ideas/{idea_id}/cockpit` | 驾驶舱聚合（P3：tasks 多层拓扑 + retrospective 真实聚合，FR-26/27） | 无鉴权 | 是 | 无（建议手动刷新） | 只读；单区可 degraded |
| GET | `/api/v1/ideas/{idea_id}/assumption-links` | 假设关联行全量读（FR-28 新增） | 无鉴权 | 是 | 无 | 只读 |
| POST | `/api/v1/ideas/{idea_id}/hypotheses/import` | 导入引用（P3：内部建关联行，契约不变，FR-28） | 无鉴权 | 是 | 无 | 引用 +1，进 IdeaChange |
| PATCH | `/api/v1/ideas/{idea_id}/assumptions/{hid}` | 单条假设回写（P3：upsert 关联行，契约不变，FR-28） | 无鉴权 | 是 | 无 | 进 IdeaChange |
| PATCH | `/api/v1/ideas/{idea_id}` | 更新 `hypotheses`（P3：reconcile 关联行，契约不变，FR-28） | 无鉴权 | 是 | 无 | 进 IdeaChange |

## 接口明细

### GET /api/v1/ideas/{idea_id}/cockpit

P3 变更仅限 `sections.tasks` 与 `sections.retrospective` 的 `data`（只增键）；其余 5 区契约以 P0~P2 为准。FR-26、FR-27。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| （body） | — | — | — | 无请求体（GET） |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sections.tasks.data.items[]` | array | 是 | 闭包内全部任务：`{id, title, state, stage, blocked, downstream, kind}`；`downstream` 为 P0 键保留 |
| `sections.tasks.data.graph` | object\|null | 是 | 无环时 `{nodes:[{id,title,kind}], edges:[{from,to}]}`；**含环时 `null`**（P0 降级不回退） |
| `sections.tasks.data.has_cycle` | boolean | 是 | 布尔保留（等价 `cycles` 非空） |
| `sections.tasks.data.cycles` | array[array[string]] | 是 | **P3 新增**：环节点路径（回边不入列，至多 5 个去重）；无环 `[]` |
| `sections.tasks.data.truncated` | boolean | 是 | **P3 新增**：节点 >100 截断标记 |
| `sections.tasks.data.upstream_total` | integer | 是 | **P3 新增**：上游闭包节点数 |
| `sections.tasks.data.downstream_total` | integer | 是 | **P3 新增**：下游闭包节点数 |
| `sections.tasks.data.warning` | string\|null | 是 | 含环文案 `检测到依赖环，已降级为任务列表` |
| `sections.tasks.data.total` / `folded` | integer / boolean | 是 | P0 键保留；`folded = total > 20` |
| `sections.retrospective.data.summary` | object | 是 | **P3 新增**：`{success, failure, pending, total}`；`finished && exit_code==0`→success，非 0→failure，其余 state→pending |
| `sections.retrospective.data.items[]` | array | 是 | 最近 5 条 finished run：`{task_id, task_title, run_id, exit_code, finished_at, result_excerpt}`，按 `finished_at` 倒序 |
| `sections.retrospective.data.reviews[]` | array | 是 | **P3 新增**：最近 5 条 closed review：`{id, topic, ended_at, decision_count, action_item_count, converted_count}` |
| `sections[x].status` | string | 是 | `ok` / `degraded`（单区异常仅该区 degraded，接口不 500） |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| tasks/retrospective 构建抛异常 | 200 | 无 detail——该区 `status=degraded` + `reason`（异常类名/`timeout`） |
| 其余区块异常 | 200 | 同上，按区隔离（P0 FR-4 机制） |

#### 示例

```http
GET /api/v1/ideas/27060d9b/cockpit
```
```json
// 200（tasks 区节选：三层链 A←B←C，含环时 graph=null）
{"sections": {"tasks": {"status": "ok", "data": {
  "items": [{"id": "a1", "title": "定方案", "kind": "direct", "downstream": false, "blocked": false, "stage": "done", "state": "completed"},
            {"id": "b2", "title": "写接口", "kind": "upstream", "downstream": false, "blocked": false, "stage": "review", "state": "running"},
            {"id": "c3", "title": "联调", "kind": "downstream", "downstream": true, "blocked": true, "stage": "ready", "state": "queued"}],
  "graph": {"nodes": [{"id": "a1", "title": "定方案", "kind": "direct"}], "edges": [{"from": "b2", "to": "a1"}]},
  "has_cycle": false, "cycles": [], "truncated": false,
  "upstream_total": 1, "downstream_total": 1, "warning": null, "total": 3, "folded": false}}},
 "retrospective": {"status": "ok", "data": {
  "summary": {"success": 2, "failure": 1, "pending": 1, "total": 4},
  "items": [{"task_id": "a1", "task_title": "定方案", "run_id": "f00d", "exit_code": 0,
             "finished_at": "2026-09-27T10:00:00", "result_excerpt": "done ok"}],
  "reviews": [{"id": "d1ab", "topic": "方案终审", "ended_at": "2026-09-27T09:00:00",
               "decision_count": 2, "action_item_count": 3, "converted_count": 1}]}}}
```
```json
// 200（含环：graph=null + warning + cycles 环路径）
{"sections": {"tasks": {"status": "ok", "data": {
  "graph": null, "has_cycle": true, "cycles": [["b2", "c3"]],
  "warning": "检测到依赖环，已降级为任务列表"}}}}
```

### GET /api/v1/ideas/{idea_id}/assumption-links

新增读端点（FR-28③）：返回该想法全部 `idea_assumption_link` 行，按 `hypothesis_id` 排序。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| （body） | — | — | — | 无请求体（GET） |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `idea_id` | string | 是 | 回显路径参数 |
| `links[].id` | integer | 是 | 行主键（自增） |
| `links[].idea_id` / `links[].hypothesis_id` | string | 是 | 唯一约束 `(idea_id, hypothesis_id)` |
| `links[].status` | string | 是 | 默认 `unverified`；PATCH 回写后为回写值 |
| `links[].note` / `links[].confirmed_by` | string | 是 | 无值为 `""` |
| `links[].updated_at` | string\|null | 是 | ISO8601 naive UTC；迁移建行取迁移时刻 |
| `total` | integer | 是 | 行数（= `links` 长度） |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| 无关联假设 | 200 | 无错误——`links: []` + `total: 0`（空态非异常） |

#### 示例

```http
GET /api/v1/ideas/27060d9b/assumption-links
```
```json
// 200
{"idea_id": "27060d9b",
 "links": [{"id": 7, "idea_id": "27060d9b", "hypothesis_id": "hyp-7f3a",
            "status": "validated", "note": "双源压测通过", "confirmed_by": "local",
            "updated_at": "2026-09-27T10:00:00"}],
 "total": 1}
```
```json
// 404
{"detail": "idea not found"}
```

### POST /api/v1/ideas/{idea_id}/hypotheses/import

P3 行为变更：原逻辑（集合合并 + IdeaChange）之外**同事务建关联表行**（已有行跳过、不覆盖回写字段）。签名、参数、响应结构与 P1 **完全一致**。FR-28②。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| `ids` | body | array[string] | 是 | 约束同 P1：形状非法 422、未知 id 422、`[]` no-op |
| `change_reason` | body | string | 否 | 写入 IdeaChange.reason，默认 `""` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean | 是 | 恒 `true`（与 P1 一致） |
| `added` | array[string] | 是 | 本次净新增引用 |
| `hypotheses` | array[string] | 是 | 导入后的完整引用列表 |
| `idea` | object | 是 | 完整 idea 资源（含 `version`） |

> 响应中**不包含**关联行——关联行经 `GET .../assumption-links` 读（契约不扩响应，P1 逐键一致）。

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| ids 非数组/含非字符串 | 422 | `ids must be a list of non-empty strings` |
| 想法不存在 | 404 | `idea not found` |
| Mio 不可用 | 503 | `Mio creativity unavailable, try again later` |
| id 不在 Mio 假设库 | 422 | `unknown hypothesis ids: ...` |

#### 示例

```http
POST /api/v1/ideas/27060d9b/hypotheses/import
{"ids": ["hyp-7f3a", "hyp-91cd"]}
```
```json
// 200（响应与 P1 逐键一致；同时 links 新增两行 status=unverified）
{"ok": true, "added": ["hyp-7f3a", "hyp-91cd"],
 "hypotheses": ["hyp-7f3a", "hyp-91cd"],
 "idea": {"id": "27060d9b", "hypotheses": ["hyp-7f3a", "hyp-91cd"], "version": 2}}
```

### PATCH /api/v1/ideas/{idea_id}/assumptions/{hid}

P3 行为变更：原 JSON 缓存回写之外**同事务 upsert 关联行**（status/note/confirmed_by/updated_at）。签名、参数、响应结构与 P1 完全一致（隐藏在 POST/GET 后的存储演进）。FR-28②。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| `hid` | path | string | 是 | 必须命中 `assumptions[]` 条目 `hid`（或 `id`）键，否则 404，不静默创建 |
| `status` / `note` / `confirmed_by` | body | string | 否 | 至少传一个，否则 422；仅校验字符串 |
| `change_reason` | body | string | 否 | 写入 IdeaChange.reason |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| （body） | object | 是 | 更新后的完整 idea 资源（与 P1 一致） |
| `assumptions[]` | array | 是 | 被改条目仅覆盖传入字段（P1 语义不变） |
| `version` | integer | 是 | 真实变更 +1；no-op 重放不变 |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| hid 不存在 | 404 | `assumption not found` |
| 三字段都没传 | 422 | `nothing to update (status/note/confirmed_by)` |
| 字段非字符串 | 422 | `status must be a string` |

#### 示例

```http
PATCH /api/v1/ideas/27060d9b/assumptions/hyp-7f3a
{"status": "validated", "note": "双源压测通过", "confirmed_by": "local"}
```
```json
// 200（响应与 P1 逐键一致；links 同行 updated_at 刷新）
{"id": "27060d9b", "assumptions": [{"hid": "hyp-7f3a", "text": "用户愿意授权",
  "status": "validated", "note": "双源压测通过", "confirmed_by": "local"}], "version": 3}
```

### PATCH /api/v1/ideas/{idea_id}

P3 行为变更：当 diff 涉及 `hypotheses`（含解除关联）或 `assumptions` 时，提交前 `_sync_link_rows` 对关联表做集合 reconcile（新增 hid 建行、移除 hid 删行、保留行回写字段不覆盖）。端点签名与响应与 P0/P1 完全一致。FR-28②。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| `hypotheses` | body | array[string]\|null | 否 | 约束同 P1（元素须字符串，`null` 清空） |
| 其余白名单字段 | body | 见 P0 契约 | 否 | 键缺失=不修改 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| （body） | object | 是 | 更新后的完整 idea 资源（与 P0/P1 一致） |
| `version` | integer | 是 | 真实变更 +1 |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| hypotheses 类型非法 | 422 | `hypotheses must be a list of strings` |
| 其余校验失败 | 422 | FastAPI `detail` 提示 |

#### 示例

```http
PATCH /api/v1/ideas/27060d9b
{"hypotheses": ["hyp-7f3a"]}
```
```json
// 200（hyp-91cd 被解除关联 → 其 link 行删除；响应与 P1 逐键一致）
{"id": "27060d9b", "hypotheses": ["hyp-7f3a"], "version": 4}
```

## 附录

### FR 追溯

| FR | 本文落点 |
|---|---|
| FR-26 | §接口明细 `GET .../cockpit`（tasks data 新增键）、§枚举全集 `kind`、§超时与并发节点上限 |
| FR-27 | §接口明细 `GET .../cockpit`（retrospective `summary/items/reviews`）、§时间约定排序归一 |
| FR-28 | §接口明细 `GET .../assumption-links` + import/PATCH/idea 三端点「行为变更」节、§幂等性与重试 |
| FR-29 | §兼容性与废弃策略、§变更记录（设计稿 v1.3 裁决条目引用） |

## 变更记录

| 版本 | 日期 | 变更 | 作者 |
|---|---|---|---|
| v1.0 | 2026-09-27 | 初版：cockpit tasks/retrospective 只增键、`GET .../assumption-links` 新增、import/PATCH/idea 双写行为变更、成功标准结构化不纳入（FR-29 裁决） | opencode |
