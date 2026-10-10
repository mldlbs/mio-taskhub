# 接口契约（API Contract）· memory-observatory

> 本文件要求事无巨细：每个参数、每个响应字段、每个错误码都有类型、必填性、约束、示例；
> 读到本文件的人应当**无需翻源码**就能正确调用并处理全部异常。

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-10-05 15:24 |
| 状态 | approved |
| 负责人 | opencode |
| 适用范围 | `GET /api/v1/memory/observatory/data`（只读记忆观测投影），消费方为 MemoryObservatoryView 及本机调用方 |

## 1. 文档信息与范围

| 项 | 值 |
|----|----|
| 契约版本 | v1 |
| 适用范围 | memory-observatory 投影端点：把 MIO_HOME（`memory.jsonl` + `experience_reuse.jsonl`）只读投影为图谱 JSON；调用方为 Rail「记忆观测」视图与本机调试 |
| 明确不含 | 记忆写入/记录（`record`/`observer/ingest`/`experience/reuse` 属既有写端点）、权威语义检索（`query`）、生命周期管理（archive/merge/forget）、Mio MCP 工具面、任何写方法 |

## 2. 环境与基础地址

| 环境 | Base URL | 说明 |
|------|----------|------|
| dev | `http://127.0.0.1:48620` | 本机默认 hub（开发与日常使用同一实例） |
| staging | `http://127.0.0.1:48621` | 预发验证：同机异端口实例（需要时手动起） |
| prod | `http://127.0.0.1:48620` | 单机版部署，与 dev 同 loopback 实例（本机工具，无云端环境） |

- 路径前缀：`/api/v1`（FastAPI 应用根，无额外网关前缀）
- 协议与端口：HTTP / 48620（默认），仅绑定 `127.0.0.1`
- 网关 / 反向代理差异：无网关，客户端直连 hub 进程；路径不重写
- 字符编码：UTF-8
- 内容类型：`application/json; charset=utf-8`

## 3. 版本策略

- 版本位置：URL 路径（`/api/v1/...`）
- 当前版本：v1
- 版本号规则：主版本进路径；破坏性契约变更开 `/api/v2` 新路径并行，不原地改造 v1
- 多版本并存策略：v1 与 v2 至少并行一个发版窗口，CHANGELOG 标注弃用时间表
- 旧版本弃用流程：CHANGELOG 公告 → 下个次版本标 deprecated → 隔一个主版本下线

## 4. 认证与鉴权

| 项 | 约定 |
|----|------|
| 认证方式 | 无（hub 为本机单用户工具，loopback 直连即信任） |
| 凭证来源 | 不适用（不发放 token） |
| 传递位置 | 不适用（忽略 `Authorization` 头，不校验也不报错） |
| 有效期 | 不适用 |
| 刷新方式 | 不适用 |
| 权限模型 | 无角色/权限点；端点本身只读，随 hub 部署边界（127.0.0.1）保护 |
| 未认证响应 | 不产生（无 401 语义；如未来加网关鉴权，401 表示未带凭证） |
| 越权响应 | 不产生（无 403 语义；只读端点无资源级权限差异） |

## 5. 通用请求头

| Header | 必填 | 类型 | 默认 | 说明 |
|--------|------|------|------|------|
| `Accept` | 否 | string | `application/json` | 期望 JSON；服务端恒返回 JSON |
| `X-Request-Id` | 否 | string | — | hub 未要求，透传无副作用（仅本端点不消费） |
| `Authorization` | 否 | string | — | 本机无鉴权，携带也不校验（见 §4） |

## 6. 通用响应结构

本端点是**例外登记**：不使用 hub 其他接口的 `{code, message, data, request_id}` 外壳，
成功与失败均为裸对象（与 mneme `/api/data` 契约逐字对齐，迁移不破坏）。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `entities` | object[] | 成功时必返 | 实体数组（详见 §18 响应字段）；成功外壳的三键之一 |
| `relations` | object[] | 成功时必返 | 关系数组，端点指向 `entities[].id` |
| `meta` | object | 成功时必返 | 视图与统计元信息（generatedAt/showLogs/skipped/counts/evidence） |
| `error` | string | 失败时必返 | 失败外壳唯一键；成功时**不出现**该键（缺失即成功） |

- `data` 为 null 与 `{}` 的语义差异：不适用——本端点没有 `data` 键；成功恒为含三键的完整对象，
  失败恒为 `{"error": "..."}` 单键对象，两种外壳互斥、不混用
- 裸数组 / 二进制例外：无（成功是对象不是裸数组）；本节登记的唯一例外是「无 code/message 外壳」本身

## 7. 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 200 | OK | 成功 | 投影构建完成（含空投影：`memory.jsonl` 缺失时也是 200） | 读 `entities/relations/meta` |
| 404 | ROUTE_NOT_FOUND | 路径不存在 | 请求了 `observatory/data` 以外的路径 | 核对路径，仅本文档 §17 清单内的路径有效 |
| 422 | PARAM_INVALID | 参数校验失败 | `logs` 传入非布尔可解析值（如 `logs=abc`），FastAPI 校验拒绝 | 只传 `logs=1`/`true`/`yes` 或省略该参数 |
| 500 | PROJECTION_ERROR | 投影构建异常 | 读 `memory.jsonl`/`experience_reuse.jsonl` 发生 IO 错误（权限/磁盘）或投影内部异常 | 查 MIO_HOME 文件权限；修复后可重试（见 §12） |

> `meta.skipped.{mio,reuse} > 0` 不是错误：坏行被跳过，响应仍为 200（见 §18）。

## 8. 分页 / 排序 / 过滤

| 项 | 参数 | 类型 | 默认 | 上限 | 说明 |
|----|------|------|------|------|------|
| 页码 | — | — | — | — | 不分页：全量投影一次返回 |
| 每页条数 | — | — | — | — | 同上 |
| 排序 | — | — | — | — | `entities` 按源文件出现顺序稳定排列，服务端不提供排序参数 |
| 视图过滤 | `logs` | boolean | false | — | `false`=观察视图（隐藏 log 实体）；`true`=全量；env 强制时失效 |
| 本地过滤 | — | — | — | — | 由调用方在已加载数据内完成（前端「本地过滤」），非服务端能力 |

- 分页响应字段：不适用（无 total/page/size/has_next）
- 超限行为：无上限截断；超大 MIO_HOME 的性能由 mtime 缓存与默认观察视图裁剪兜底（见 spec「性能与容量」）
- 多字段排序的优先级规则：不适用

## 9. 字段命名与类型规范

| 项 | 规范 |
|----|------|
| 命名风格 | 沿用 mneme 原始契约混排：实体级 `reuse_score` 为 snake_case，`meta` 及其嵌套为 camelCase（`generatedAt`/`showLogs`/`memTotal`）；**禁止统一改造**（破坏迁移兼容）。反例：`totalScore`/`total_score` 自造字段 |
| ID 类型 | `entities[].id` 为响应内 int 序号（0 起、仅本次响应有效）；业务键用 `entities[].name`（`mio-<记忆id>` 等字符串），跨请求稳定 |
| 布尔 | `meta.showLogs` 为 JSON boolean；查询参数 `logs` 接受 `1`/`true`/`yes`（大小写不敏感） |
| 金额 | 不适用 |
| 数组 | 一律空数组 `[]` 表示空，不使用 `null`（`obs`/`entities`/`relations` 同规则） |
| 可选字段 | 本契约全部字段**每次必返**（成功外壳三键齐全）；字段缺失 = 契约破坏，调用方应视为版本异常而非「无数据」 |

## 10. 时间 / 数值 / 空值约定

- 时间格式：ISO 8601（`meta.generatedAt`，例 `2026-10-05T07:20:00.000Z`）
- 时区：UTC
- 时间精度：毫秒
- 数值范围与溢出处理：`reuse_score` 为非负 int（公式下限 0，无上限封顶）；`counts/evidence/skipped` 为非负 int；JS 侧 safe integer 范围内无溢出风险
- 空值语义：无 `null` 字段；空集合用 `[]`；源文件缺失时 `counts/evidence` 全 0 而非缺失

## 11. 枚举全集

| 枚举 | 取值 | 含义 | 出现在哪些接口 |
|------|------|------|---------------|
| `entities[].type` | `rule` / `context` / `problem` / `note` / `experience` / `log` / `source` / `project` | 记忆类型（decision→rule）、自动日志、来源节点、项目节点 | GET `/api/v1/memory/observatory/data` |
| `relations[].rel` | `来自` / `属于` | 记忆→来源 / 记忆→项目 | 同上 |
| `logs`（query） | `true` / `false` | 视图开关（默认 false） | 同上 |

> 调用方必须前向兼容未知 `type`（未来 Mio 新增 kind 时服务端兜底映射 `note`，但 `source`/`project` 虚拟类型不变）；
> 未知取值按默认样式渲染，不得崩溃。

## 12. 幂等性与重试

| 接口 / 场景 | 幂等 | 幂等键 | 重试建议 |
|------------|------|--------|---------|
| GET /observatory/data | 是（GET 天然幂等；同文件状态响应等价，`generatedAt` 会变） | 不需要 | 500 可安全重试；422 属参数错误，改参数后再发 |
| 并发重复请求 | 是 | 不需要 | 服务端缓存锁串行重建，无副作用 |

- 安全重试的状态码：`500`（退避 1s/2s/4s，最多 3 次）；`429` 本端点不产生（见 §13）
- 绝不可重试的状态码：无写语义，不存在非幂等写；`422` 重试无意义（先修参数）
- 重试间隔与退避策略：指数退避 1s→2s→4s，最多 3 次后报错

## 13. 限流与配额

| 范围 | 阈值 | 窗口 | 超限响应 |
|------|------|------|---------|
| 本机调用方 | 不限流（loopback 单机工具，无配额） | — | — |

> 若未来经反代暴露，由反代统一限流并返回 HTTP 429 + `Retry-After`；本应用自身不实现限流。

## 14. 超时与并发

- 服务端处理超时：无应用层超时；冷构建实测 733 条 <50ms、5k 条 <200ms，缓存命中 <5ms
- 建议客户端超时：5000ms（覆盖冷构建最坏情况）
- 乐观锁 / 版本号字段：不适用（只读）
- 并发冲突响应：无冲突语义；缓存重建由 `threading.Lock` 串行化，并发读各自拿到一致快照
- 长任务的处理方式：不适用（同步即时返回）

## 15. 文件上传与下载

| 项 | 约定 |
|----|------|
| 上传方式 | 不涉及（纯查询端点，无文件上传/下载能力） |

## 16. 安全与脱敏

- 敏感字段清单及脱敏规则：`entities[].obs/title` 是记忆原文摘录，可能含项目内部信息——仅经 loopback 呈现给本机用户，不外发、不写日志
- 传输要求：本机 HTTP（127.0.0.1）；如需远程访问经 SSH 隧道，不直接绑 0.0.0.0
- 日志禁止记录的字段：访问日志只记方法/路径/状态/耗时/实体数，不记响应体
- 越权与注入防护：无鉴权面；输出由 React 文本渲染天然转义（禁 `dangerouslySetInnerHTML`），无 SQL/命令拼接

## 17. 接口清单

| Method | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|--------|------|------|------|------|------|---------|
| GET | `/api/v1/memory/observatory/data` | 只读记忆观测投影（MIO_HOME→图谱 JSON） | 无（本机只读） | 是 | 无 | 静态无状态（不关联状态机） |

## 18. 接口明细

### GET /api/v1/memory/observatory/data

- 用途：把 MIO_HOME 两源文件投影为图谱 JSON（实体/关系/meta），供 Rail「记忆观测」渲染
- 权限：无（loopback 只读）
- 幂等：是（GET）
- 副作用：无写操作；仅进程内 mtime 缓存
- 关联状态：无状态机，任意时刻可调

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| `logs` | query | boolean | 否 | false | 接受 `1`/`true`/`yes`（大小写不敏感）；env `MIO_OBSERVATORY_HIDE_LOGS` 为真时被忽略 | `true` 返回含 log 实体的全量视图 | `?logs=1` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `entities` | object[] | 是 | 实体数组 | `[...]` |
| `entities[].id` | int | 是 | 响应内序号，0 起 | `0` |
| `entities[].name` | string | 是 | 唯一业务键 | `mio-abc123` |
| `entities[].type` | string | 是 | 枚举见 §11 | `rule` |
| `entities[].title` | string | 是 | 首句 ≤60 字，超出截断 + `…` | `采用双写校验…` |
| `entities[].obs` | string[] | 是 | 首元素为 meta 行，其后正文行 | `["来源 mcp · ...", "..."]` |
| `entities[].reuse_score` | int | 是 | 复用热度（非权威评分），≥0 | `8` |
| `relations` | object[] | 是 | 关系数组 | `[...]` |
| `relations[].source` | int | 是 | 源实体 id | `0` |
| `relations[].target` | int | 是 | 目标实体 id | `1` |
| `relations[].rel` | string | 是 | 枚举见 §11 | `来自` |
| `meta.generatedAt` | string | 是 | ISO 8601 UTC | `2026-10-05T07:20:00.000Z` |
| `meta.showLogs` | boolean | 是 | 实际生效视图 | `false` |
| `meta.skipped.mio` | int | 是 | memory.jsonl 坏行数 | `0` |
| `meta.skipped.reuse` | int | 是 | experience_reuse.jsonl 坏行数 | `0` |
| `meta.counts.entities` | int | 是 | 实体数（数组长度） | `45` |
| `meta.counts.relations` | int | 是 | 关系数（数组长度） | `60` |
| `meta.evidence.memTotal` | int | 是 | 全量记忆数（含被视图隐藏的 log） | `733` |
| `meta.evidence.memWithEvidence` | int | 是 | reuse>0 的记忆数 | `20` |
| `meta.evidence.reuseRecords` | int | 是 | 复用证据文件总行数 | `20` |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 422 | PARAM_INVALID | 参数校验失败 | `logs` 非可解析布尔 | 改传 `logs=1` 或省略 |
| 500 | PROJECTION_ERROR | 投影构建异常 | 源文件 IO 失败或内部异常 | 查 MIO_HOME 权限后退避重试 |

#### 示例

请求：

```bash
curl -s "http://127.0.0.1:48620/api/v1/memory/observatory/data?logs=1"
```

响应（200）：

```json
{
  "entities": [
    {"id": 0, "name": "mio-abc123", "type": "rule",
     "obs": ["来源 mcp · 项目 agent-dev · decision #架构", "第一行清洗后内容"],
     "title": "首句标题", "reuse_score": 8}
  ],
  "relations": [{"source": 0, "target": 1, "rel": "来自"}],
  "meta": {
    "generatedAt": "2026-10-05T07:20:00.000Z", "showLogs": true,
    "skipped": {"mio": 0, "reuse": 0},
    "counts": {"entities": 733, "relations": 900},
    "evidence": {"memTotal": 733, "memWithEvidence": 20, "reuseRecords": 20}
  }
}
```

失败（500）：

```json
{"error": "permission denied: C:/Users/.../.mio-intelligence/memory.jsonl"}
```

## 19. 兼容性与废弃策略

- 什么算破坏性变更：删除/改名 `entities[]`/`meta` 字段、`type` 枚举删值、`rel` 改义、错误外壳从 `{error}` 改为 `detail` 包装
- 什么算兼容变更：新增实体类型枚举值、`meta` 新增可选键、新增查询参数（带默认）
- 变更通知方式与提前期：CHANGELOG + GitHub Release 说明，随次版本发布
- 废弃流程：CHANGELOG 标 deprecated → `/api/v2` 并行一个发版窗口 → 下线 v1
- 契约校验方式：pytest 契约用例（响应键集合与枚举断言，见 spec 测试计划 `test_api_endpoint`）随每次跑量

## 20. 变更记录

| 日期 | 契约版本 | 变更内容 | 类型 | 影响方 |
|------|---------|---------|------|--------|
| 2026-10-05 | v1 | 初版：只读投影端点（迁移自 mneme `GET /api/data`，契约逐字对齐） | 兼容（新增接口） | MemoryObservatoryView（首发消费方） |

## 21. 附录

- HTTP 状态码本端点相关全集：`200` 成功 / `404` 路径不存在 / `422` 参数错误 / `500` 投影异常
- 业务错误码全集：与 §7 一致（`PROJECTION_ERROR`、`PARAM_INVALID`、`ROUTE_NOT_FOUND`）
- 枚举全集：与 §11 一致（`type` 8 值、`rel` 2 值、`logs` 布尔）
- 数据字典 / 术语表：
  - **观察视图**：默认视图，隐藏 `kind=note` 且 tags 含 `task-outcome` 的自动日志
  - **reuse_score（复用热度）**：证据驱动的呈现分数，非权威价值判断；无复用证据 ≠ 无价值
  - **证据覆盖率**：`memWithEvidence/memTotal`，头部显式展示
  - **ego view（出入边）**：以选中实体为中心的关系列表，不渲染全图
- 相关文档：需求 `docs/taskhub/requirement-memory-observatory.md`、规格 `docs/taskhub/spec-memory-observatory.md`
