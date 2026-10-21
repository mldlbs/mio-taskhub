# P1-2 接口契约：任务评审与阶段推进

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 契约版本 | P1-2 |
| 状态 | approved |
| 最后更新 | 2026-10-21 09:51 |
| 负责人 | hermes |
| 适用范围 | 任务评审提交（approve/reject/comment）与进入 done 阶段的证据门控 |

## 1. 文档信息与范围

本契约覆盖 3 个端点的**行为收窄**：

| 端点 | 变更 |
|---|---|
| `POST /api/v1/tasks/{task_id}/reviews` | approve 需非空审阅人；自审恒拒；`self_review` 字段移除 |
| `POST /api/v1/tasks/{task_id}/stage` | 目标 `done` 需真实评审证据 |
| `POST /api/v1/tasks/{task_id}/stage/move` | 同上（共用同一门控） |

**明确不含**：

- 不含新增端点 —— 本次**不新增**任何 API；
- 不含任务列表/看板/文档类端点 —— 它们的契约未变；
- 不含 MCP 工具契约 —— 任务评审**没有** MCP 入口
  （`taskhub_submit_review` 是**想法**评审工具，与任务评审无关）；
- 不含认证鉴权机制的变更 —— 本次不碰。

## 2. 环境与基础地址

| 环境 | Base URL |
|---|---|
| dev（本机） | `http://127.0.0.1:48620` |
| prod | 由部署配置决定，不在本文档固化 |

路径前缀 `/api/v1` 由应用自身路由挂载，不经网关重写。

## 3. 认证与鉴权

本仓库当前**未启用**鉴权（本地单机部署形态），本契约的三个端点**无认证要求**。

本任务引入的「审阅人 ≠ 执行者」是**业务层**的独立性约束，不是身份鉴权 ——
它防止「执行者自己宣布自己验收通过」，**不**防止「冒用他人名字提交」。
后者需要真正的身份体系，超出 P1-2 范围（见 `requirement.md` 非目标）。

| 状态码 | 含义 | 业务码 |
|---|---|---|
| 401 | 未认证 | 当前不适用 |
| 403 | 越权 | 当前不适用 |

## 4. 通用响应结构

成功响应为端点各自的业务对象（无统一 `data` 外壳）；失败响应统一为：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | object \| string | 是 | 错误详情；本任务新增的错误为 object |
| `detail.code` | string | 否 | 业务错误码，前端按码渲染 |
| `detail.message` | string | 否 | 面向人的说明文案 |
| `detail.hint` | string | 否 | 补救建议 |
| `detail.reviewer` | string | 否 | 触发判定的审阅人 |
| `detail.executor` | string | 否 | 任务最近的执行者 |
| `detail.has_approve_record` | bool | 否 | 是否存在 approve 记录 |
| `detail.has_review_doc` | bool | 否 | 是否登记了 review 文档路径 |
| `detail.min_length` | int | 否 | 结论长度下限 |
| `detail.current_length` | int | 否 | 实际结论长度 |

无返回裸数组或二进制的端点。

## 5. 统一错误码

| code | HTTP | 含义 | 端点 |
|---|---|---|---|
| `review.reviewer_required` | 409 | approve 未提供审阅人 | `/reviews` |
| `review.self_approval` | 409 | 审阅人是本任务的执行者 | `/reviews`、`/stage`、`/stage/move` |
| `review.evidence_missing` | 422 | 进 done 缺真实评审证据 | `/stage`、`/stage/move` |
| `review.summary_too_short` | 422 | 结论内容太短（既有，未改） | `/reviews` |
| `task.not_found` | 404 | 任务不存在 | 全部 |
| `stage.invalid_transition` | 400 | 阶段流转非法 | `/stage`、`/stage/move` |

无完成路径（`failed`/`retrying` → `done`）返回 **409**，detail 为纯字符串
（沿用既有格式，未纳入业务码体系）。

## 6. 字段命名与类型规范

| 字段 | 类型 | 必填 | 约束 |
|---|---|---|---|
| `decision` | string | 是 | 枚举 `approve` / `reject` / `comment`；其他值 422 |
| `reviewer` | string | approve 时是 | 去空白后非空；≤64 字符 |
| `summary` | string | approve 时是 | 未登记 review 文档时 ≥ 8 字符 |
| `comments` | string | 否 | 自由文本 |
| `checklist` | object | 否 | 键值对 |
| `artifacts` | array | 否 | 产出物引用 |
| `self_review` | bool | **已移除** | 字段被忽略，不再是绕过开关 |

命名风格：snake_case；ID 一律 string；布尔用 `is_` / `has_` 前缀或语义化动词。

时间字段一律 ISO 8601 带时区偏移（`2026-10-21T01:43:12.762241+00:00`）。
**字段缺失与显式 null 的区别**：本 API 的可选字段缺失时按「空值」处理
（字符串 `""`、对象 `{}`、数组 `[]`），不返回 `null`；调用方无需判 `null`。

## 7. 幂等性与重试

| 端点 | 幂等性 | 幂等键 | 重复提交返回 |
|---|---|---|---|
| `/reviews` | **不幂等** | 无 | 201/200 + 新记录（每次新建 TaskReview） |
| `/stage` | 不幂等 | 无 | 409（状态机拒绝重复流转） |
| `/stage/move` | 不幂等 | 无 | 409（状态机拒绝重复流转） |

可安全重试的状态码：网络层错误、502/503/504。
**不可**重试：409 `review.*`（重试不会改变判据结果）、422（入参不合法）。

退避策略：指数退避，基准 1s，上限 30s，最多 3 次。

## 8. 接口清单

| 方法 | 路径 | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|---|---|---|---|---|---|---|
| POST | `/api/v1/tasks/{task_id}/reviews` | 提交评审 | 无 | 否 | 无 | 任务任意阶段 |
| POST | `/api/v1/tasks/{task_id}/stage` | 推进阶段 | 无 | 否 | 无 | 阶段流转 |
| POST | `/api/v1/tasks/{task_id}/stage/move` | 移动阶段 | 无 | 否 | 无 | 阶段流转 |

## 9. 接口明细

### POST /api/v1/tasks/{task_id}/reviews

提交审阅记录。`decision` 取值 `approve` / `reject` / `comment`。

#### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| path `task_id` | string | 是 | 任务 ID |
| body `decision` | string | 是 | approve / reject / comment |
| body `reviewer` | string | approve 时是 | 审阅人标识；空或缺省 → 409 `review.reviewer_required` |
| body `summary` | string | approve 时是 | 审查结论；未登记 review 文档时 ≥ 8 字符 |
| body `comments` | string | 否 | 备注 |
| body `checklist` | object | 否 | 检查项键值对 |
| body `artifacts` | array | 否 | 产出物引用 |
| body `self_review` | bool | **已移除** | 被忽略，不再放行自审 |

#### 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 评审记录 ID |
| `decision` | string | 回显 decision |
| `review_duration_sec` | int \| null | 从 `review_started_at` 算起的审阅耗时 |

#### 错误码

| code | HTTP | 触发条件 | 处理建议 |
|---|---|---|---|
| `review.reviewer_required` | 409 | approve 且 reviewer 为空或缺省 | 填入审阅人标识后重试 |
| `review.self_approval` | 409 | approve 且 reviewer == 任务最近执行者（忽略 `self_review`） | 改由他人评审，或先 comment 记录再由他人 approve |
| `review.summary_too_short` | 422 | approve 且 summary < 8 字符且未登记 review 文档 | 补充结论，或先登记 review 文档 |

副作用保证：被拒时**不**创建 TaskReview、**不**写 `task.review_result`、
**不**推进任务状态或阶段。

#### 示例

请求：

```bash
curl -X POST http://127.0.0.1:48620/api/v1/tasks/a1aba9ef/reviews \
  -H 'Content-Type: application/json' \
  -d '{"decision":"approve","reviewer":"someone-else","summary":"已核对变更范围与测试结果，无阻断问题"}'
```

成功响应：

```json
{"id": "3f2a...", "decision": "approve", "review_duration_sec": 42}
```

被拒响应（自审）：

```json
{
  "detail": {
    "code": "review.self_approval",
    "message": "审阅人 'opencode' 是本任务最近的执行者，不能自我 approve；请由他人独立验收",
    "reviewer": "opencode",
    "executor": "opencode",
    "hint": "「独立验收」要求审阅人与执行者不同。若确需自审，请改为 comment 记录，或由他人复核后再 approve"
  }
}
```

### POST /api/v1/tasks/{task_id}/stage

推进到下一研发阶段。

#### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| path `task_id` | string | 是 | 任务 ID |
| body `target_stage` | string | 是 | 目标阶段 |
| body `review_result` | string | 否 | 审查结论文本；**不再单独构成**进入 done 的证据 |
| body `doc_paths` | object | 否 | 产出物路径映射；`review` 项须**实际落盘** |
| body `force` | bool | 否 | `true` 跳过门控并留痕 |

#### 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 任务 ID |
| `stage` | string | 推进后的阶段 |
| `spec_path` | string | 规格文档路径 |
| `plan_path` | string | 计划文档路径 |
| `doc_paths` | object | 产出物路径映射 |
| `review_result` | string | 审查结论 |
| `state` | string | 任务状态 |

#### 错误码

| code | HTTP | 触发条件 | 处理建议 |
|---|---|---|---|
| `review.evidence_missing` | 422 | 目标 done 且无他人 approve 记录、无实际落盘 review 文档 | 先提交 approve 评审记录，或写入并登记 review 文档 |
| `review.self_approval` | 409 | 目标 done 且 approve 记录全部来自执行者本人 | 由他人 approve 后重试 |

#### 示例

请求：

```bash
curl -X POST http://127.0.0.1:48620/api/v1/tasks/a1aba9ef/stage \
  -H 'Content-Type: application/json' \
  -d '{"target_stage":"done","review_result":"approved"}'
```

被拒响应：

```json
{
  "detail": {
    "code": "review.evidence_missing",
    "message": "进入 done 需要真实评审证据：既无 approve 评审记录，也无实际落盘的 review 文档；review_result 纯文本不足以构成独立验收",
    "has_approve_record": false,
    "has_review_doc": false,
    "hint": "先 POST /api/v1/tasks/{id}/reviews 提交 decision=approve 的评审记录（审阅人不得是本任务的执行者），或写入并登记 review 文档（doc_paths['review']，文件须实际存在）；紧急情况可带 force=true 跳过并留痕"
  }
}
```

### POST /api/v1/tasks/{task_id}/stage/move

拖拽移动阶段。`target_stage == "done"` 时门控与 `POST /stage` **完全一致**
（共用 `task_stages._check_review_evidence`）。

#### 请求参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| path `task_id` | string | 是 | 任务 ID |
| body `target_stage` | string | 是 | 目标阶段 |
| body `review_result` | string | 否 | 同 `POST /stage` |
| body `doc_paths` | object | 否 | 同 `POST /stage` |
| body `force` | bool | 否 | 同 `POST /stage` |

#### 响应字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 任务 ID |
| `stage` | string | 移动后的阶段 |
| `spec_path` | string | 规格文档路径 |
| `plan_path` | string | 计划文档路径 |
| `doc_paths` | object | 产出物路径映射 |
| `review_result` | string | 审查结论 |
| `state` | string | 任务状态 |

#### 错误码

| code | HTTP | 触发条件 | 处理建议 |
|---|---|---|---|
| `review.evidence_missing` | 422 | 同 `POST /stage` | 同上 |
| `review.self_approval` | 409 | 同 `POST /stage` | 同上 |

#### 示例

请求：

```bash
curl -X POST http://127.0.0.1:48620/api/v1/tasks/a1aba9ef/stage/move \
  -H 'Content-Type: application/json' \
  -d '{"target_stage":"done","review_result":"ok"}'
```

被拒响应同 `POST /stage`（同一门控、同一错误码）。

**判据顺序差异**：`failed` / `retrying` 任务移动到 `done` 返回 **409**
（状态机无完成路径），**不是** 422 `review.evidence_missing`：

```json
{"detail": "不可移动到 done：状态 failed 无完成路径（failed/retrying 请先 retry，running 请等待 agent 提交）"}
```

## 10. 变更记录

| 版本 | 日期 | 契约版本 | 变更 | 兼容/破坏 | 影响方 |
|---|---|---|---|---|---|
| v1.0 | 2026-10-21 | P1-2 | 收窄 approve 判据（禁空 reviewer、删 self_review 通道），新增进入 done 的评审证据门控 | **破坏性** —— 此前可 200 的请求现返回 409/422 | 所有调用 `/reviews` `/stage` `/stage/move` 的客户端 |

### 相对变更前行为的关键差异

| # | 场景 | 变更前 | 变更后 |
|---|---|---|---|
| 1 | `reviewer=""` | 200 | 409 `review.reviewer_required` |
| 2 | 不传 `reviewer` | 200 | 409 `review.reviewer_required` |
| 3 | `self_review=true` | 200 | 409 `review.self_approval` |
| 4 | `POST /stage` 带 `review_result` 进 done | 200（TaskReview=0） | 422 `review.evidence_missing` |
| 5 | `POST /stage/move` 带 `review_result` 进 done | 200（TaskReview=0） | 422 `review.evidence_missing` |
| 6 | 只登记 `doc_paths.review`（文件不存在） | 200（review_result="见审查文档 xxx.md"） | 422 `review.evidence_missing` |
| 7 | 他人 approve 后进 done | 200 | 200（不变） |
| 8 | 纯人工任务（无 run）approve + 进 done | 200 | 200（不变） |
| 9 | `reject` / `comment` 自审 | 200 | 200（不变） |
| 10 | `force=true` | 200 | 200（不变） |
| 11 | `failed` 任务 move→done | 409 | 409（不变，但判据顺序前移） |

## 11. 版本策略

| 项 | 约定 |
|---|---|
| 契约版本 | 语义化 `v<major>.<minor>.<patch>`；`major` 变更 = 破坏性契约变更 |
| 破坏性变更 | 递增 `major`，须在「变更记录」标注影响方与兼容期 |
| 新增端点/可选字段 | 递增 `minor`，向后兼容 |
| 仅修文档表述 | 递增 `patch` |
| 弃用期 | 破坏性变更至少保留一个 `minor` 版本双读/双写兼容期 |
| 生效方式 | 契约随代码发布，无独立灰度 |

## 12. 通用请求头

| 请求头 | 必填 | 说明 |
|---|---|---|
| `Content-Type: application/json` | 是 | 请求体为 JSON |
| `Accept: application/json` | 否 | 响应恒为 JSON |
| `X-Request-Id` | 否 | 客户端自带的追踪 ID；本 API 当前不消费 |

本 API **不使用**任何鉴权请求头（见 §3）。

## 13. 分页

本契约涉及的 3 个端点**均不返回列表**，因此无分页参数。

涉及分页的相邻端点（`GET /api/v1/tasks`、`GET /api/v1/reviews/queue`）
不在本契约范围内，其分页约定保持既有实现不变。

## 14. 时间 / 数值 / 空值约定

| 类型 | 约定 |
|---|---|
| 时间 | ISO 8601 带时区偏移，如 `2026-10-21T01:43:12.762241+00:00` |
| 数值 | 整数用 int；时长用秒（`review_duration_sec`）；比例用 0~1 浮点 |
| 空字符串 | 用 `""` 表示「无内容」，不用 `null` |
| 空对象 | 用 `{}` 表示「无子项」，不用 `null` |
| 空数组 | 用 `[]` 表示「无条目」，不用 `null` |
| 布尔 | `true` / `false`，不用 0/1 |
| 字段缺失 | 语义等同对应的空值（调用方无需判 `null`） |

## 15. 枚举全集

| 枚举 | 取值 | 说明 |
|---|---|---|
| `decision` | `approve` / `reject` / `comment` | 评审决定；其他值 422 |
| `target_stage` | `brainstorming` / `design` / `planning` / `ready` / `implementing` / `review` / `done` / `cancelled` | 研发阶段；非法值 400 |
| 任务 `state` | `QUEUED` / `CLAIMED` / `RUNNING` / `COMPLETED` / `FAILED` / `RETRYING` / `CANCELLED` | 任务状态（只读，由系统维护） |
| 错误码 | 见 §5 | 业务错误码全集 |

## 16. 限流与配额

当前**无**限流与配额机制（本地单机部署形态）。

若后续引入网关限流，约定：
- 限流维度：源 IP；
- 超限响应：`429`，`detail.code = "rate_limited"`；
- 客户端退避：见 §7。

## 17. 超时与并发

| 项 | 约定 |
|---|---|
| 服务端读超时 | 客户端建议 10s（评审提交与阶段推进均为同步 DB 操作，毫秒级返回） |
| 客户端写超时 | 建议 30s |
| 并发 | 同一任务的评审可并发提交；每次产生独立 TaskReview 记录 |
| 并发判据 | 门控是**读时判定**。两人同时 approve 同一任务时，最坏结果是多一条 approve 记录，不影响「approved 可信」目标 |
| 竞态后果 | 阶段推进由状态机保护，重复推进被拒（409） |

## 18. 文件上传与下载

本契约的 3 个端点**不涉及**文件上传/下载。

涉及文件的**间接**约定：进入 done 时校验 review 文档是否**实际落盘**
（`doc_paths['review']` 指向的文件存在且非空）。该校验只读文件系统元信息，
不传输文件内容；文件通过其他端点写入。

## 19. 安全与脱敏

| 项 | 约定 |
|---|---|
| 敏感字段 | 3 个端点均不接收或返回凭据、token、密钥 |
| 日志脱敏 | 错误响应不回显文件内容，只回显路径与布尔判定结果 |
| 路径逃逸 | review 文档路径解析受 workspace 逃逸防护约束（既有实现） |
| 审阅人身份 | 「审阅人 ≠ 执行者」是业务独立性约束，**非**身份鉴权；不防冒名顶替（见 §3） |
| 注入 | 请求体为 JSON，无字符串拼接的 SQL 路径（ORM 参数化） |

## 20. 兼容性与废弃策略

| 变更类型 | 版本 | 兼容期 | 通知 |
|---|---|---|---|
| approve 判据收窄（本次） | `major` | 紧急通道 `force=true` 保留 | 变更记录 §10 已标注影响方 |
| 移除 `self_review` 字段 | `major` | 字段被**忽略**而非报错 —— 老客户端继续传也不会 4xx | 已标注 |
| 新增可选字段 | `minor` | 无需通知 | — |
| 弃用端点 | `major` | 至少 1 个 `minor` 版本双跑 | 变更记录标注 |

**迁移建议**：原先靠 `self_review=true` 自审的调用方，应改为
① 提交 `comment` 记录自检结论，再由他人 approve；或
② 走 `force=true` 通道并在别处留痕。

## 21. 附录

### A. 相关文档

| 文档 | 位置 |
|---|---|
| 需求规格 | `docs/taskhub/a1aba9ef/requirement.md` |
| 技术设计 | `docs/taskhub/a1aba9ef/spec.md` |
| 测试与验收 | `docs/taskhub/a1aba9ef/test.md` |
| 实施计划 | `docs/taskhub/a1aba9ef/plan.md` |
| 产品决策 | `docs/taskhub/product-decision-20261020.md` |

### B. 错误码速查

```text
409 review.reviewer_required   approve 未提供审阅人
409 review.self_approval       审阅人是本任务的执行者
422 review.evidence_missing    进 done 缺真实评审证据
422 review.summary_too_short   approve 结论太短（既有）
409 (纯字符串)                 状态机无完成路径（failed/retrying → done）
```

### C. 判据伪码

```text
assert_reviewable(task, reviewer, decision):
    if decision != "approve":            return OK
    if reviewer is blank:                return DENY reviewer_required
    executor = last_run_agent(task)
    if executor and executor == reviewer: return DENY self_approval
    return OK

check_review_evidence(task):
    if force:                            return OK      # 紧急通道，留痕
    approves = taskreviews(task, decision="approve")
    executor = last_run_agent(task)
    if approves:
        if not executor:                 return OK      # 纯人工任务
        if any(r.reviewer != executor):  return OK      # 他人已验收
        return DENY self_approval
    if review_doc_written_and_nonempty(task): return OK
    return DENY evidence_missing
```
