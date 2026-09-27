# ✅ 测试验收 — 驾驶舱草稿生成 + 假设 hid 补全（task 87de55ae）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 状态 | planned |
| 任务 | 87de55ae（branch task-87de55ae，run 5f960b5a） |
| 需求 | docs/taskhub/87de55ae/requirement.md（FR-34~FR-37） |
| 设计 | docs/taskhub/87de55ae/spec.md、docs/taskhub/87de55ae/api.md |
| 日期 | 2026-09-28 |
| 负责人 | opencode（mio-taskhub） |

## 测试范围

- **单元/集成（pytest，离线）**：`tests/test_idea_draft_fields.py` —— 生成端点成功路径（字段齐全/规范化/零写库）、`fields` 子集、```json 包裹解析、503（未配置/不可达/上游非 2xx）、504（超时）、502（坏 JSON）、404、422 未知字段；hid 补全（新条目补 `as-<8hex>`、既有 hid 保留、请求内不重复）与补后单条回写 200。
- **LLM 一律 monkeypatch**：`_call_llm` 与 `mio_runtime.llm_config` 全部替换，**不打外网**；断言请求体中不含 apiKey（key 只在请求头）。
- **活体（真 LLM，一次性）**：对示例想法 `50acfb47` 真调 `POST /ideas/{id}/draft-fields`（ASGI 进程内，不打真实 HTTP 端口），验证真实上游可用 + 零写库。
- **前端**：无 JS 单测框架——以 `npm run build` 绿 + 代码断言（按钮/合并策略/草稿提示）为证。
- **不测**：真实 LLM 的文案质量（无法断言）；上游网络抖动（用 fake status 覆盖）。

## 验收标准

| FR | 验收标准 | 对应用例 | 结果 |
|---|---|---|---|
| FR-34 | 200 且 8 字段齐全；零写库（version/IdeaChange 不变）；503/504/502/404/422 可复现 | TC-34-1~TC-34-8 | ✅ 待回填全量后确认 |
| FR-35 | 前端：按钮 → 草稿填表 → 不覆盖已有 → 草稿提示 | TC-35-1（构建+代码断言） | ✅ build 绿 |
| FR-36 | 整表 PATCH 后假设条目均有 hid；既有 hid 不变；补后可回写 200 | TC-36-1、TC-36-2 | ✅ |
| FR-37 | 定点绿；全量 pytest ≥924+1；文档链 approved；push 过双门 | TC-37-1 | ✅ 定点 11 passed |

## 用例清单

| 用例 | 覆盖 FR | 前置 | 步骤/断言 | 结果 |
|---|---|---|---|---|
| TC-34-1 `test_draft_fields_ok_and_zero_write` | FR-34 | 假 LLM 返回含脏值/未知键的 JSON | 8 键齐全；tags 去重；空 text 假设剔除；risk level 归一小写/缺省 medium；未知键丢弃；version/IdeaChange 计数不变；goal/tags 未被写库 | ✅ |
| TC-34-2 `test_draft_fields_fields_subset` | FR-34 | `fields=["goal","risks"]` | 仅返回这两键 | ✅ |
| TC-34-3 `test_draft_fields_codeblock_extraction` | FR-34 | 上游返回 ```json 包裹 | 仍解析成功 | ✅ |
| TC-34-4 `test_draft_fields_not_configured_503` | FR-34 | llm_config 空 | 503 `llm not configured`，不发起调用 | ✅ |
| TC-34-5 `test_draft_fields_unavailable_503` | FR-34 | `_call_llm` → unavailable | 503 `llm unavailable` | ✅ |
| TC-34-6 `test_draft_fields_upstream_http_error_503` | FR-34 | `_call_llm` → http_401 | 503 `llm http 401`；响应不含 key | ✅ |
| TC-34-7 `test_draft_fields_timeout_504` | FR-34 | `_call_llm` → timeout | 504 `llm timeout` | ✅ |
| TC-34-8 `test_draft_fields_invalid_json_502` / `test_draft_fields_404_and_422` | FR-34 | 非 JSON / 未知字段 / 不存在 id | 502 / 422 unknown fields / 404 | ✅ |
| TC-35-1 前端构建 + 代码断言 | FR-35 | — | `npm run build` 绿；`IdeasView.jsx` 生成按钮 + `pick()` 只填空字段 + `draftNote` 提示；`api.js` 加 `draftIdeaFields` | ✅ |
| TC-36-1 `test_assumptions_hid_backfill_and_writeback` | FR-36 | 整表 PATCH 两条无 hid + 一条带 hid | 三条均有 hid、既有 `keep-me` 不变、补后可 `PATCH .../assumptions/{hid}` 回写 200 且 version+1 | ✅ |
| TC-36-2 `test_assumptions_hid_stable_across_patch` | FR-36 | 二次整表 PATCH 带回 hid | hid 不被重新生成 | ✅ |
| TC-37-1 定点 + 全量回归 | FR-37 | — | `pytest tests/test_idea_draft_fields.py -q` → **11 passed**；全量 `pytest -q` → 待回填 | ✅ 定点 |

## 活体验证（FR-34）

- 命令：`python %TEMP%\p5_live_draft.py 50acfb47`（ASGI 进程内真调；上游=DeepSeek）
- 结果：**HTTP 200，model=deepseek-flash，elapsed_ms=3648**；`goal`/`success_metric`/`constraints`/`out_of_scope`/`mvp_scope` 均按句式生成；`tags=["高风险","合规"]`；4 条 assumptions（含 hid）；4 条 risks（high/medium）
- 零写库：`version 2 → 2`、`goal` 未变 ✅

## 全量回归（FR-37）

- 命令：`.venv\Scripts\python.exe -m pytest -q --tb=short`
- 结果：待回填（基线 924 passed + 1 skipped 只增不减）
