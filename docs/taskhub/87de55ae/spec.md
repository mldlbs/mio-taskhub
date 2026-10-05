# 模块 Spec — 驾驶舱草稿生成 + 假设 hid 补全（task 87de55ae）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.2 |
| 最后更新 | 2026-09-28 00:40 |
| 状态 | approved |
| 负责人 | opencode（mio-taskhub） |
| 适用范围 | 本任务新增的「草稿生成」能力（后端端点 + 前端交互）与「本地假设 hid 补全」修复；不含驾驶舱既有 7 区块渲染与评审/任务链路 |
| 上游设计 | docs/taskhub/design-idea-landing.md（v1.3） |
| 需求基线 | docs/taskhub/87de55ae/requirement.md（FR-34~FR-37） |

## 1. 模块职责与边界

- 做什么：
  - `mio_taskhub/api/draft.py`：新增 `POST /ideas/{idea_id}/draft-fields`——读 LLM 配置 → 组装 prompt → 调 `/chat/completions` → 规范化并返回 8 字段草稿（**零写库**）；
  - `mio_taskhub/mio_runtime.py`：新增 `llm_config()`（读原始 `apiUrl/apiKey/model`，env 覆盖）与 `llm_enabled()`；不新增依赖（urllib）；
  - `mio_taskhub/api/ideas.py`：整表 PATCH 的 assumptions 归一化路径补 `hid`（FR-36）；
  - 前端 `IdeasView.jsx`：「✨ 生成草稿」按钮 + 合并策略 + AI 草稿提示；`api.js` 加 `draftIdeaFields()`。
- 不做什么：
  - 不自动保存草稿（生成与保存严格分离：只有「保存」产生 IdeaChange）；
  - 不做多轮对话、不做 RAG/记忆注入、不做流式输出；
  - 不改既有端点签名与响应键（只新增端点 + 补 hid 字段值）；
  - 不在服务端缓存草稿（每次点击即一次真实生成）。

## 2. 输入 / 输出

- 输入：
  - 路径参数 `idea_id`（8 位 hex，须存在）；
  - body（可选）：`{"overwrite": bool, "fields": ["goal", ...]}`——`fields` 限定生成/返回的字段子集（缺省全 8 个）；
  - 服务端配置：`~/.mio-intelligence/config.json`（`apiUrl`/`apiKey`/`model`），env `MIO_LLM_URL`/`MIO_LLM_KEY`/`MIO_LLM_MODEL` 优先；
  - 上下文：想法 `title`/`description` + 已填结构化字段（作为「已有信息」提示 LLM 保持一致）。
- 输出：
  - 200：`{draft: {goal, success_metric, constraints, out_of_scope, mvp_scope, tags[], assumptions[{hid,text,status}], risks[{text,level,mitigation}]}, source: "llm", model: "<model>", elapsed_ms: int}`；
  - 404 `idea not found`；503 `llm not configured/unavailable`；504 `llm timeout`；502 `llm returned invalid json`；
  - 422 `fields` 含非白名单键。
- 副作用：**无**（不写 DB、不产生 IdeaChange、不落盘缓存）。

## 3. 依赖

- 上游模块：
  - `mio_runtime`（配置读取、超时/子进程惯例）；
  - Mio LLM 端点（OpenAI 兼容 `POST {apiUrl}`，`Authorization: Bearer <key>`，body `{model, messages, temperature, response_format}`）；
  - 前端 `api.js`（`request()` 封装与错误处理）。
- 外部库：标准库 `urllib.request`/`json`/`time`/`uuid`；服务端 `fastapi`/`pydantic`（既有）。
- 反向依赖（不得破坏）：P0~P4 既有 ideas 端点与驾驶舱渲染；FR-15 回写端点语义（本任务只让新数据可用，不改其契约）。

## 4. 详细设计

### 4.1 生成流程（draft.py）

```mermaid
flowchart TD
  A[POST /ideas/{id}/draft-fields] --> B{LLM 配置可用?}
  B -- 否 --> C[503 llm not configured]
  B -- 是 --> D[取 idea: title/description/已有字段]
  D --> E[组装 system+user prompt<br/>要求严格 JSON 且字段齐全]
  E --> F[POST {apiUrl} 超时 30s]
  F -- URLError/连接失败 --> G[503 llm unavailable]
  F -- 超时 --> H[504 llm timeout]
  F -- 非 200 --> I[503 llm http <code>]
  F -- 200 --> J[解析 content → 截取/解析 JSON]
  J -- 失败 --> K[502 invalid json]
  J -- 成功 --> L[字段白名单过滤 + 类型规范化]
  L --> M[200 draft]
```

- **prompt 约定**：system 说明「你是产品评审助手，输出严格 JSON，键固定 8 个，中文，未提及处给保守合理值」；user 给标题、描述、已填字段清单，并要求：`goal` 用「给【谁】解决【什么问题】，因为【为什么现在】」句式、`success_metric` 用「【指标】从【现状】到【目标】，在【期限】内」、`assumptions` 每条含 `text`（2~4 条）、`risks` 每条含 `text/level(low|medium|high)/mitigation`、`tags` 逗号概念 2~4 个；
- **解析健壮性**：优先 `response_format={"type":"json_object"}`；再尝试从 ```json 代码块提取；最后取首个 `{` 到末个 `}` 子串；
- **规范化**：字符串字段 `str().strip()`；`tags` 去重去空；`assumptions` 仅保留 dict 且必有 `text`（补 `hid=as-<8hex>`、`status="open"`）；`risks` 缺失 `level` 补 `medium`；未知键丢弃；返回体**只含白名单键**；
- **耗时统计**：`elapsed_ms` 由 `time.perf_counter()` 计算，便于前端提示与排障。

### 4.2 前端交互与合并策略（IdeasView.jsx）

- 按钮位置：「✏️ 结构化字段」块头部，`编辑` 左侧，文案 `✨ 生成草稿`；`generating` 为真时禁用并显示 `生成中…`；
- 点击流程：`api.draftIdeaFields(id)` → 成功后 `setFieldForm(merge(draft))` 且 `setFieldEdit(true)`，并置 `draftNote=true`（表单顶部渲染「AI 草稿，请核对后保存」）；
- **合并函数** `mergeDraft(current, draft, overwrite)`：`overwrite=false`（默认）时仅当 `current[k]` 为空（`''`/`[]`）才用草稿值；`overwrite=true` 由「覆盖已有内容」勾选触发，弹 `window.confirm` 二次确认；
- 失败处理：503 → `LLM 未配置或不可用，可手动填写`；504 → `生成超时，请重试或手动填写`；其余 → 透传后端 detail；**不改变**面板既有的 7 区块渲染；
- `tags` 在表单里是逗号串、`assumptions` 是换行串、`risks` 是 JSON 串——草稿按同一形态回填，复用 `saveFieldEdit` 现有解析路径（不新增保存分支）。

### 4.3 hid 补全（ideas.py）

- 位置：整表 `PATCH /ideas/{id}` 的 assumptions 归一化（`_normalize_assumption_rows` 或在 `_validate_json_field("assumptions", ...)` 之后的写回前）；
- 规则：对每个 dict 条目，若 `hid` 与 `id` 均缺失 → `setdefault("hid", "as-" + uuid4().hex[:8])`；同一请求内 `seen` 集合去重（碰撞重生成）；既有 `hid`/`id` 原样保留；字符串条目保持原样（由既有逻辑转 dict 时同样补 hid）；
- 与 diff 的关系：hid 作为该条目的稳定标识随整表 diff 落 `IdeaChange`（键 `assumptions`），便于后续 `assumptions[hid]` 单条回写（FR-2/FR-15）。

## 5. 异常与边界情况

| 场景 | 处理方式 |
|------|---------|
| LLM 未配置（config.json 无 apiKey/apiUrl） | 503 `llm not configured`；前端提示「可手动填写」；不发起网络请求 |
| apiUrl 指向不可达地址/网络错误 | `urllib.error.URLError` → 503 `llm unavailable` |
| 响应超时（>30s） | `socket.timeout`/`TimeoutError` → 504 `llm timeout`（前端可重试） |
| 上游返回 4xx/5xx | 503 `llm http <code>`；**不回显上游原文**（避免泄漏 key/内部信息） |
| 返回内容非 JSON / 缺字段 | 502 `llm returned invalid json`；缺字段按规范化补默认值（不报错，草稿可编辑） |
| 想法不存在 | 404 `idea not found`（与既有端点一致） |
| `fields` 含非法键 | 422 `unknown fields: ...` |
| 生成期间用户保存面板 | 生成端点不写库，二者无冲突；保存以表单当前值为准 |
| 已有非空字段 | 默认不覆盖；`overwrite=true` 才覆盖且需前端 confirm |
| 测试环境 | 测试一律 monkeypatch LLM 调用（不打外网）；`MIO_HOME` 指向不存在路径时视为未配置（fail-open 到 503） |
