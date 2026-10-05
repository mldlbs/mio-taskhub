# API 设计 — 驾驶舱草稿生成 + 假设 hid 补全（task 87de55ae）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.2 |
| 契约版本 | idea-draft-p5 |
| 状态 | approved |
| 最后更新 | 2026-09-28 00:41 |
| 负责人 | opencode（mio-taskhub） |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.3） |
| 需求基线 | docs/taskhub/87de55ae/requirement.md（FR-34~FR-37） |
| 关联任务 | 87de55ae（branch task-87de55ae） |
| 前序契约 | docs/taskhub/eb248b6a/api.md（P4）；P3 `d4bdf530`、P2 `286dd112`、P1 `8442e38d`、P0 `2fe56e6a` |

## 文档信息与范围

本文覆盖 P5 的两处变更：① **新增** `POST /api/v1/ideas/{idea_id}/draft-fields`（LLM 草稿生成，纯读不落库）；② **行为补全** `PATCH /api/v1/ideas/{idea_id}`（整表更新 assumptions 时为缺 `hid` 的条目补生成 hid，签名与响应键不变）。

**明确不含**：

- 自动保存草稿的端点：不存在（保存仍走既有 `PATCH /ideas/{id}`）；
- LLM 配置的读写端点：不存在（配置由 `mio config llm` 管理，hub 只读 `config.json`）；
- 流式/多轮生成、记忆注入接口：不存在；
- 字段模型变更：不存在（不新增字段）。

## 环境与基础地址

| 环境 | Base URL | 说明 |
|---|---|---|
| dev（本机常驻） | `http://127.0.0.1:48620/api/v1` | PyInstaller hub，SQLite 单库 |
| test（单测） | `http://testserver/api/v1` | FastAPI ASGITransport 进程内直调 |
| staging | 不适用 | 无 staging 形态 |
| prod | `http://127.0.0.1:48620/api/v1` | 与 dev 同构（本机单用户） |

上游 LLM 基础地址来自 `~/.mio-intelligence/config.json` 的 `apiUrl`（默认示例 `https://api.deepseek.com/chat/completions`），可由 env `MIO_LLM_URL` 覆盖。

## 认证与鉴权

| 项 | 约定 |
|---|---|
| 客户端→hub | **无鉴权**（本机单用户，绑定 127.0.0.1） |
| hub→LLM | `Authorization: Bearer <apiKey>`；key 仅进程内使用，**不写日志、不进响应** |
| 401/403 | 客户端侧不返回；LLM 侧 401/403 统一映射为 503 `llm http 401/403`（不回显上游原文） |

## 通用请求头

| 头 | 值 | 必填 | 说明 |
|---|---|---|---|
| Content-Type | `application/json; charset=utf-8` | 带 body 时必填 | 缺失 → FastAPI 解析失败 422 |
| Accept | `application/json` | 否 | 响应恒为 JSON |

## 通用响应结构

REST 直出式，无统一外壳（与 P0~P4 一致）。失败响应统一：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `detail` | string | 是 | 人类可读原因（FastAPI 惯例） |

`draft-fields` 成功响应为**裸对象**：`{draft, source, model, elapsed_ms}`（见 §接口明细）。

## 统一错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `idea_not_found` | 想法不存在 | `idea_id` 查无 | 核对 id |
| 422 | `unknown_fields` | `fields` 含非法键 | 不在 8 字段白名单 | 改为白名单键 |
| 422 | `validation_error` | 其余参数校验失败 | body 类型不符 | 按 detail 修正 |
| 502 | `llm_invalid_json` | LLM 返回非结构化 JSON | content 解析失败且无法截取 | 重试；仍失败视为上游问题 |
| 503 | `llm_not_configured` | LLM 未配置 | `config.json` 缺 `apiUrl`/`apiKey` | `mio config llm --url --model --key` |
| 503 | `llm_unavailable` | LLM 不可达 | 网络错误/上游 4xx/5xx | 稍后重试或手动填写 |
| 504 | `llm_timeout` | LLM 超时 | 超过 30s 预算 | 重试或手动填写 |
| 500 | `internal_error` | 服务端异常 | 非预期错误 | 重试；查 hub 日志 |

## 字段命名与类型规范

| 项 | 约定 | 反例 |
|---|---|---|
| 命名 | `snake_case` | `successMetric` ✗ |
| hid | `as-<8 位 hex>`；既有条目保留原 hid | 复用他人 hid ✗ |
| 布尔 | JSON `true/false` | `"1"` ✗ |
| 数组 | 空数组 `[]` 表示无数据 | null 表示数组 ✗ |
| 缺失 vs null | 草稿恒返回全部请求字段（不缺键）；值为空用 `""`/`[]` | 缺键代替空值 ✗ |

## 枚举全集

| 枚举 | 取值全集 |
|---|---|
| 草稿字段白名单 | `goal`, `success_metric`, `constraints`, `out_of_scope`, `mvp_scope`, `tags`, `assumptions`, `risks` |
| `risks[].level` | `low`, `medium`, `high`（缺失补 `medium`） |
| `assumptions[].status` | `open`, `active`, `validated`, `rejected`（草稿默认 `open`） |
| `source` | `llm`（预留 `rule`，本期不产生） |

## 幂等性与重试

| 接口 | 幂等 | 说明 | 重试建议 |
|---|---|---|---|
| GET /ideas/{id} | 是 | 纯读 | 可重试 |
| POST /ideas/{id}/draft-fields | **是（无副作用）** | 不写库、无缓存；同输入可能产出不同文案（LLM 非确定） | 可重试（504/503 建议退避 2s/5s） |
| PATCH /ideas/{id} | 是 | 整表 + 补 hid；同值重放为 no-op（hid 已存在则不重生成） | 可重试 |

安全重试：上述全部（生成端点重试仅消耗 LLM 配额，不产生数据副作用）。

## 限流与配额

本机单用户无网关限流。**配额提示**：生成端点每次调用消耗一次上游 LLM 请求（约数百 token）；前端按钮 disabled 防连点；同一次点击只发一次请求。

## 超时与并发

| 项 | 值 |
|---|---|
| 生成端点上游超时 | **30s**（连接 10s / 读 30s）；超时 → 504 |
| 前端等待 | 与后端一致（30s+），期间按钮 loading、可取消（关闭表单不影响后端完成） |
| 并发 | 同一 idea 并发生成互不影响（无共享状态、不写库）；SQLite 无写竞争 |
| 配置读取 | 每次请求读取 `config.json`（体积小），失败按未配置处理（503），不缓存 key |

## 分页

不适用：生成端点为单次计算，返回固定 8 字段草稿；idea 读取为单资源。

## 时间 / 数值 / 空值约定

| 项 | 约定 |
|---|---|
| 时间 | 无时间字段返回；`elapsed_ms` 为整数毫秒 |
| 数值 | 无浮点金额；`elapsed_ms` ≥ 0 |
| 空值 | 字符串 `""`；数组 `[]`；`model` 为实际使用的模型名（配置值原样，不含 key） |
| 截断 | 上游 prompt 中描述超过 2000 字符时截断（保留前 2000 字符 + 省略标记） |

## 文件上传与下载

不适用：本组接口无文件传输。

## 安全与脱敏

| 项 | 说明 |
|---|---|
| apiKey | 仅从 `config.json`/env 读取并在请求头使用；**不写日志、不进响应、不进异常文案** |
| 错误文案 | 上游错误只回 `llm http <code>`，不回显上游 body（可能含内部信息） |
| 网络暴露 | hub 仅绑定 127.0.0.1；出站仅访问配置的 LLM apiUrl |
| 数据外发 | 生成会把「想法标题 + 描述 + 已填字段」发给 LLM——属用户显式点击触发的预期行为；面板按钮附提示 |
| 注入 | 参数经 pydantic 校验；prompt 中用户内容只作数据拼接（不拼接为指令），并要求模型输出 JSON |

## 版本策略

| 项 | 约定 |
|---|---|
| 路径版本 | `/api/v1`；破坏性变更 → `/api/v2` |
| 新增端点 | 兼容变更（新增路径） |
| PATCH 补 hid | **兼容变更**：既有条目 hid 不变；仅对缺失者补值（旧客户端忽略该键即可） |
| 契约版本 | `idea-draft-p5` |

## 兼容性与废弃策略

- `PATCH /ideas/{id}` 响应键与既有完全一致（只多出 assumptions 条目内的 `hid` 值）——P0~P4 契约用例回归锁定（`test_import_and_patch_dual_write_p1_contract_unchanged` 等）；
- 补 hid 后 FR-15 单条回写对新数据可用；旧数据（无 hid）行为不变，直到其被整表 PATCH 一次；
- 无废弃对象；未来若更换 LLM 供应商，只需改 `config.json`（契约不变）。

## 接口清单

| 方法 | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|---|---|---|---|---|---|---|
| GET | `/api/v1/ideas/{idea_id}` | 读想法（生成上下文来源） | 无鉴权 | 是 | 无 | 只读 |
| POST | `/api/v1/ideas/{idea_id}/draft-fields` | **P5 新增**：LLM 生成 8 字段草稿（不落库） | 无鉴权 | 是（无副作用） | 无（前端防连点） | 只读 |
| PATCH | `/api/v1/ideas/{idea_id}` | 整表更新（P5：为缺 hid 的 assumptions 补 hid） | 无鉴权 | 是 | 无 | 进 IdeaChange |
| PATCH | `/api/v1/ideas/{idea_id}/assumptions/{hid}` | 单条假设回写（P5：对新数据也可用） | 无鉴权 | 是 | 无 | 进 IdeaChange |

## 接口明细

### POST /api/v1/ideas/{idea_id}/draft-fields

- 用途：由 LLM 生成驾驶舱 8 字段草稿（FR-34）
- 副作用：无（不写库、不产生 IdeaChange、无缓存）
- 关联状态：任意状态想法均可调用

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|---|---|---|---|---|---|---|---|
| `idea_id` | path | string | 是 | — | 8 位 hex，须存在 | 想法 ID | 50acfb47 |
| `overwrite` | body | boolean | 否 | false | 仅影响返回语义（服务端不做合并，合并由前端按此字段决策） | 是否允许覆盖已有非空字段 | true |
| `fields` | body | array[string] | 否 | 全部 8 项 | 元素须在白名单内 | 限定生成字段子集 | `["goal","risks"]` |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|
| `draft.goal` | string | 是 | 目标（给谁解决什么问题、为什么现在） | 给【值班调度员】解决【…】的问题，因为【…】 |
| `draft.success_metric` | string | 是 | 成功标准（指标从现状到目标、期限） | 【告警→接单时长】从【20 分钟】到【≤5 分钟】，在【3 个月内】 |
| `draft.constraints` | string | 是 | 约束（时间/预算/人手/合规） | 时间：6 周；预算：不新增服务器 |
| `draft.out_of_scope` | string | 是 | 不做什么 | 不做预测模型；不做移动端 |
| `draft.mvp_scope` | string | 是 | MVP 范围 | 仅 12 个易涝点位：告警聚合 → 一键分派 |
| `draft.tags` | array[string] | 是 | 标签（去重去空） | `["高风险","合规"]` |
| `draft.assumptions[].hid` | string | 是 | 服务端生成 `as-<8hex>` | as-9f21c0ab |
| `draft.assumptions[].text` | string | 是 | 假设正文 | 值班员愿意点「接单」 |
| `draft.assumptions[].status` | string | 是 | 默认 `open` | open |
| `draft.risks[].text` | string | 是 | 风险描述 | 告警误报导致误分派 |
| `draft.risks[].level` | string | 是 | `low`/`medium`/`high` | high |
| `draft.risks[].mitigation` | string | 是 | 缓解措施 | 首月只提示不自动派单 |
| `source` | string | 是 | 生成来源 | llm |
| `model` | string | 是 | 使用的模型名（不含 key） | deepseek-flash |
| `elapsed_ms` | integer | 是 | 生成耗时毫秒 | 4820 |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `idea_not_found` | 想法不存在 | id 查无 | 核对 id |
| 422 | `unknown_fields` | fields 非法 | 含白名单外键 | 用白名单键 |
| 502 | `llm_invalid_json` | 上游返回非 JSON | 解析失败 | 重试 |
| 503 | `llm_not_configured` / `llm_unavailable` | 未配置 / 不可达 / 上游非 2xx | 见 §统一错误码 | 配 LLM 或手动填写 |
| 504 | `llm_timeout` | 超 30s | 上游慢 | 重试或手动填写 |

#### 示例

```http
POST /api/v1/ideas/50acfb47/draft-fields
{"overwrite": false}
```
```json
// 200（节选）
{"draft": {"goal": "给【汛期值班调度员】解决【告警靠电话汇总、平均 20 分钟才分派】的问题，因为【汛期窗口只有 2 小时】",
  "success_metric": "【告警→接单平均时长】从【20 分钟】到【≤5 分钟】，在【3 个月内】",
  "constraints": "时间：汛期前上线；预算：不新增服务器；合规：数据不出内网",
  "out_of_scope": "不做内涝预测模型；不做移动端 App",
  "mvp_scope": "仅 12 个易涝点位：告警聚合 → 一键分派",
  "tags": ["高风险", "合规"],
  "assumptions": [{"hid": "as-9f21c0ab", "text": "值班员愿意在系统里点「接单」", "status": "open"}],
  "risks": [{"text": "告警误报导致误分派", "level": "high", "mitigation": "首月只提示不自动派单"}]},
 "source": "llm", "model": "deepseek-flash", "elapsed_ms": 4820}
```
```json
// 503（未配置）
{"detail": "llm not configured"}
// 504（超时）
{"detail": "llm timeout"}
```

### PATCH /api/v1/ideas/{idea_id}

- 用途：整表更新（P5 行为补全：assumptions 缺 hid 时补生成；FR-36）
- 副作用：进 IdeaChange（键 `assumptions` 等）

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 约束 | 说明 |
|---|---|---|---|---|---|
| `idea_id` | path | string | 是 | 须存在否则 404 | 想法 ID |
| `assumptions` | body | array | 否 | 元素为 dict 或 string；dict 缺失 `hid`/`id` 时服务端补 | 假设行 |
| 其余白名单字段 | body | 见 P0 契约 | 否 | 键缺失=不修改 | 目标/风险/标签等 |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|---|---|---|---|---|
| （body） | object | 是 | 更新后的完整 idea 资源（键与 P0 一致） | `{"id":"50acfb47","version":3}` |
| `assumptions[].hid` | string | 是 | **P5**：缺失者被补为 `as-<8hex>`；既有值不变 | as-1f0c9d2e |
| `version` | integer | 是 | 真实变更 +1 | 3 |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|---|---|---|---|---|
| 404 | `idea_not_found` | 想法不存在 | id 查无 | 核对 id |
| 422 | `validation_error` | assumptions 类型非法 | 非数组 / 元素非 dict/string | 按 detail 修正 |

#### 示例

```http
PATCH /api/v1/ideas/50acfb47
{"assumptions": [{"text": "值班员愿意点接单"}, {"text": "告警延迟 < 30s"}]}
```
```json
// 200（节选：两条都被补 hid，可随后用于单条回写）
{"id": "50acfb47", "version": 3,
 "assumptions": [{"hid": "as-1f0c9d2e", "text": "值班员愿意点接单"},
                  {"hid": "as-77b31a05", "text": "告警延迟 < 30s"}]}
```

## 附录

### FR 追溯

| FR | 本文落点 |
|---|---|
| FR-34 | §接口明细 `POST /ideas/{id}/draft-fields`（全块）、§统一错误码、§超时与并发 |
| FR-35 | §通用响应结构（draft 裸对象）、§接口明细 draft-fields 请求参数（`overwrite` 语义）、§安全与脱敏 |
| FR-36 | §接口明细 `PATCH /ideas/{id}`（hid 补全）、§幂等性与重试、§兼容性与废弃策略 |
| FR-37 | §兼容性与废弃策略（回归承诺）、§变更记录 |

### 相关文档

- 需求：`docs/taskhub/87de55ae/requirement.md`
- 状态/模块设计：`docs/taskhub/87de55ae/spec.md`
- 前序契约：`docs/taskhub/eb248b6a/api.md`（P4）、`docs/taskhub/d4bdf530/api.md`（P3）

## 变更记录

| 版本 | 日期 | 变更 | 类型 | 作者 |
|---|---|---|---|---|
| v1.0 | 2026-09-28 | 初版：新增 draft-fields 端点；PATCH 整表补 hid | 兼容 | opencode |
