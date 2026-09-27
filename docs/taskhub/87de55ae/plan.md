# 实施计划 — 驾驶舱草稿生成 + 假设 hid 补全（task 87de55ae）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.2 |
| 状态 | approved |
| 负责人 | opencode（mio-taskhub） |
| 需求基线 | docs/taskhub/87de55ae/requirement.md（FR-34~FR-37） |
| 设计基线 | docs/taskhub/87de55ae/spec.md、docs/taskhub/87de55ae/api.md |
| 分支 | task-87de55ae（叠在 4b9d5f8 之上） |

## 目标

1. 后端新增 `POST /api/v1/ideas/{idea_id}/draft-fields`（LLM 起草 8 字段，零写库，503/504/502 降级）；
2. 前端「✏️ 结构化字段」加「✨ 生成草稿」，草稿填入编辑表单、已有值默认不覆盖；
3. 整表 PATCH 为缺 hid 的 assumptions 补 hid，使 FR-15 回写对新数据可用；
4. 测试 + 全量回归 + 文档链 + push。

## 实施步骤

| # | 步骤 | 产出/文件 | 验证 |
|---|---|---|---|
| 1 | 后端配置读取：`mio_runtime.llm_config()` / `llm_enabled()`（config.json + env 覆盖，不暴露 key） | mio_taskhub/mio_runtime.py | 单测：有/无配置两种 |
| 2 | 新增草稿端点：prompt 组装 → urllib 调用 → 解析/规范化 → 错误映射 | mio_taskhub/api/draft.py（新） | 单测：成功/503/504/502/404/422 |
| 3 | 注册路由（api/__init__ 或 app 装配处） | mio_taskhub/app.py 或等价 | `TestClient` 能打到新路径 |
| 4 | hid 补全：整表 PATCH assumptions 归一化补 `hid`（保留既有，请求内去重） | mio_taskhub/api/ideas.py | 单测：补全 + 用返回 hid 回写 200 |
| 5 | 前端 API 封装 + 按钮与合并策略 + 提示 | web/src/api.js、web/src/components/IdeasView.jsx | `npm run build` 绿；人工活体 |
| 6 | 测试：`tests/test_idea_draft_fields.py`（monkeypatch 假 LLM，不打外网） | tests/test_idea_draft_fields.py（新） | 定点绿 |
| 7 | 全量回归 + 前端构建 | — | `pytest -q` ≥924 passed + 1 skipped；`npm run build` 绿 |
| 8 | 活体验证（新 exe）：对示例想法 50acfb47 真跑一次生成 → 表单填入 → 保存进 diff | dist exe | 截图/响应片段留痕（不含 key） |
| 9 | 文档链与提交：spec/api/plan approved → commit（引用 FR-34~FR-37）→ push 过双门 → submit/advance done | docs/taskhub/87de55ae/* | push 成功 |

## 设计与实现要点（落地细节）

- **零写库纪律**：生成端点在实现上只调用 `db.get(...)` 读取，绝不 `db.commit()`；测试用「调用前后 `version` 与 `IdeaChange` 行数不变」断言；
- **prompt 稳定性**：system 固定输出契约（8 键 JSON、句式模板、条数范围）；`response_format={"type":"json_object"}`，失败走「代码块提取 → 首尾花括号截取 → 502」三级兜底；
- **错误映射**：`URLError`→503 `llm_unavailable`；`TimeoutError/socket.timeout`→504 `llm_timeout`；上游非 2xx→503 `llm http <code>`（不回显 body）；解析失败→502；
- **合并策略**：前端 `mergeDraft(current, draft, overwrite)`；默认只填空字段；`overwrite=true` 需 confirm；保存仍走既有 `saveFieldEdit`（不新增保存分支）；
- **hid 生成**：`as-` + `uuid4().hex[:8]`，请求内 `seen` 去重；仅补缺失者（`hid`/`id` 皆无），既有值原样保留；字符串条目转 dict 时同样补；
- **测试不打外网**：`monkeypatch` 替换内部 `_call_llm`（或注入 fake transport），断言请求体含模型名与提示片段，不含 key 泄漏。

## 验证清单

- [ ] 单测：draft-fields 200 且字段齐全、version/IdeaChange 不变
- [ ] 单测：503（未配置）、503（不可达）、504（超时）、502（坏 JSON）、404、422
- [ ] 单测：assumptions 补 hid（两条都补、既有 hid 不变、可用其回写 200）
- [ ] `npm run build` 绿
- [ ] 全量 `pytest -q` ≥924 passed + 1 skipped
- [ ] 活体：示例想法生成草稿 → 表单 → 保存 → IdeaChange 可见
- [ ] 文档：requirement/spec/api/plan approved；push 过文档门 + FR 门

## 风险与回滚

| 风险 | 影响 | 对策 |
|---|---|---|
| LLM 慢/不稳定 | 按钮长时间 loading | 30s 超时 → 504 提示；不落库无脏数据 |
| 草稿质量差 | 用户仍需大改 | 明确标注「AI 草稿，请核对」；只填空字段，不覆盖已有 |
| key 泄漏到日志/响应 | 安全事故 | 只读进请求头；错误不回显上游 body；测试断言响应不含 key 片段 |
| 补 hid 破坏既有契约 | P0~P4 回归 | 只补缺失值、键集不变；跑既有契约用例 |
| 回滚 | — | 生成端点为新增路径（删路由即回滚）；hid 补全为纯增字段值 |

## 追溯

| FR | 步骤 |
|---|---|
| FR-34 | 步骤 1、2、3、6 |
| FR-35 | 步骤 5 |
| FR-36 | 步骤 4、6 |
| FR-37 | 步骤 7、8、9 |
