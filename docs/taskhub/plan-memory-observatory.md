# 🗓 memory-observatory 实现计划

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 状态 | draft |
| 任务 | 8a966293 |
| 上游规格 | docs/taskhub/spec-memory-observatory.md v1.0（已 approved） |
| 上游契约 | docs/taskhub/api-memory-observatory.md v1.0（已 approved） |
| 上游需求 | docs/taskhub/requirement-memory-observatory.md v1.0（FR-1~FR-10） |
| 更新日期 | 2026-10-05 |

---

## 📌 概述

按 spec 顺序实施：**先读后端（纯函数+测试）→ 端点 → 前端 React → 回归 → 文档回填 → 门控提交**。每步独立可验收、可单独提交；步骤内标注 FR 引用（满足 pre-push FR 追溯门控）。

**前置条件**：

- 分支 `task-8a966293`（从 main 切；分支名关联任务，供 pre-push 钩子校验）
- Python 环境可跑 `pytest`；Node 构建用 workbuddy 的 `npm.cmd run build`（workdir `web/`，见 AGENTS 环境备注）
- 参考实现 `E:\work\code\agent-dev\mneme\`（只读，**零改动**，FR-10）
- 动手前已通过 `taskhub_read_document(run_id=…)` 读 requirement/spec/api（Read Evidence）

---

## 📋 实施步骤

### Step 1 后端投影模块（FR-3/FR-4/FR-5/FR-6）

- 新建 `mio_taskhub/memory_observatory.py`，按 spec「详细设计」实现：
  - 常量：`MIO_KIND_MAP` / `KIND_WEIGHTS` / `DECISION_WORDS_RE` / `LOG_TAG`
  - 函数：`data_paths` / `read_jsonl` / `clean_mio_content` / `derive_mio_title` /
    `load_evidence` / `compute_reuse_score` / `parse_mio` / `build_data` /
    `build_data_cached` + `_reset_cache` / `_get_skipped`
  - 缓存：模块级 `_cache` + `threading.Lock` 双检；key = 两文件 `(mtime_ns, size)` + `show_logs`
  - `MIO_HOME` 经 `mio_runtime.home()` 解析；零写入、不 import 写路径
- 验收：`python -c "from mio_taskhub import memory_observatory"` 导入无副作用；与 mneme `hermes-server.js` 逐函数对照无语义遗漏

### Step 2 后端测试（FR-1~FR-6 覆盖面）

- 新建 `tests/test_memory_observatory.py`，按 spec「测试计划」11 个用例：
  `test_only_reads_mio_home` / `test_default_view_hides_logs` / `test_logs_param_expands` /
  `test_env_force_hides_logs` / `test_reuse_score_formula` / `test_kind_map_clean_title` /
  `test_relations_and_ids` / `test_evidence_stats` / `test_bad_lines_skipped` /
  `test_mtime_cache` / `test_api_endpoint`（Step 3 后补端点部分）
- fixture：`tmp_path` 建假 MIO_HOME（monkeypatch env），构造含 log/observer 源/决策词/坏行的数据
- 验收：`pytest tests/test_memory_observatory.py -q` 全绿

### Step 3 API 端点挂载（FR-1/FR-2）

- `mio_taskhub/api/memory.py` 新增 `GET /observatory/data`：
  - `logs: bool = False`；env `MIO_OBSERVATORY_HIDE_LOGS` 真时强制 `show_logs=False`
  - 成功直出投影；失败 `JSONResponse(status_code=500, content={"error": str(e)})`（不用 HTTPException detail 包装）
- 补 `test_api_endpoint`（TestClient：200 schema keys、`logs=1`、异常注入 500 `{error}`）
- 验收：契约与 api-memory-observatory.md §18 逐字段一致；OpenAPI 出现该路径

### Step 4 前端邻接表 + 视图组件（FR-7）

- 新建 `web/src/lib/observatoryAdjacency.js`：`buildAdjacency(relations)` 单次 O(E) 构建
  `Map id → [{other, rel, dir:'out'|'in'}]`（纯函数，可单测）
- 新建 `web/src/components/MemoryObservatoryView.jsx`，按 spec「前端设计」实现：
  - 头部（标题 + 证据覆盖率 + 日志开关 + 刷新）、工具条（类型 chips + 本地过滤 +「非权威检索」标注）
  - 卡片网格（type 徽标 + 复用热度徽标，**阈值打开 mneme/hermes-graph.html 核对现值**）
  - 详情面板（meta 行 + obs 正文 + 出入边 ego 点击跳转）
  - 项目/来源聚合面板（记忆数/decision 数/高复用数）
  - 提示词片段（rule/experience 按 reuse_score 降序、带来源跳转、复制/下载、启发式标注）
  - fetch 仅 `/api/v1/memory/observatory/data`；错误条+重试；卸载 abort；禁 `dangerouslySetInnerHTML`
- Rail 注册：定位 `MioRuntimeView` 注册处同级新增「记忆观测」入口
- 验收：`npm run build` 绿；grep 无 `3456`/`hermes`/`localhost:3456` 残留

### Step 5 回归与语义核对（FR-8/FR-10）

- 全量 `pytest -q`（只增不减）+ `npm run build`
- 对照核对：README 能力表 5 项（观察视图/本地过滤/复用热度/提示词片段/聚合导航）逐一在 UI 可达
- 文案红线扫描：UI 无「高价值/规则提取/权威检索」措辞
- `git -C E:\work\code\agent-dev\mneme status` 干净（FR-10）
- 验收：全绿 + 核对清单勾完

### Step 6 文档回填与收尾（FR-9）

- requirement/spec/api 三份追溯表「测试用例」列回填真实用例名
- `README.md` 新增端点与视图说明、`MIO_OBSERVATORY_HIDE_LOGS` 配置项；`CHANGELOG.md` 记入 Unreleased
- 验收：`taskhub_doc_quality` 无 errors、分数不低于基线（棘轮）

### Step 7 提交与门控（FR-9/FR-10）

- 分支 `task-8a966293` 提交（代码 + 测试 + 文档回填），`git push` 过 pre-push：
  - spec/api/plan approved ✓（本任务内完成）
  - diff 引用的 FR-n 在已批准 requirement 中真实存在（Step 1~5 注释/提交信息带 FR 引用）
- `taskhub_read_status(run_id)` 自查 Read Evidence → `taskhub_submit_result`
- 验收：push 被钩子接受；submit 成功；向用户一句话汇报

---

## ✅ 验证命令

| 项 | 命令 |
|---|---|
| 后端单测 | `pytest tests/test_memory_observatory.py -q` |
| 全量回归 | `pytest -q`（workdir=mio-taskhub 根） |
| 前端构建 | `& 'C:\Users\admin\AppData\Local\hermes\node\npm.cmd' run build`（workdir=`web`） |
| 3456/Node 残留 | grep `3456|hermes-server|localhost:3456` 于 `web/src` 与 `mio_taskhub/` |
| 契约核对 | TestClient 响应键集合 vs api 文档 §18 |
| mneme 隔离 | `git -C ..\mneme status --short` 为空 |

---

## ⚠️ 风险与回退

| 风险 | 回退 |
|---|---|
| Python 投影性能不达标 | 缓存已兜底；实测超目标时按 spec 风险表优化（预读/行缓存），不改契约 |
| Rail 注册机制与假设不符 | 按实际注册表调整入口挂法，组件与端点不变 |
| pre-push FR 校验拒绝 | 检查 diff 中 FR-n 拼写与 requirement 一致性，修正引用后重推（禁 `--no-verify` 绕过） |
| 任一步失败 | `git checkout main && git branch -D task-8a966293` 回退；已批准文档不回退（质量只升不降） |

---

## 📎 追溯

| 步骤 | FR | 验证 |
|---|---|---|
| Step 1 | FR-3/4/5/6 | Step 2 用例 |
| Step 2 | FR-1~6 | pytest 绿 |
| Step 3 | FR-1/2 | TestClient + OpenAPI |
| Step 4 | FR-7 | npm build + grep + 手工清单 |
| Step 5 | FR-8/10 | 全量 pytest + mneme status |
| Step 6 | FR-9 | doc_quality/棘轮 |
| Step 7 | FR-9/10 | pre-push + submit |
