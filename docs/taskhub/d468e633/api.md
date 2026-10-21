# 接口契约 · 窄门禁（空 diff 拦截）

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.1 |
| 契约版本 | 1.0（**破坏性**：新增 422 错误码 `run.empty_diff`） |
| 状态 | approved |
| 最后更新 | 2026-10-20 16:32 |
| 负责人 | 项目作者（单人） |
| 关联任务 | d468e633 |
| 适用范围 | 本次变更涉及的 4 个接口 + 1 个 WS 事件 |

## 文档信息与范围

本文档描述「窄门禁」引入的**契约变更**，供 Agent 端（submit_result 调用方）与前端联调使用。

**明确不含**：

- 不含全量接口清单——mio-taskhub 的完整 REST 面（157 路由）不在本文档范围。
- 不含既有接口中与本次变更无关的字段。
- 不含测试执行器相关契约（本期不实现）。

**变更摘要**：

| 编号 | 变更 | 兼容性 |
|---|---|---|
| C-1 | `POST /runs/{run_id}/result` 新增 422 错误码 `run.empty_diff` | **破坏性**（成功路径新增失败可能） |
| C-2 | `GET /tasks/{task_id}` 的 `runs[]` 新增 3 个字段 | 兼容（新增字段） |
| C-3 | `PATCH /tasks/{task_id}` 新增可编辑字段 `diff_gate_exempt` | 兼容（新增可选字段） |
| C-4 | 新增 `GET /runs/{run_id}/diff-evidence` | 兼容（新增接口） |
| C-5 | 新增 WS 事件类型 `run.result_rejected` | 兼容（新增事件类型） |

## 环境与基础地址

| 环境 | Base URL | 说明 |
|---|---|---|
| 本地开发 | `http://127.0.0.1:48620/api/v1` | hub 默认端口 48620，单用户本地实例 |
| 本地冒烟 | `http://127.0.0.1:48621/api/v1` | 隔离库跑门控冒烟脚本 `.smoke_ws/gate_smoke.sh` 时使用 |

- 本接口是**本地回环**服务，不经网关，路径前缀 `/api/v1` 由应用自身挂载，不会被剥离或重写。
- 端口被占用时 hub 不会静默重绑到别的端口（见 `docs/taskhub/d468e633/requirement.md` 边界与约束）。

## 认证与鉴权

- **无认证**：hub 为本地单用户服务，不校验 token/cookie。
- **鉴权靠所有权**：`POST /runs/{run_id}/result` 支持可选查询参数 `agent`；传了则严格校验该 run 的持有者，不匹配返回 **403**（`run.not_owner`）；不传则跳过校验（兼容旧调用方，属已知限制）。
- 401 **不使用**——本服务无认证环节，不存在「未认证」态。403 表示「已识别调用方但越权」，业务码 `run.not_owner`。
- 事件与文档接口无独立鉴权，沿用回环可达性边界。

## 通用响应结构

成功与失败**共用同一外壳**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | string \| object | 失败时必填 | 失败详情；字符串为纯文本，对象为结构化业务码（见统一错误码表） |
| `code` | string | 结构化失败时必填 | 业务错误码，如 `run.empty_diff`；**注意：`detail` 为对象时 `code` 位于 `detail` 内** |
| `message` | string | 结构化失败时必填 | 人可读说明（三段式：发生了什么/为什么/下一步） |
| `hint` | string | 否 | 调用方可执行的下一步建议 |

- `data` 为 `null` 与 `data` 为 `{}` 语义不同：前者表示「查询成功但无该实体」，后者表示「实体存在但字段为空」。
- 本次涉及的接口**不返回裸数组**，也不返回二进制。

## 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 403 | `run.not_owner` | 越权操作他人 run | 传了 `agent` 且与 run 持有者不符 | 用正确的 agent 名重试 |
| 404 | — | run / task 不存在 | id 查不到 | 核对 id |
| 409 | `run.not_active` | run 已结束 | 对 `state=finished` 的 run 调 heartbeat | 结束即终态，不再操作 |
| 409 | `run.race_lost` | 并发操作竞争失败 | 心跳与超时回收同时发生，CAS 失败 | 重试一次 |
| 409 | `run.result_already_submitted` | 重复提交成功结果 | run 已 finished 且 `exit_code=0` 且任务已完成 | 无需重试 |
| 409 | `policy_risk_high` | 危险操作需确认 | 破坏性操作命中历史风险策略 | 带 `confirm=true` 重试 |
| **422** | **`run.empty_diff`** | **声明成功但本次零改动** | **`success=true` 且双快照判定无改动且未豁免** | **先在工作区实际完成改动再提交；确无代码产出则置 `diff_gate_exempt=true`** |
| 422 | — | ReadEvidence 门控未满足 | 本次 run 未读必读文档或版本已变 | 按 `detail.missing` / `detail.stale` 补读后重提 |

> 通用 422 文案「数据校验失败，请检查必填项」**不适用于** `run.empty_diff`，调用方必须按 `detail.code` 分支处理。

## 字段命名与类型规范

- 命名风格：snake_case；布尔用 `is_` 前缀（如 `is_exempt`）；无驼峰。
- 反例：`diffSummary`、`FilesChanged`、`changed-files` 一律不用。
- ID 一律 string（8 位十六进制任务 id 如 `d468e633`）。
- 「字段缺失」与「显式 null」语义不同：缺失 = 客户端未提供，服务端用默认值；`null` = 服务端明确表示无值。`diff_summary` 在未采集时为 `null`，**不是** `{}`。
- 计数类字段（`files_changed` / `insertions` / `deletions` / `rejections`）一律整数，最小 0，不返回字符串。
- 布尔字段（`changed` / `skipped` / `blocked` / `ok`）一律 JSON 布尔，不用 0/1。

## 幂等性与重试

| 接口 | 幂等 | 幂等键 | 重复调用返回 |
|---|---|---|---|
| `POST /runs/{run_id}/result` | 非幂等 | — | 成功结果重复提交 → 409 `run.result_already_submitted` |
| `GET /tasks/{task_id}` | 幂等（只读） | — | 始终返回当前快照 |
| `PATCH /tasks/{task_id}` | 幂等（同值覆盖） | — | 始终返回更新后实体 |
| `GET /runs/{run_id}/diff-evidence` | 幂等（只读） | — | 始终返回当前判定 |

- **可安全重试**：所有 GET；以及 422 `run.empty_diff`（这是本次设计的核心——被拒后 Agent 改完可重提）。
- **绝不可重试**：409 `run.result_already_submitted`（重试无意义且会二次触发状态流转）。
- **退避策略**：被拒后重试无需退避（同一 run 内，attempt 不增加）；但连续被拒达 2 次后本次执行判失败，重试间隔由既有指数退避（`BASE_RETRY_SECONDS=2.0`，`api/runs.py:103-108`）接管。
- 幂等键说明：本接口不使用请求头幂等键，run_id 本身即天然幂等键。

## 接口清单

| Method | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|---|---|---|---|---|---|---|
| POST | `/api/v1/runs/{run_id}/result` | Agent 提交执行结果（**新增 422 拦截**） | run 持有者（可选 agent 校验） | 非 | 不限流 | claimed/running → finished |
| GET | `/api/v1/tasks/{task_id}` | 任务详情（**runs[] 新增 3 字段**） | 无（本地） | 是 | 不限流 | 任意 |
| PATCH | `/api/v1/tasks/{task_id}` | 更新任务（**新增 diff_gate_exempt**） | 无（本地） | 是 | 不限流 | 任意 |
| GET | `/api/v1/runs/{run_id}/diff-evidence` | **新增**：查询本次 run 的改动证据与是否会拦 | 无（本地） | 是 | 不限流 | 任意 |

## 接口明细

### POST /api/v1/runs/{run_id}/result

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|---|---|---|---|---|---|---|
| `run_id` | path | string | 是 | — | 8 位十六进制 | `d468e633` |
| `agent` | query | string | 否 | — | 传了则必须是该 run 持有者，否则 403 | `claude-code` |
| `success` | body | bool | 否 | `true` | `true` 时会触发变更门禁与读证门控 | `true` |
| `result` | body | string | 否 | `""` | 执行结果正文，任意长度 | `已完成 useFacilityMap 染色` |
| `exit_code` | body | int | 否 | `success?0:1` | 显式传入以该值为准 | `0` |
| `progress` | body | int | 否 | — | 本接口内部固定置 100，不接受客户端值 | — |

> **注意**：Agent 上报的**任何改动信息都不参与判定**——判定证据由服务端自行采集。

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | run id |
| `state` | string | 是 | 提交后为 `FINISHED` |
| `result` | string | 是 | 回显提交的 result |
| `task_state` | string \| null | 是 | 任务状态；`null` 表示 task 缺失 |

#### 错误码

| HTTP | 业务码 | 含义 | 处理建议 |
|---|---|---|---|
| 403 | `run.not_owner` | 越权 | 核对 agent 名 |
| 404 | — | run 不存在 | 核对 id |
| 409 | `run.result_already_submitted` | 重复提交成功结果 | 不重试 |
| 422 | `run.empty_diff` | **声明成功但本次零改动** | 实际完成改动后重提；或置 `diff_gate_exempt=true` |
| 422 | — | 读证门控未满足 | 按 `detail.missing`/`detail.stale` 补读 |

#### 示例

请求：

```bash
curl -X POST "http://127.0.0.1:48620/api/v1/runs/d468e633/result?agent=claude-code" \
  -H "Content-Type: application/json" \
  -d '{"success": true, "result": "已完成染色改造"}'
```

被拦截时的响应（HTTP 422）：

```json
{
  "detail": {
    "code": "run.empty_diff",
    "message": "拒绝提交：声明 success=true，但本次 run 期间工作区零改动（0 文件 / 0 行）",
    "hint": "先在工作区实际完成改动（提交或保留改动均可）再 submit_result；若本任务确无代码产出，用 PATCH /tasks/{id} 设置 diff_gate_exempt=true。",
    "files_changed": 0,
    "rejections": 1,
    "max_rejections": 2
  }
}
```

### GET /api/v1/tasks/{task_id}

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|---|---|---|---|---|---|---|
| `task_id` | path | string | 是 | — | 8 位十六进制 | `d468e633` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 任务 id |
| `state` | string | 是 | 任务状态 |
| `runs` | array | 是 | 每次执行记录，按 `started_at` 倒序 |
| `runs[].id` | string | 是 | run id |
| `runs[].state` | string | 是 | run 状态 |
| `runs[].exit_code` | int \| null | 是 | 退出码；未结束为 null |
| `runs[].result` | string \| null | 是 | 自报结果正文 |
| `runs[].diff_summary` | object \| null | 是 | **新增**：改动证据；未采集为 `null`（非 `{}`） |
| `runs[].diff_summary.before` | object | 否 | 前快照 `{head, porcelain, files, insertions, deletions}` |
| `runs[].diff_summary.after` | object | 否 | 后快照，同上结构 |
| `runs[].diff_summary.changed` | bool | 否 | 是否判定为有改动 |
| `runs[].diff_summary.reason` | string | 否 | 判定原因码，如 `has_changes` / `empty_diff` / `no_workspace` |
| `runs[].annotations` | array | 是 | **新增**：标注列表，空数组表示无标注 |
| `runs[].annotations[].code` | string | 是 | 标注码，如 `run.empty_diff` / `no_workspace` |
| `runs[].annotations[].level` | string | 是 | `block` \| `warn` |
| `runs[].annotations[].message` | string | 是 | 人可读说明；`warn` 级须含「不影响放行」 |
| `runs[].annotations[].at` | string | 是 | ISO 时间戳 |
| `runs[].rejections` | int | 是 | **新增**：本 run 累计被拒次数，默认 0 |

#### 错误码

| HTTP | 业务码 | 含义 | 处理建议 |
|---|---|---|---|
| 404 | — | 任务不存在 | 核对 id |

#### 示例

```json
{
  "id": "d468e633",
  "state": "running",
  "runs": [
    {
      "id": "a1b2c3d4",
      "state": "FINISHED",
      "exit_code": 0,
      "result": "已完成染色改造",
      "diff_summary": { "before": {"head": "9f2c1ab", "porcelain": ""}, "after": {"head": "9f2c1ab", "porcelain": " M src/map.ts"}, "changed": true, "reason": "has_changes" },
      "annotations": [],
      "rejections": 0
    }
  ]
}
```

### PATCH /api/v1/tasks/{task_id}

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|---|---|---|---|---|---|---|
| `task_id` | path | string | 是 | — | 8 位十六进制 | `d468e633` |
| `diff_gate_exempt` | body | bool | 否 | `false` | **新增**；true 表示本任务不参与变更门禁 | `true` |
| `workspace` | body | string | 否 | 保持原值 | 设为空串即等价于豁免（无工作区） | `E:/work/code/agent-dev/mio-taskhub` |
| `title` / `description` / `labels` / `files` / `deliverables` | body | 各类型 | 否 | 保持原值 | 既有可编辑字段，语义不变 | — |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | string | 是 | 任务 id |
| `diff_gate_exempt` | bool | 是 | **新增**：当前豁免状态 |
| `doc_paths` | object | 是 | 文档登记表，不变 |

#### 错误码

| HTTP | 业务码 | 含义 | 处理建议 |
|---|---|---|---|
| 404 | — | 任务不存在 | 核对 id |
| 422 | — | 依赖环或非法引用（既有校验） | 按 `detail` 修正 `depends_on` |

#### 示例

```bash
curl -X PATCH "http://127.0.0.1:48620/api/v1/tasks/d468e633" \
  -H "Content-Type: application/json" \
  -d '{"diff_gate_exempt": true}'
```

```json
{ "id": "d468e633", "diff_gate_exempt": true }
```

### GET /api/v1/runs/{run_id}/diff-evidence

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|---|---|---|---|---|---|---|
| `run_id` | path | string | 是 | — | 8 位十六进制 | `a1b2c3d4` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `run_id` | string | 是 | run id |
| `task_id` | string | 是 | 所属任务 id |
| `mode` | string | 是 | 实际生效的开关值 `off` \| `warn` \| `block` |
| `would_block` | bool | 是 | 当前状态下再提交成功是否会被拒；供 Agent 提交前自查 |
| `skipped` | bool | 是 | 是否因豁免/无法采集而跳过 |
| `reason` | string | 是 | 原因码 |
| `files_changed` | int | 是 | 当前后快照的变更文件数 |
| `before` | object \| null | 是 | 前快照；未采集为 null |
| `after` | object \| null | 是 | 后快照；未采集为 null |
| `changed` | bool | 是 | 双快照比较结果 |

#### 错误码

| HTTP | 业务码 | 含义 | 处理建议 |
|---|---|---|---|
| 404 | — | run 不存在 | 核对 id |

#### 示例

```json
{
  "run_id": "a1b2c3d4",
  "task_id": "d468e633",
  "mode": "warn",
  "would_block": true,
  "skipped": false,
  "reason": "empty_diff",
  "files_changed": 0,
  "before": { "head": "9f2c1ab", "porcelain": "" },
  "after": { "head": "9f2c1ab", "porcelain": "" },
  "changed": false
}
```

## 事件与推送

WebSocket `/ws` 新增事件类型（其余不变）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | string | 固定 `task_update`（事件 `entity` 必须是 `task`） |
| `event.type` | string | 固定 `run.result_rejected` |
| `event.entity_id` | string | 任务 id |
| `event.run_id` | string | run id |
| `event.payload.code` | string | 固定 `run.empty_diff` |
| `event.payload.message` | string | 三段式文案 |
| `event.payload.rejections` | int | 累计被拒次数 |
| `event.payload.max_rejections` | int | 上限（2） |

## 变更记录

| 日期 | 契约版本 | 变更内容 | 兼容/破坏 | 影响方 |
|---|---|---|---|---|
| 2026-10-20 | 1.0 | 首次定义：新增 `run.empty_diff` 422 错误码、`GET /runs/{id}/diff-evidence`、WS 事件 `run.result_rejected`、`runs[]` 三字段、`diff_gate_exempt` 可编辑字段 | 破坏（成功路径新增失败可能） | Agent 端 submit_result 调用方、前端 TaskDetail |

## 版本策略

- 版本号放在**文档头**（契约版本 1.0），不放在 URL 路径或请求头——本服务为本地单用户，无多版本并存需求。
- 破坏性变更必须在本表追加一行并写明影响方与提前期。

## 通用请求头

| Header | 必填 | 类型 | 默认 | 说明 |
|---|---|---|---|---|
| `Content-Type` | 写操作必填 | string | — | `application/json` |
| `Accept` | 否 | string | `application/json` | 本服务只返回 JSON |

## 分页

本次涉及的接口**均不分页**：任务详情一次性返回其全部 runs（本地单用户，单任务 run 数量有界）。
既有列表接口的分页参数不变，不在本契约范围。

## 时间 / 数值 / 空值约定

- 时间：服务端返回 naive UTC 的 ISO 字符串（如 `2026-10-20T08:29:33.838099`），**不带 Z/偏移**；前端需按 UTC 解析（`web/src/constants.js:78-88` 的 `parseUtc`）。
- 数值：计数类均为整数；增删行数无上限（int32 足够）。
- 空值：`diff_summary` 未采集时为 `null`；`annotations` 无内容时为 `[]`（空数组，非 null）；`before` / `after` 子对象同理。

## 枚举全集

| 枚举 | 取值 | 含义 | 出现位置 |
|---|---|---|---|
| `MIO_DIFF_GATE` | `off` / `warn` / `block` | 门禁生效模式，默认 `warn` | 环境变量 |
| `Verdict.reason` | `gate_off` / `no_task` / `no_workspace` / `task_exempt` / `task_kind_exempt` / `concurrent_run` / `collect_failed` / `no_before_snapshot` / `has_changes` / `empty_diff` / `empty_diff_warn` | 判定原因 | `diff_summary.reason`、`diff-evidence.reason` |
| `annotations[].level` | `block` / `warn` | 标注级别 | `runs[].annotations` |
| `TaskKind` | `normal` / `change_tracking` / `idea_review` | 任务类型；后两者豁免门禁 | 任务详情 |
| `RunState` | `CLAIMED` / `RUNNING` / `FINISHED` | 运行状态 | runs[] |

> 调用方必须能处理**未知枚举取值**（前向兼容要求）：未知 `reason` 按「跳过判定」处理，未知 `level` 按 `warn` 渲染。

## 限流与配额

- 本地单用户服务，**不限流、不配额**，无 429 响应。
- 唯一的资源约束是 git 子进程：单次判定 ≤ 4 次 git 调用，超时 5s。

## 超时与并发

- 建议客户端超时：submit_result ≥ 15s（含 git 采集）。
- 并发冲突：同一 run 的并发提交由既有 CAS 与 `assert_run_submittable` 处置，响应 409 `run.result_already_submitted`。
- 同一 workspace 存在多个活跃 run 时，本次实现**跳过判定**（`reason=concurrent_run`），不产生竞态误判。

## 文件上传与下载

本契约涉及的接口**不涉及文件上传或下载**（本节保留仅为完整性；无相关接口）。

## 安全与脱敏

- 绝对路径（如 workspace 绝对路径）**不出现在任何响应体中**，只出现在内部日志。
- git 调用为只读子命令，不执行任何写操作，不拼接用户输入到 shell（参数以数组传入，不走 shell）。
- 错误信息不回显 git 的完整 stderr（可能含本地路径），只回显归一化原因。
- 日志禁止记录 `result` 正文与 diff 明细（可能含业务代码）。

## 兼容性与废弃策略

- **破坏性变更**定义：既有成功路径新增失败可能、既有字段语义改变、既有错误码语义改变。本次 C-1 属破坏性（成功提交可能返回 422）。
- 通知方式与提前期：本地单用户，随仓库 CHANGELOG 与本文档变更记录同步。
- 废弃三阶段：`run.empty_diff` **不设废弃期**；若判据后续调整，用 `MIO_DIFF_GATE=warn` 灰度而非直接改语义。
- 契约校验方式：前端按 `detail.code` 分支渲染，未知 code 落到通用错误分支（不得崩溃）。

## 附录

### 状态码全集（本次涉及）

| 状态码 | 含义 |
|---|---|
| 200 | 成功 |
| 403 | 越权（`run.not_owner`） |
| 404 | 资源不存在 |
| 409 | 冲突（`run.not_active` / `run.race_lost` / `run.result_already_submitted` / `policy_risk_high`） |
| 422 | 门控未通过（`run.empty_diff` / 读证门控） |

### 错误码全集

见「统一错误码」章节。

### 关联文档

- 需求规格：`docs/taskhub/d468e633/requirement.md`
- 模块设计：`docs/taskhub/d468e633/spec.md`
- 测试验收：`docs/taskhub/d468e633/test.md`
- 收窄决策：`docs/taskhub/product-decision-20261020.md`
- 可实施方案：`docs/taskhub/narrow-gate-implementation-plan-20261020.md`
