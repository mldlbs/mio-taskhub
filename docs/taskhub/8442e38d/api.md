# API 设计 — 想法落地闭环 P1 假设关联（task 8442e38d）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 契约版本 | idea-landing-p1 |
| 状态 | approved |
| 最后更新 | 2026-09-27 18:08 |
| 负责人 | opencode（mio-taskhub） |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.2 冻结·包 B） |
| 需求基线 | docs/taskhub/requirement-idea-landing-p1.md（FR-11~FR-17） |
| 关联任务 | 8442e38d（branch task-8442e38d） |
| 前序契约 | docs/taskhub/2fe56e6a/api.md（P0，idea-landing-p0，本文只写 P1 新增/变更） |

## 文档信息与范围

本文是 P1「想法落地闭环·包 B 假设关联」**新增/变更接口**的契约文档，适用于本机单用户部署的 mio-taskhub Web 客户端。P0 端点的既有语义（POST/PATCH 基础字段、dismiss、任务图等）以 P0 契约为准，本文只描述 P1 引入的差异。

**明确不含**（防止被当成全量接口清单）：

- P0 既有端点全量语义（见 docs/taskhub/2fe56e6a/api.md）；
- 分数双向同步接口：不存在——分数真源=Mio，taskhub 只读拉取（NFR-4，无任何回写 Mio 的端点）；
- 假设独立关联表 `idea_assumption_link`（P3 视用法启动）；
- P2 结构化评审、P3 任务拓扑/复盘接口。

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
| 401 与 403 | **本组接口不返回 401/403**（无认证态可言、无越权概念）；若未来挂反向代理引入认证，401=未认证、403=已认证但无权，业务码分别为 `unauthenticated` / `forbidden` |

## 通用请求头

| 头 | 值 | 必填 | 说明 |
|---|---|---|---|
| Content-Type | `application/json; charset=utf-8` | 带请求体时必填 | 缺失时 FastAPI 按 JSON 解析失败 → 422 |
| Accept | `application/json` | 否 | 响应恒为 JSON（UTF-8） |

## 通用响应结构

本组接口为 **REST 直出式**：成功响应即资源本身，**无统一 `{code,msg,data}` 外壳**（与 P0 一致的显式例外）。逐端点响应字段见 §接口明细。

失败响应统一外壳：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | string | 是 | 人类可读错误说明（FastAPI `HTTPException` 惯例） |

- 导入端点返回包装体 `{ok, added, hypotheses, idea}`（`ok` 恒 `true`，`added` 为本次净新增）——属操作结果而非资源本身，故带外壳字段；
- `GET .../cockpit` 返回顶层对象（`sections` 为 map 而非数组）；跨服务失败**不改结构**：仍 200，仅该区 `status=degraded`。

## 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `idea_not_found` | 想法不存在 | 路径 `idea_id` 在 ideas 表无记录 | 核对 id；列表接口重新获取 |
| 404 | `assumption_not_found` | 单条假设不存在 | `assumptions` 中无匹配 `hid` 的条目（**不静默创建**） | GET idea 核对现有 hid 后重试 |
| 422 | `ids_malformed` | 导入 ids 形状非法 | `ids` 非数组，或含非字符串/空串元素 | 改为 `["hyp-id", ...]` |
| 422 | `unknown_hypothesis_ids` | 引用了 Mio 不存在的 id | 导入 ids 不在当前假设库（`unknown hypothesis ids: x, y`） | 调 `GET /mio/ferment` 刷新列表后重选 |
| 422 | `hypotheses_not_string_list` | hypotheses 字段类型违反约束 | POST/PATCH 传 `hypotheses` 非数组或元素非字符串 | 改为字符串数组（如 `["hyp-1"]`），清空用 `null` |
| 422 | `nothing_to_update` | 回写 body 无有效字段 | `PATCH .../assumptions/{hid}` 未传 `status`/`note`/`confirmed_by` 任一 | 至少传一个字段 |
| 422 | `field_not_string` | 回写字段类型错误 | `status`/`note`/`confirmed_by` 传入非字符串 | 改为字符串 |
| 422 | `validation_error` | 其余请求体校验失败 | FastAPI/pydantic 模型校验不通过 | 按 `detail` 提示修正参数 |
| 503 | `mio_unavailable` | Mio creativity 不可用 | 导入是显式动作：runtime 目录缺失、CLI 调用失败/超时（`Mio creativity unavailable, try again later`） | 稍后重试；确认 `mio` CLI 可用 |
| 200（区内降级） | `section_degraded` | 驾驶舱单区降级 | Mio 失败时 cockpit 仍 200，仅 `sections.hypotheses.status=degraded` | 见 §超时与并发；其余区块不受影响 |
| 500 | `internal_error` | 服务端异常 | 非预期错误（本组接口设计上不应出现） | 重试一次；仍失败查 hub 日志 |

## 字段命名与类型规范

| 项 | 约定 | 反例 |
|---|---|---|
| 命名 | `snake_case` | `hypId` ✗ → `hyp_id` ✓ |
| ID | idea/任务为 8 位十六进制；Mio hypothesis id 为 Mio 侧原样字符串（`mio-hyp` 命名空间，taskhub 不再造 id） | taskhub 自造假设 id ✗ |
| 布尔 | JSON `true/false`，无 `"0"/"1"` | `"broken": "1"` ✗ |
| 数组 | JSON 数组；空数组 `[]` 表示「显式清空」 | 逗号分隔字符串 ✗ |
| 枚举值 | 小写蛇形（`mio_timeout`） | 中文/驼峰 ✗ |

**字段缺失 vs 显式 null**（PATCH 语义，调用方最易踩；`hypotheses` 为 P1 新列，其余同 P0）：

| 传入 | 字符串字段（goal 等） | JSON 字段（assumptions/risks/tags/hypotheses） |
|---|---|---|
| 键缺失 | 不修改 | 不修改 |
| `""` | 清空为默认 `""` | 非法 → 422（须传数组或 null） |
| `null` | 视同不修改（模型默认 `""`） | 清空（存储为 `NULL`，读出 `null`） |
| `[]` | 非法 → 422 | 清空（存储 `[]`，读出 `[]`） |

`hypotheses` 附加约束：元素必须为字符串，否则 422（`hypotheses must be a list of strings`）。

## 枚举全集

| 枚举 | 取值全集 |
|---|---|
| `sections` 键（7 区） | `goal`, `hypotheses`, `mvp`, `tasks`, `risks`, `approvals`, `retrospective` |
| `sections[x].status` | `ok`, `degraded` |
| `sections.hypotheses.reason`（P1 降级原因） | `mio_timeout`（CLI 失败/调用抛异常）、`mio_unavailable`（runtime 目录缺失）、`timeout`（区块 3s 预算）、`total_timeout`（总预算 5s 裁剪）、其余为异常类名兜底 |
| `sections.hypotheses.data.source` | `mio`（有引用且拉取成功）、`none`（无引用，不打 Mio） |
| hypothesis `status`（Mio 侧，只读透传） | `draft`, `active`, `validated`, `rejected` |
| 本地假设 `status`（回写允许值，非服务端强枚举） | `open`, `active`, `validated`, `rejected`（前端下拉全集；服务端仅校验字符串） |
| `idea.status`（既有） | `new`, `fermenting`, `formed`, `broken_down`, `archived`, `cancelled` |

## 幂等性与重试

| 接口 | 幂等 | 幂等键/行为 | 重复提交返回 |
|---|---|---|---|
| POST .../hypotheses/import | 是 | 集合合并：已关联 id 不重复写入（`added=[]` 时不 bump 版本、不追加 diff） | 200 + `added: []` + 当前 `hypotheses` |
| PATCH .../assumptions/{hid} | 是 | 同 payload 重放：`patch` 值与现值全同 → 不 bump 版本、不追加 diff | 200 + 最新资源 |
| PATCH /ideas/{id}（解除关联） | 是 | 传完整目标 `hypotheses` 数组，last-write-wins | 200 + 最新资源 |
| GET .../cockpit | 是 | 纯读，无副作用；缓存命中不重复跨服务调用 | 200 + 当前聚合 |
| GET /mio/ferment | 是 | 纯读（每请求实时拉 Mio，不缓存——缓存由 cockpit 假设区自持） | 200 + 当前列表 |

**可安全重试**：以上全部。**建议退避**：`503`（Mio 不可用）指数退避（2s/4s/8s），`500` 指数退避（1s/2s/4s），`4xx` 不重试。

## 限流与配额

本机单用户无网关限流，接口不设配额。前端约束：导入弹层按需打开（每次实时拉 `GET /mio/ferment`）；cockpit 进详情页拉一次 + 手动刷新，避免轮询（总预算 5s，重复并发会浪费 SQLite 连接与 Mio 调用）。

## 超时与并发

| 项 | 值 |
|---|---|
| cockpit 假设区跨服务超时 | **3s**（`SECTION_TIMEOUTS["hypotheses"]=3.0`；内部 CLI 调用预算 2.5s，先于区块预算失败 → `mio_timeout`） |
| 假设区结果缓存 | **5min TTL**，进程内缓存，key=关联 hyp id 集合；命中带 `cached_at`，重启即失效属预期（NFR-2） |
| 导入端点跨服务超时 | 10s（显式动作，允许比展示更长；失败 → 503 而非静默） |
| cockpit 其余区超时 / 总预算 | 1s / 5s（P0 不变，超时返回已就绪部分） |
| 并发模型 | SQLite WAL；`POST .../hypotheses/import` 与 `PATCH .../assumptions/{hid}` 的读-改-写在**进程内互斥锁**串行化 → 并发两发均进 diff、无丢更新（FR-15） |
| 其余 PATCH | last-write-wins，无乐观锁（单用户可接受，P0 契约不变） |

## 分页

**本组接口不涉及分页**：导入列表单次最多 100 条（Mio 假设库 `limit=100` 上限）；cockpit 为单资源聚合；回写为单条。若后续假设量级超限，沿用既有 `?limit&offset` 风格，不在本契约内。

## 时间 / 数值 / 空值约定

| 项 | 约定 |
|---|---|
| 时间 | ISO8601 UTC；`sections.hypotheses.cached_at` 形如 `2026-09-27T06:00:00+00:00`（带偏移）；前端 `parseUtc` 对 naive/aware 两种形态均可解析 |
| 数值 | 三元分 `novelty`/`feasibility`/`impact` 为整数（Mio 原样透传，可能为 `null` 表示 Mio 未评）；`score` 为汇总分 |
| 空值 | 字符串空=`""`；JSON 空=`null`（与 `[]` 区别见「字段命名与类型规范」） |
| 断链条目 | `{id, broken: true}` **只有两个字段**：分数/标题在 Mio 侧已不存在，无法透传（前端灰显「已失效」） |
| 缺失字段 | PATCH 中键缺失=不修改 |

## 文件上传与下载

不适用：本组接口无文件上传/下载（假设为 Mio 侧结构化对象，taskhub 只存 id 引用）。

## 安全与脱敏

| 项 | 说明 |
|---|---|
| 网络暴露 | 仅绑定 127.0.0.1，不出网；Mio 调用为本机 CLI 子进程 |
| 分数只读（NFR-4） | 代码路径中**不存在**任何向 Mio 写分数/状态的调用；回写仅限 taskhub 本地 `assumptions` 字段且必须人工触发（前端带确认弹窗） |
| 敏感数据 | 假设文本/备注无凭证/PII；`confirmed_by` 为自由字符串（本机默认 `local`） |
| 日志 | 错误 `detail` 可含字段名与未知 id 列表，不记录假设正文全文 |
| 注入 | 全部参数经 pydantic 校验 + SQLAlchemy 绑定参数，无字符串拼接 SQL；`assumptions[hid]` 键名来自路径参数且经 dict 查找，不拼进 SQL |

## 版本策略

| 项 | 约定 |
|---|---|
| 路径版本 | `/api/v1`；破坏性变更（改字段类型/删字段/改语义）→ 新增 `/api/v2` 并行 |
| 字段演进 | `hypotheses` 为可空新列（NFR-1：旧数据零迁移）；只增不删，旧客户端零破坏 |
| 契约版本 | `idea-landing-p1`；语义变更时递增并更新「变更记录」 |

## 兼容性与废弃策略（FR-17）

- 旧 idea 数据（`hypotheses` 为 `NULL`）在 POST 读取、PATCH、cockpit 全路径正常返回 `[]`，假设区 `source=none` 空态；
- P0 全部行为（下一步动作、任务图、高风险判定、`mode=free` 讨论、既有编辑评审）不受 P1 影响——全量 pytest 回归覆盖（基线 885 例只增不减）；
- cockpit `sections.hypotheses.data` 由 P0 的 `{items:[], source:"stub"}` 演进为真实聚合（`source` 枚举值变更：`stub` → `mio`/`none`），P0 契约未冻结该值，前端以 `status` 判定为准；
- 废弃流程：先在文档标 deprecated 并保留 ≥1 个发布周期，服务端对旧字段继续读写，再随大版本移除。

## 接口清单

| 方法 | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|---|---|---|---|---|---|---|
| POST | `/api/v1/ideas/{idea_id}/hypotheses/import` | 从 Mio 发酵假设导入 id 引用（FR-12） | 无鉴权 | 是 | 无 | 引用 +1，进 IdeaChange |
| PATCH | `/api/v1/ideas/{idea_id}/assumptions/{hid}` | 单条本地假设人工回写（FR-15） | 无鉴权 | 是 | 无 | 进 IdeaChange（键 `assumptions[hid]`） |
| PATCH | `/api/v1/ideas/{idea_id}` | 更新 `hypotheses`（含解除关联，FR-11/FR-14） | 无鉴权 | 是 | 无 | 进 IdeaChange（键 `hypotheses`） |
| GET | `/api/v1/ideas/{idea_id}/cockpit` | 驾驶舱聚合（P1：假设区真实数据，FR-13/14/16） | 无鉴权 | 是 | 无（建议手动刷新） | 只读 + 5min 缓存 |
| GET | `/api/v1/mio/ferment` | 发酵假设列表（导入弹层数据源，FR-12 复用） | 无鉴权 | 是 | 无 | 只读（实时拉 Mio） |

## 接口明细

### POST /api/v1/ideas/{idea_id}/hypotheses/import

从 Mio 发酵假设库导入引用：集合合并去重、幂等；未知 id 422；Mio 不可用 503（显式动作不静默）。FR-11、FR-12。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404（校验顺序：422 形状 → 404 → 503 → 422 未知 id） |
| `ids` | body | array[string] | 是 | 非数组或含非字符串/空串元素 → 422；`[]` → 幂等 no-op（200） |
| `change_reason` | body | string | 否 | 写入 IdeaChange.reason，默认 `""` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `ok` | boolean | 是 | 恒 `true` |
| `added` | array[string] | 是 | 本次净新增（去重后、原已关联的不计） |
| `hypotheses` | array[string] | 是 | 导入后的完整引用列表（合并保序：原序在前、新增追加） |
| `idea` | object | 是 | 完整 idea 资源（含 `version`，导入 bump +1） |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| ids 非数组/含非字符串 | 422 | `ids must be a list of non-empty strings` |
| 想法不存在 | 404 | `idea not found` |
| Mio 不可用（runtime 缺失/CLI 失败） | 503 | `Mio creativity unavailable, try again later` |
| id 不在 Mio 假设库 | 422 | `unknown hypothesis ids: nope, foo` |

#### 示例

```http
POST /api/v1/ideas/27060d9b/hypotheses/import
{"ids": ["hyp-7f3a", "hyp-91cd", "hyp-7f3a"]}
```
```json
// 200（hyp-7f3a 重复输入被去重；假设库里有这两条）
{"ok": true, "added": ["hyp-7f3a", "hyp-91cd"],
 "hypotheses": ["hyp-7f3a", "hyp-91cd"],
 "idea": {"id": "27060d9b", "hypotheses": ["hyp-7f3a", "hyp-91cd"], "version": 2}}
```
```json
// 503（Mio 不可用）
{"detail": "Mio creativity unavailable, try again later"}
```

### PATCH /api/v1/ideas/{idea_id}/assumptions/{hid}

单条本地假设人工回写：只改一条、不整列表覆盖；diff 键 `assumptions[hid]`；进程内锁串行化读-改-写（并发两发都进 diff、无丢更新）；值全同重放为 no-op。FR-15、NFR-4。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| `hid` | path | string | 是 | 必须命中 `assumptions[]` 中条目的 `hid`（或 `id`）键，否则 404，**不静默创建** |
| `status` | body | string | 否 | 与其余两字段至少传一个，否则 422；服务端仅校验字符串（建议 `open`/`active`/`validated`/`rejected`） |
| `note` | body | string | 否 | 备注文本 |
| `confirmed_by` | body | string | 否 | 回写人标识（本机前端默认 `local`） |
| `change_reason` | body | string | 否 | 写入 IdeaChange.reason，默认 `""` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| （body） | object | 是 | 更新后的**完整 idea 资源**（非 diff） |
| `assumptions[]` | array | 是 | 被改条目仅覆盖传入字段，其余属性（`hid`/`text` 等）与相邻条目原样保留 |
| `version` | integer | 是 | 真实变更 +1；no-op 重放不变 |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| hid 不存在 | 404 | `assumption not found` |
| 三个字段都没传 | 422 | `nothing to update (status/note/confirmed_by)` |
| 字段非字符串 | 422 | `status must be a string` |

#### 示例

```http
PATCH /api/v1/ideas/27060d9b/assumptions/h1
{"status": "validated", "note": "双源压测通过", "confirmed_by": "local"}
```
```json
// 200（IdeaChange 追加 {"assumptions[h1]": {"old": {...}, "new": {...}}}）
{"id": "27060d9b", "assumptions": [{"hid": "h1", "text": "用户愿意授权",
  "status": "validated", "note": "双源压测通过", "confirmed_by": "local"}], "version": 3}
```
```json
// 404（hid 不在）
{"detail": "assumption not found"}
```

### PATCH /api/v1/ideas/{idea_id}

P1 变更：`hypotheses` 加入 JSON 白名单（FR-11）；**解除关联**（FR-14）经本端点传完整目标数组。其余语义同 P0 契约（docs/taskhub/2fe56e6a/api.md）。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| `hypotheses` | body | array[string]\|null | 否 | 元素须为字符串否则 422；`null`/`[]` 清空；键缺失不修改 |
| 其余 body 键 | body | 混合 | 否 | 白名单外键忽略；同 P0 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `hypotheses` | array[string] | 是 | 更新后引用列表（旧数据 NULL 读出 `[]`） |
| `version` | integer | 是 | 归一后有变化才 +1（P0 版本语义不变） |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| hypotheses 非数组 | 422 | `hypotheses must be a list` |
| 元素非字符串 | 422 | `hypotheses must be a list of strings` |

#### 示例

```http
PATCH /api/v1/ideas/27060d9b
{"hypotheses": ["hyp-91cd"], "change_reason": "解除已失效的 hyp-7f3a"}
```
```json
// 200（IdeaChange 追加 {"hypotheses": {"old": ["hyp-7f3a","hyp-91cd"], "new": ["hyp-91cd"]}}）
{"id": "27060d9b", "hypotheses": ["hyp-91cd"], "version": 4}
```

### GET /api/v1/ideas/{idea_id}/cockpit

P1 变更：`sections.hypotheses` 由空壳接真实聚合——对每个引用 id 经 Mio creativity 拉三元分与 status；断链标 `broken`；3s 超时 + 5min 缓存 + 区块级 degraded。FR-13、FR-14、FR-16。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 |
|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 |
| `user` | query | string | 否 | 默认 `local`（同 P0，决定 dismiss 过滤） |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `sections.hypotheses.status` | string | 是 | `ok` / `degraded`（仅本区降级，整包仍 200） |
| `sections.hypotheses.reason` | string | 否 | degraded 时出现，枚举见 §枚举全集（如 `mio_timeout`） |
| `sections.hypotheses.cached_at` | string | 否 | 假设区结果缓存时间（ISO8601 UTC，TTL 5min 内命中仍携带） |
| `...data.source` | string | 是 | `mio` / `none` |
| `...data.total` | integer | 是 | 条目数（= 关联引用数） |
| `...data.items[]` | array | 是 | 每项见下表；保 idea 关联顺序 |
| `...data.items[].id` | string | 是 | Mio hypothesis id |
| `...data.items[].broken` | boolean | 是 | `true` = Mio 侧已删除/查不到（断链） |
| `...data.items[].title/status` | string | 否 | 非断链条目：Mio 原样透传（`broken` 条目无此字段） |
| `...data.items[].novelty/feasibility/impact/score` | integer\|null | 否 | 三元分 + 汇总分，只读；`null` = Mio 未评 |
| `...data.items[]`（断链） | object | — | 仅 `{id, broken: true}`——分数无法透传，前端灰显「已失效」+ 提供解除关联 |
| 其余 6 区 / `next_action` / `high_risk` | 见 P0 契约 | 是 | P1 不变 |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| 想法不存在 | 404 | `idea not found` |
| Mio CLI 失败/异常 | 200 | 不报错：`sections.hypotheses` = `degraded` + `reason=mio_timeout` |
| Mio runtime 缺失 | 200 | `degraded` + `reason=mio_unavailable` |
| 假设区超 3s / 总预算裁剪 | 200 | `degraded` + `reason=timeout` / `total_timeout` |

#### 示例

```http
GET /api/v1/ideas/27060d9b/cockpit
```
```json
// 200
{"sections": {
  "goal": {"status": "ok", "data": {"goal": "..."}},
  "hypotheses": {"status": "ok", "cached_at": "2026-09-27T06:00:00+00:00",
    "data": {"source": "mio", "total": 2, "items": [
      {"id": "hyp-7f3a", "title": "用户愿意授权", "status": "active",
       "novelty": 9, "feasibility": 8, "impact": 7, "score": 8.0, "broken": false},
      {"id": "hyp-gone", "broken": true}]}},
  "mvp": {"status": "ok"}, "tasks": {"status": "ok"}, "risks": {"status": "ok"},
  "approvals": {"status": "ok"}, "retrospective": {"status": "ok"}},
 "idea": {"id": "27060d9b", "hypotheses": ["hyp-7f3a", "hyp-gone"]},
 "next_action": null, "high_risk": false}
```
```json
// 200（Mio 超时：仅假设区灰，其余照常）
{"sections": {"hypotheses": {"status": "degraded", "reason": "mio_timeout"},
  "goal": {"status": "ok"}, "...": "..."}, "next_action": null, "high_risk": false}
```

### GET /api/v1/mio/ferment

导入弹层的列表数据源（FR-12 复用既有端点）：返回 Mio 发酵假设全量列表（含三元分与状态）+ taskhub 侧映射/已关联标注。只读实时拉取（缓存属 cockpit 假设区自持，本端点不缓存）。

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约定 |
|---|---|---|---|---|
| `limit` | query | integer | 否 | 默认 50，范围 1~100 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `available` | boolean | 是 | Mio 可用性；`false` 时 `items` 为空（前端显示「Mio 暂无 active 假设」） |
| `mapping` | object | 是 | Mio status → taskhub idea status 建议映射（只读展示） |
| `items[]` | array | 是 | 假设列表 |
| `items[].id` | string | 是 | hypothesis id（导入端点接受的正是此值） |
| `items[].title/status` | string | 是 | 标题 / Mio 状态（`draft`/`active`/`validated`/`rejected`） |
| `items[].novelty/feasibility/impact/score` | integer\|null | 否 | 三元分 + 汇总分 |
| `counts` | object | 是 | `{total, linked, pending_actions}` 计数 |

#### 错误码

| 场景 | HTTP | detail 片段 |
|---|---|---|
| `limit` 越界 | 422 | pydantic 校验信息 |
| Mio 不可用 | 200 | 不报错：`available=false` + `items=[]`（列表是浏览动作，降级不 503；503 只留给导入这种显式写动作） |

#### 示例

```http
GET /api/v1/mio/ferment?limit=50
```
```json
// 200
{"available": true, "mapping": {"draft": "new", "active": "fermenting",
  "validated": "formed", "rejected": "cancelled"},
 "items": [{"id": "hyp-7f3a", "title": "用户愿意授权", "status": "active",
   "novelty": 9, "feasibility": 8, "impact": 7, "score": 8.0,
   "suggested_status": "fermenting", "linked": null, "action": null}],
 "counts": {"total": 12, "linked": 2, "pending_actions": 1}}
```

## 附录

### FR 追溯

| FR | 端点/章节 |
|---|---|
| FR-11 | §字段命名（hypotheses 约束）、PATCH /ideas/{id} 明细、导入明细（diff 键 `hypotheses`） |
| FR-12 | POST .../hypotheses/import 明细 + GET /mio/ferment 明细（数据源复用、幂等、503） |
| FR-13 | GET .../cockpit 明细（三元分 + status 徽章字段、只读） |
| FR-14 | GET .../cockpit 明细（`broken` 条目）+ PATCH /ideas/{id} 明细（解除关联） |
| FR-15 | PATCH .../assumptions/{hid} 明细 + §超时与并发（锁、no-op 幂等、404/422） |
| FR-16 | cockpit 明细 + §超时与并发（3s / 5min / `cached_at` / 区块级 degraded） |
| FR-17 | §兼容性与废弃策略（P0 回归不变、`source` 枚举演进） |

## 变更记录

| 日期 | 契约版本 | 变更内容 | 兼容/破坏 | 影响方 |
|---|---|---|---|---|
| 2026-09-27 | idea-landing-p1 v1.0 | P1 初版：假设导入/单条回写/cockpit 假设区契约、3s+5min 降级契约、断链语义 | 兼容（新增端点 + 可空新列 `hypotheses`，P0 行为不变） | Web 客户端假设区与导入弹层；无外部调用方 |
