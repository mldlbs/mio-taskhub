# 接口契约（API Contract）· memory-merge 路径修复

> 本文件覆盖本次修复涉及的既有端点路径契约；**无新增/改名端点**，仅固化前端唯一调用路径。
> 读到本文件的人应当无需翻源码就能正确调用并处理全部异常。

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-10-05 18:01 |
| 状态 | approved |
| 负责人 | opencode |
| 适用范围 | 前端修复后的 3 个 Memory Gateway 调用路径（health/query/observer-ingest），及其余不变的关联端点 |

## 1. 文档信息与范围

| 项 | 值 |
|----|----|
| 契约版本 | v1（路径前缀 `/api/v1/memory`） |
| 适用范围 | 记忆视图合并任务中被前端改指的既有端点；定义前端唯一合法调用路径 |
| 明确不含 | 新端点、端点改名、请求/响应字段变化、旧路径 `/api/memory/*` 兼容 alias |

## 2. 环境与基础地址

| 环境 | Base URL | 说明 |
|------|----------|------|
| dev | `http://127.0.0.1:48620` | 本机默认 hub |
| prod | `http://127.0.0.1:48620` | 单机版部署，与 dev 同 loopback 实例 |

- 路径前缀：`/api/v1`（FastAPI 应用根，无网关前缀）
- 协议与端口：HTTP / 48620，仅绑定 `127.0.0.1`
- 字符编码：UTF-8；内容类型：`application/json; charset=utf-8`

## 3. 版本策略

- 版本位置：URL 路径（`/api/v1/...`）
- 当前版本：v1；破坏性变更开 `/api/v2` 并行，不原地改造
- 旧版本弃用流程：CHANGELOG 公告 → 次版本标 deprecated → 隔一个主版本下线

## 4. 认证与鉴权

| 项 | 约定 |
|----|------|
| 认证方式 | 无（hub 为本机单用户工具，loopback 直连即信任） |
| 凭证来源 / 传递位置 | 不适用（忽略 `Authorization` 头，不校验也不报错） |
| 未认证 / 越权响应 | 不产生（无 401/403 语义） |

## 5. 通用请求头

| Header | 必填 | 类型 | 默认 | 说明 |
|--------|------|------|------|------|
| `Accept` | 否 | string | `application/json` | 期望 JSON；服务端恒返回 JSON |
| `Content-Type` | POST 必填 | string | `application/json` | POST 请求体为 JSON |

## 6. 通用响应结构

本组端点为 **hub 原生结构**（非观测端点的裸对象例外）：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| 业务字段 | 视端点 | 是 | health/query/ingest 各自返回业务对象（见 §18） |
| `detail` | string/object | 错误时 | FastAPI `HTTPException` 标准错误体（错误路径唯一外壳） |
| `error` | string | 否 | 仅观测端点 `/observatory/data` 使用，本文件端点不返回 |

- 成功与失败互斥：成功无 `detail` 键；失败恒为 `{"detail": ...}`
- 裸数组 / 二进制例外：无

## 7. 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 200 | OK | 成功 | 查询/健康/上报完成 | 读业务字段 |
| 404 | ROUTE_NOT_FOUND | 路径不存在 | 误调旧前缀 `/api/memory/*` 或拼错路径 | 核对前缀必须为 `/api/v1/memory`（本契约 §17 清单） |
| 422 | PARAM_INVALID | 参数校验失败 | query 参数越界（如 limit>200）、ingest 缺 trace_id | 修正请求参数 |
| 500 | STORE_ERROR | 存储层异常 | `store.query_memories` 等抛错 | 读 `detail.error`，重试或查数据文件 |

## 8. 分页 / 排序 / 过滤

| 项 | 参数 | 类型 | 默认 | 上限 | 说明 |
|----|------|------|------|------|------|
| 分页 | 无 | — | — | — | query 一次性返回（limit 上限 200） |
| 过滤 | `kind` / `project` / `keyword` | string | null | — | 仅 `/query` 支持 |
| 排序 | — | — | — | — | 按存储内相关度（`_score`）降序，无排序参数 |

- 超限行为：`limit>200` 返回 422；无 page/total 分页字段

## 9. 字段命名与类型规范

| 项 | 规范 |
|----|------|
| 命名风格 | 后端存储层 snake_case（`entity_count`、`store_type`） |
| ID 类型 | `entity_id` 为 string |
| 布尔 | `available` 为 JSON boolean |
| 数组 | 一律空数组 `[]` 表示空，不使用 `null` |
| 可选字段 | 各端点响应结构稳定；字段缺失视为版本异常 |

## 10. 时间 / 数值 / 空值约定

- 时间格式：ISO 8601（事件 `at` 字段），UTC，毫秒精度
- 数值范围：`limit` 为 1~200 整数；计数类字段非负整数
- 空值语义：无 `null` 字段；空集合用 `[]`

## 11. 枚举全集

| 枚举 | 取值 | 含义 | 出现在哪些接口 |
|------|------|------|---------------|
| `kind`（query） | decision/context/problem/note/experience | 记忆类型过滤 | GET `/api/v1/memory/query` |
| `event_type`（ingest） | 任意 string（如 react_render_error、task_outcome） | 观察事件类型 | POST `/api/v1/memory/observer/ingest` |
| `outcome`（ingest） | success/failure/error/aborted | 事件结局 | 同上 |
| `status`（health） | ok/degraded | 网关健康态 | GET `/api/v1/memory/health` |

## 12. 幂等性与重试

| 接口 / 场景 | 幂等 | 幂等键 | 重试建议 |
|------------|------|--------|---------|
| GET /health | 是 | 不需要 | 网络错误可重试 |
| GET /query | 是 | 不需要 | 网络错误可重试 |
| POST /observer/ingest | 业务幂等（同 trace_id 事件重复入库为新记录，不影响正确性） | trace_id 可作去重键 | 500 退避 1s/2s/4s 最多 3 次 |

- 绝不可重试的状态码：422（先修参数）

## 13. 限流与配额

| 范围 | 阈值 | 窗口 | 超限响应 |
|------|------|------|---------|
| 本机调用方 | 走 hub 全局 RateLimitMiddleware（既有策略） | — | 429（如触发） |

> 前端轮询频率：网关 tab 5s 一次 health/metrics/events，远低于限流阈值。

## 14. 超时与并发

- 服务端处理超时：无应用层超时（本地文件读取，毫秒级）
- 建议客户端超时：5000ms
- 并发冲突：只读端点无冲突；ingest 追加写由存储层串行化
- 长任务处理：不适用

## 15. 文件上传与下载

| 项 | 约定 |
|------|------|
| 上传方式 | 不涉及（三个端点均无文件能力） |

## 16. 安全与脱敏

- 敏感字段：query 结果含记忆原文摘录，仅 loopback 呈现，不外发
- 传输要求：本机 HTTP（127.0.0.1）；不直接绑 0.0.0.0
- 日志禁止记录：访问日志不记响应体（记忆原文）
- 注入防护：查询经参数化存储层接口，无 SQL/命令拼接；前端 React 文本渲染转义

## 17. 接口清单

| Method | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|--------|------|------|------|------|------|---------|
| GET | `/api/v1/memory/health` | 网关健康（修复后前端唯一路径） | 无 | 是 | 全局 | 无状态 |
| GET | `/api/v1/memory/query` | 关键词/类型查询 | 无 | 是 | 全局 | 无状态 |
| POST | `/api/v1/memory/observer/ingest` | 前端报错/观察事件回传 | 无 | 业务幂等 | 全局 | 追加写 |
| GET | `/api/v1/events?limit=` | 实时事件（前端按 memory_ 前缀过滤，不变） | 无 | 是 | 全局 | 无状态 |
| GET | `/metrics` | Prometheus 文本（调用统计，不变） | 无 | 是 | 全局 | 无状态 |
| GET | `/api/v1/memory/observatory/data` | 观测投影（不变，契约见 8a966293 api 文档） | 无 | 是 | 全局 | mtime 缓存 |

> **旧前缀 `/api/memory/*`：未注册任何路由，恒 404，不提供兼容（本任务修复对象）。**

## 18. 接口明细

### GET /api/v1/memory/health

- 用途：Memory Gateway 健康状态与近 5 分钟调用指标
- 权限：无（loopback）
- 幂等：是（GET 天然幂等）
- 副作用：无（记录调用指标）

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| — | — | — | 否 | — | 无参数 | 健康查询不接受任何参数，直接 GET | `GET /api/v1/memory/health` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `status` | string | 是 | `ok`（available=true）/ `degraded` | ok |
| `mcp.available` | boolean | 是 | 存储层可用 | true |
| `mcp.store_type` | string | 是 | 存储实现 | jsonl |
| `mcp.entity_count` | int | 是 | 实体数 | 120 |
| `mcp.relation_count` | int | 是 | 关系数 | 0 |
| `mcp.data_file` | string | 是 | 数据文件路径 | C:/.../memory_store.jsonl |
| `mcp.calls_total_5m` | int | 是 | 近 5 分钟调用总数 | 12 |
| `mcp.per_tool_5m` | object | 是 | 工具 → 次数 | {"query": 8} |
| `mcp.last_error_per_tool` | object | 是 | 工具 → 最近错误 | {} |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | ROUTE_NOT_FOUND | 路径不存在 | 误调旧前缀 `/api/memory/health` | 前缀改为 `/api/v1/memory` |
| 500 | STORE_ERROR | 存储层异常 | `store.health()` 读文件失败 | 查数据文件权限后重试 |

#### 示例

请求：

```bash
curl -s "http://127.0.0.1:48620/api/v1/memory/health"
```

响应（200）：

```json
{
  "status": "ok",
  "mcp": {
    "available": true, "store_type": "jsonl",
    "entity_count": 120, "relation_count": 0,
    "data_file": "C:/.../memory_store.jsonl",
    "calls_total_5m": 12, "per_tool_5m": {"query": 8, "record": 4},
    "last_error_per_tool": {}
  }
}
```

### GET /api/v1/memory/query

- 用途：按关键词/类型/项目查询记忆
- 权限：无（loopback）
- 幂等：是（GET 天然幂等）
- 副作用：无（记录调用指标）

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| kind | query | string | 否 | null | 取值见 §11 枚举 | 记忆类型过滤 | decision |
| project | query | string | 否 | null | — | 项目名过滤 | agent-dev |
| keyword | query | string | 否 | null | — | 关键词子串匹配 | 双写校验 |
| limit | query | int | 否 | 20 | 1~200（越界 422） | 返回上限 | 50 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `total` | int | 是 | 命中总数 | 3 |
| `entities[].name` | string | 是 | 实体名 | ent-1 |
| `entities[].entityType` | string | 是 | 实体类型 | decision |
| `entities[]._score` | int | 是 | 相关度（≥0 才展示评分徽标） | 2 |
| `entities[].observations` | string[] | 是 | 观察行数组 | ["采用双写校验…"] |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 422 | PARAM_INVALID | 参数校验失败 | `limit` 越界（<1 或 >200） | 改 limit 至 1~200 |
| 500 | STORE_ERROR | 存储层异常 | `query_memories` 抛错 | 读 `detail.error` 后重试 |

#### 示例

请求：

```bash
curl -s "http://127.0.0.1:48620/api/v1/memory/query?keyword=%E5%8F%8C%E5%86%99&kind=decision&limit=50"
```

响应（200）：

```json
{ "total": 3, "entities": [
  { "name": "ent-1", "entityType": "decision", "_score": 2,
    "observations": ["采用双写校验…"] } ] }
```

### POST /api/v1/memory/observer/ingest

- 用途：前端观察事件/报错回传（ErrorBoundary 上报等）
- 权限：无（loopback）
- 幂等：业务幂等（同 trace_id 重复入库为新记录，不影响正确性，trace_id 可作去重键）
- 副作用：追加写存储层 + 广播 memory_ 事件

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| trace_id | body | string | 是 | — | 非空 | 追踪 ID | web-ui-1760000000 |
| event_type | body | string | 是 | — | 非空 | 事件类型 | react_render_error |
| payload | body | object | 否 | `{}` | 值为 JSON | 详情（前端截断 message ≤200 字符） | {"message":"x"} |
| outcome | body | string | 否 | success | 取值见 §11 枚举 | 事件结局 | failure |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `ok` | boolean | 是 | 入库成功恒为 true（失败走错误码） | true |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 422 | PARAM_INVALID | 参数校验失败 | 缺 `trace_id`/`event_type` 或类型错误 | 补齐必填字段 |
| 500 | STORE_ERROR | 存储层异常 | 追加写失败 | 读 `detail.error`，退避重试 |

#### 示例

请求（ErrorBoundary 上报）：

```bash
curl -s -X POST "http://127.0.0.1:48620/api/v1/memory/observer/ingest" \
  -H "Content-Type: application/json" \
  -d "{\"trace_id\":\"web-ui-1\",\"event_type\":\"react_render_error\",\"outcome\":\"failure\",\"payload\":{\"message\":\"x\"}}"
```

响应（200）：

```json
{"ok": true}
```

## 19. 兼容性与废弃策略

- 什么算破坏性变更：改名/删除上述路径、响应字段删除、错误外壳从 `detail` 改为裸 `error`
- 什么算兼容变更：新增可选查询参数（带默认）、响应新增可选键
- 本任务定性：**兼容修复**——前端指向纠正为既有 v1 路径，服务端零改动
- 契约校验方式：`test_web_no_legacy_memory_paths`（前端无旧路径）+ `test_api_memory_docstring_v1`（注释一致）+ TC-2 真机三连

## 20. 变更记录

| 日期 | 契约版本 | 变更内容 | 类型 | 影响方 |
|------|---------|---------|------|--------|
| 2026-10-05 | v1 | 初版：固化 3 个修复路径为唯一调用路径，声明旧前缀 404 无 alias | 兼容（前端指向修正，服务端不变） | MemoryView 网关 tab / ErrorBoundary |

## 21. 附录

- HTTP 状态码全集：`200` 成功 / `404` 旧路径或拼错 / `422` 参数错误 / `500` 存储异常
- 业务错误码全集：与 §7 一致
- 枚举全集：与 §11 一致
- 数据字典 / 术语表：
  - **记忆网关（Memory Gateway）**：hub 本地存储的读写与健康面（本文件端点）
  - **记忆观测（Observatory）**：MIO_HOME 只读投影面（`/observatory/data`，契约见 8a966293 api 文档）
  - **旧路径**：`/api/memory/*`，未注册恒 404，已由本任务修复
- 相关文档：需求 `docs/taskhub/requirement-memory-merge.md`、规格 `docs/taskhub/spec-memory-merge.md`
