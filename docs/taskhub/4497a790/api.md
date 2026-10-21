# 接口契约：P1-1 终态口径统计

## 文档信息

| 项 | 值 |
|----|-----|
| 契约版本 | v1.0 |
| 状态 | approved |
| 最后更新 | 2026-10-21 08:52 |
| 负责人 | hermes (agent) |
| 适用范围 | 本文档**仅覆盖** P1-1 新增的 1 个端点 `GET /api/v1/board/terminal-stats` |
| 明确不含 | 既有全部 `/api/v1/*` 端点（见 `/docs` 的 OpenAPI）、窄门禁的 422 契约（属 task d468e633） |

## 文档信息与范围

- **契约版本**：v1.0（P1-1 引入）
- **服务**：`mio-taskhub` v0.6.0
- **范围**：终态（COMPLETED / FAILED / CANCELLED）统计口径，failed 与 cancelled 严格分离
- **不含**：任务 CRUD、阶段推进、门禁类端点
- **权威性**：本端点是**口径定义源**。`/metrics` 与 `/observability/report`
  的 `success_rate` 若与本端点不一致，属实现漂移，应向本端点对齐

## 环境与基础地址

| 环境 | Base URL | 说明 |
|------|----------|------|
| dev（本地源码） | `http://127.0.0.1:48620/api/v1` | `mio-taskhub serve` 默认端口 |
| prod（绿色版） | `http://127.0.0.1:48620/api/v1` | `mio-taskhub.exe` 默认端口 |

> 路径前缀固定为 `/api/v1`。**不含** `/api/v1` 前缀的地址（`/docs`、`/metrics`、`/dashboard`）不受本文档约束。

## 认证与鉴权

| 项 | 说明 |
|----|------|
| 认证方式 | Bearer Token（可选启用） |
| 凭证来源 | 服务端：`MIO_TASKHUB_TOKEN` 环境变量 > `--token` 参数 > 未设置时自动生成并打印 |
| 传递位置 | HTTP Header `Authorization: Bearer <token>` |
| 有效期 | **无过期机制**（单用户本地工具的设计取舍；token 轮换为 P3 待办） |
| 权限模型 | 无多租户/无角色 —— token 即全权 |

**401 vs 403**：
- **401 unauthorized**：未带 token 或 token 不匹配（`main.py:make_auth_middleware`）
- **403**：本端点**不存在**此分支

> `/api/*` 前缀需认证；Web UI、`/docs`、`/metrics`、`/dashboard` 免认证。

## 通用响应结构

**成功响应**（本端点直接返回对象，无 `data` 外壳）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `total_tasks` | integer | 是 | 任务总数（全量，含所有状态） |
| `active` | integer | 是 | 非终态任务数 |
| `terminal_total` | integer | 是 | 终态任务数 = completed + failed + cancelled |
| `by_state` | object<string, integer> | 是 | 各状态计数，零填充全部已知状态 |
| `completed` | integer | 是 | COMPLETED 计数 |
| `failed` | integer | 是 | FAILED 计数 |
| `cancelled` | integer | 是 | CANCELLED 计数 |
| `attempted` | integer | 是 | attempted = completed + failed |
| `success_rate` | number \| null | 是 | completed / attempted，4 位小数；attempted=0 时为 null |
| `failure_rate` | number \| null | 是 | failed / attempted；attempted=0 时为 null |
| `cancel_rate` | number \| null | 是 | cancelled / terminal_total；terminal_total=0 时为 null |
| `definitions` | object<string, string> | 是 | 每个比率的分子/分母说明 |
| `data_integrity` | object | 是 | 数据完整性自检 |

**失败响应**：本端点**不返回** 4xx 业务错误（无参数、无业务校验）。仅 401（未认证）与 500（DB 不可达）。

> `null` 与 `0` 的语义差异：比率的 `null` = **分母为 0，比率无定义**；比率的 `0` = **分母不为 0，但分子为 0**。二者不可互换。

## 统一错误码

| HTTP 状态 | 业务码 | 含义 | 触发条件 | 处理建议 |
|-----------|--------|------|----------|----------|
| 401 | `unauthorized` | 未认证 | 启用鉴权但 `Authorization` 头缺失或 token 不匹配 | 检查 `MIO_TASKHUB_TOKEN` 是否与服务端一致 |
| 500 | — | 内部错误 | SQLite 不可达或聚合查询异常 | 查 hub 日志；用 `/healthz` 确认进程存活、`/readyz` 确认 DB 可达 |

> 本端点**无** 4xx 业务错误码（无参数校验）。P1-1 窄门禁的 `run.empty_diff` 422 码
> 属 task d468e633，不在本契约范围。

## 字段命名与类型规范

| 规则 | 说明 |
|------|------|
| 命名 | `snake_case` |
| 比率 | 浮点，**4 位小数**（`round(x, 4)`） |
| 比率范围 | `[0.0, 1.0]` |
| 计数 | 整数，`>= 0` |
| 空值 | 分母为 0 的比率用 `null`（不用 0，不用省略） |
| 状态键 | `by_state` 的键是**枚举名**（`COMPLETED`/`FAILED`/`CANCELLED`/`QUEUED`/`CLAIMED`/`RUNNING`/`RETRYING`/`BLOCKED_FAILED`），**大写**；零填充，不省略 |
| 时间 | 本端点不返回时间字段 |

## 幂等性与重试

| 项 | 说明 |
|----|------|
| 幂等 | 纯读，多次调用结果一致（除非期间有任务状态变更） |
| 重试 | 安全，可无限重试 |
| 副作用 | 无。不写库、不发事件、不触发广播 |

## 接口清单

| 方法 | 路径 | 作用 | 认证 |
|------|------|------|------|
| GET | `/api/v1/board/terminal-stats` | 终态全量统计：failed / cancelled 严格分离，成功率口径唯一且可审计 | 是（`/api/*` 前缀） |

## 接口明细

### GET /api/v1/board/terminal-stats

#### 请求参数

本端点**无参数**（全量统计，不接受过滤 —— 过滤会引入新的分母歧义）。

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|------|------|------|------|------|------|------|
| （无） | — | — | — | — | — | — |

鉴权头：

| 名称 | 位置 | 类型 | 必填 | 默认 | 约束 | 示例 |
|------|------|------|------|------|------|------|
| Authorization | Header | string | 是 | — | `Bearer <token>` | `Bearer abc123xyz` |

#### 响应字段

| 字段 | 类型 | 必填 | 示例 | 说明 |
|------|------|------|------|------|
| `total_tasks` | integer | 是 | `1933` | 任务总数 |
| `active` | integer | 是 | `43` | 非终态任务数 |
| `terminal_total` | integer | 是 | `1890` | completed + failed + cancelled |
| `by_state` | object | 是 | `{"COMPLETED":179,"FAILED":1525,...}` | 各状态计数，零填充全部已知状态 |
| `completed` | integer | 是 | `179` | COMPLETED 计数 |
| `failed` | integer | 是 | `1525` | FAILED 计数 |
| `cancelled` | integer | 是 | `186` | CANCELLED 计数 |
| `attempted` | integer | 是 | `1704` | completed + failed |
| `success_rate` | number\|null | 是 | `0.105` | completed / attempted |
| `failure_rate` | number\|null | 是 | `0.895` | failed / attempted |
| `cancel_rate` | number\|null | 是 | `0.0984` | cancelled / terminal_total |
| `definitions` | object | 是 | `{"attempted":"COMPLETED + FAILED（真正尝试过执行的任务）",...}` | 口径说明 |
| `data_integrity.cancelled_with_failure_evidence` | integer | 是 | `0` | 有失败证据却标成取消的条数；>0 表示口径仍被污染 |
| `data_integrity.cancelled_missing_timestamp` | integer | 是 | `62` | 取消任务缺 `cancelled_at` 的条数；>0 表示有绕过状态机的硬写 |
| `data_integrity.clean` | boolean | 是 | `false` | 两者皆 0 时为 true |

#### 错误码

| 业务码 | HTTP 状态 | 含义 | 触发条件 | 处理建议 |
|--------|-----------|------|----------|----------|
| `unauthorized` | 401 | 未认证 | 启用鉴权但 token 缺失/不匹配 | 核对 `MIO_TASKHUB_TOKEN` |
| — | 500 | 内部错误 | DB 不可达 | 查日志；`/readyz` 验 DB |

#### 示例

**请求**：
```bash
curl -s -H "Authorization: Bearer $MIO_TASKHUB_TOKEN" \
  http://127.0.0.1:48620/api/v1/board/terminal-stats
```

**响应 200**：
```json
{
  "total_tasks": 1933,
  "active": 43,
  "terminal_total": 1890,
  "by_state": {
    "QUEUED": 42,
    "CLAIMED": 1,
    "RUNNING": 0,
    "RETRYING": 0,
    "COMPLETED": 179,
    "FAILED": 1525,
    "CANCELLED": 186,
    "BLOCKED_FAILED": 0
  },
  "completed": 179,
  "failed": 1525,
  "cancelled": 186,
  "attempted": 1704,
  "success_rate": 0.105,
  "failure_rate": 0.895,
  "cancel_rate": 0.0984,
  "definitions": {
    "attempted": "COMPLETED + FAILED（真正尝试过执行的任务）",
    "success_rate": "COMPLETED / attempted（cancelled 不进分母）",
    "failure_rate": "FAILED / attempted",
    "cancel_rate": "CANCELLED / terminal_total（取消是独立终态，单独度量）"
  },
  "data_integrity": {
    "cancelled_with_failure_evidence": 0,
    "cancelled_missing_timestamp": 62,
    "clean": false
  }
}
```

**响应 401**：
```json
{"detail": "unauthorized"}
```

## 变更记录

| 版本 | 日期 | 变更 | 作者 |
|------|------|------|------|
| v1.0 | 2026-10-21 | 首次发布：`GET /api/v1/board/terminal-stats`（P1-1） | hermes (agent) |

## 版本策略

| 项 | 约定 |
|----|------|
| 契约版本 | 与文档版本号一致（`v1.0`），语义化版本 |
| 兼容承诺 | **新增字段** = 向后兼容（客户端应忽略未知字段）；**删除/改名字段** = 破坏性变更 → 升主版本号 |
| 弃用期 | 破坏性变更至少保留一个次版本，弃用期 ≥ 1 个发布周期 |
| 口径变更 | `success_rate` / `attempted` 等**口径定义**变更属产品级决策，须同步更新 `definitions` 字段、`api.md`、并通知下游消费者 |

## 通用请求头

| 名称 | 必填 | 示例 | 说明 |
|------|------|------|------|
| `Authorization` | 启用鉴权时是 | `Bearer abc123` | Bearer token |
| `Accept` | 否 | `application/json` | 固定 JSON |

本端点**不**接受任何自定义业务请求头（无 `X-Trace-Id` 等）—— 追踪由
OpenTelemetry 中间件自动注入（`trace_id` / `span_id` 出现在结构化日志中）。

## 分页

本端点**无分页**。

理由：全量统计口径要求一次性返回全部终态计数；若分页，分母会被切碎，
`success_rate` 将失去意义。如未来数据量导致响应过大，正确做法是**改为
窗口化/聚合口径**（如按月分桶），而非加分页。

## 时间 / 数值 / 空值约定

| 项 | 约定 |
|----|------|
| 时间 | 本端点**不返回**时间字段。若未来新增，一律用 ISO 8601 带时区（`2026-10-21T08:45:53+08:00`） |
| 数值 | 计数为整数；比率为浮点、4 位小数；比率范围 `[0.0, 1.0]` |
| 空值 | 分母为 0 的比率用 `null`；**0** 与 **null** 语义不同（0 = 分母非 0 且分子为 0） |
| 字符串 | UTF-8，中文不转义（`ensure_ascii=false`） |

## 枚举全集

| 枚举 | 取值 |
|------|------|
| `by_state` 的键 | `QUEUED` · `CLAIMED` · `RUNNING` · `RETRYING` · `COMPLETED` · `FAILED` · `CANCELLED` · `BLOCKED_FAILED` |
| `data_integrity.clean` | `true` · `false` |

`BLOCKED_FAILED` 是历史遗留态（不在状态转移表内，不可达），保留在枚举中以便
审计对账；`ops/data_fixes.py` 会把它批量迁回 `QUEUED` + `block_reason="依赖未满足"`。

## 限流与配额

| 项 | 值 |
|----|-----|
| 限流 | 全局 `MIO_TASKHUB_RATE_LIMIT`，默认 **120 req/min/IP**（`middleware.py:RateLimitMiddleware`） |
| 本端点额外配额 | 无 |
| 429 | 触发全局限流时返回 |

## 超时与并发

| 项 | 说明 |
|----|------|
| 服务端处理 | 单条聚合 SQL，无锁竞争，1933 行实测 < 20ms |
| 客户端超时 | 建议 5s（无长查询） |
| 并发 | 纯读，可任意并发；SQLite WAL 模式支持多读单写 |
| 一致性 | 快照读（单条 SELECT 内一致）；两次调用之间的差异源于期间的状态变更 |

## 文件上传与下载

不适用。本端点**无**任何文件上传/下载能力。

## 安全与脱敏

| 项 | 说明 |
|----|------|
| 认证 | Bearer token（见「认证与鉴权」） |
| 授权 | 无角色模型 —— token 即全权（单用户本地工具） |
| 脱敏 | 响应**只含聚合计数**，不含任何任务标题、描述、路径等业务内容 |
| 注入 | 全部查询为参数化 SQL（SQLAlchemy `text()` + 绑定参数），无字符串拼接 |
| 传输 | 明文 HTTP（仅监听 127.0.0.1）；对外暴露需 `--host` + `--auth`，否则启动被拒绝（`net_guard.py`） |

## 兼容性与废弃策略

| 项 | 说明 |
|----|------|
| 向前兼容 | 新增字段不影响既有客户端（JSON 解析忽略未知键） |
| 弃用流程 | 字段弃用 → 标 `deprecated` → 保留 ≥1 个次版本 → 删除并升主版本号 |
| 迁移路径 | 本端点为**新增**，无迁移负担 |
| 与其他端点的兼容 | `/observability/report` 的 `tasks` 段已对齐本端点口径；若本端点口径变更，须同步 `report.py` |

## 附录

### A. 相关端点

| 端点 | 关系 |
|------|------|
| `GET /api/v1/observability/report` | `tasks` 段已与本端点对齐（`success_rate` / `attempted` / `data_integrity` / `definitions`） |
| `GET /metrics` | Prometheus 文本；主口径与本端点一致，但额外排除 `insight-auto`/`insight-followup` 监控工件 |
| `GET /api/v1/board/overview` | 阶段/状态分布，不含成功率 |
| `GET /api/v1/board/summary` | 看板汇总（阶段计数 + 队列 + 告警），不含终态成功率 |

### B. 修复前后的口径对照（历史留档）

| 位置 | 修复前口径 | 修复前实测 | 修复后 |
|------|-----------|-----------|--------|
| `observability/metrics.py` | `completed / (completed + failed)` | 0.8364 | 不变（本就正确） |
| `observability/report.py` | `completed / (completed + failed + cancelled)` | 0.0947 | `completed / attempted` → 与本端点一致 |
| `api/board.py` `/overview` | 无成功率 | — | 新增 `/terminal-stats` |

> 0.8364 是**虚高**的：1490 条失败被清洗成取消后从分母消失（分母 1705 → 214）。
> 修复后 0.1050 是诚实值；其中 1470 条是 idea 评审任务的重复失败风暴（监控工件），
> 排除后**真实交付率 0.8281**。

### C. 修复脚本

```bash
# 预览（默认，不写库）
.venv/Scripts/python.exe scripts/repair_cancelled_failed.py --dry-run
# 落库
.venv/Scripts/python.exe scripts/repair_cancelled_failed.py --apply
# 回滚
.venv/Scripts/python.exe scripts/repair_cancelled_failed.py --undo
```
