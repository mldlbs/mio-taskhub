# 🗺️ 实现计划（Plan）· memory-merge

## 📄 文档信息

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 任务 | e4c3bc51 |
| 分支 | task-e4c3bc51 |
| 上游 | requirement-memory-merge.md（FR-1~FR-6）、spec-memory-merge.md、api-memory-merge.md |
| 更新日期 | 2026-10-05 |

---

## 步骤

### Step 1：前端合并（FR-1）
1. `web/src/components/MemoryView.jsx`：
   - import `MemoryObservatoryView`；
   - 加 `useState('observatory')` tab 状态 + 两个 tab 按钮（`memory-tab` / `is-on`，`role="tablist"/"tab"`）；
   - `tab==='observatory'` 渲染 `<MemoryObservatoryView />`；`tab==='gateway'` 渲染原全部 section（header/存储状态/查询/调用统计/实时事件，逻辑不动）。
2. `web/src/components/Rail.jsx`：删除 `id:'observatory'` entry（L75-82）。
3. `web/src/App.jsx`：删除 `view === 'observatory'` 分支（L456-458）与顶部 `MemoryObservatoryView` import（确认无其他引用后）。
4. `web/src/index.css`：追加 `.memory-tab` / `.memory-tab.is-on` 样式（视觉参数对齐 `sjv__tab`）。

### Step 2：路径修复（FR-2）
1. `web/src/api.js:145`：`'/api/memory/health'` → `'/api/v1/memory/health'`。
2. `web/src/components/MemoryView.jsx:77`：`'/api/memory/query?…'` → `'/api/v1/memory/query?…'`。
3. `web/src/App.jsx:402`：`'/api/memory/observer/ingest'` → `'/api/v1/memory/observer/ingest'`。
4. `web/src/components/MemoryView.jsx:253` 提示文案 `/api/memory/record` → `/api/v1/memory/record`。

### Step 3：注释与脚本修复（FR-3/FR-4）
1. `mio_taskhub/api/memory.py` 模块 docstring 6 行路径加 `v1`（函数体零 diff）。
2. `packaging/build.ps1` L69：`'dist\mio-taskhub'` → `'dist\mio-taskhub-v3'`（校验/复制/zip/启动随 `$distDir` 传导）。

### Step 4：静态回归用例（FR-1/2/3/4）
新增 `tests/test_memory_merge.py`：
- `test_web_no_legacy_memory_paths`：递归扫描 `web/src` 下 `*.js/*.jsx`，断言无字面 `/api/memory/`；
- `test_api_memory_docstring_v1`：`api/memory.py` 含 `/api/v1/memory/health` 且无裸 `/api/memory/` 行；
- `test_build_script_dist_matches_spec`：解析 spec `COLLECT(... name="X")` 与 build.ps1 `$distDir` 目录名一致；
- `test_rail_single_memory_entry`：`Rail.jsx` 无 `id: 'observatory'`、`App.jsx` 无 `view === 'observatory'`、`MemoryView.jsx` 默认 `useState('observatory')` 且含「记忆观测」「记忆网关」两文案。

### Step 5：回归与构建（FR-5）
1. `.venv\Scripts\python.exe -m pytest tests -q`：新增 4 用例绿、观测层 11 用例绿、全量只增不减（存量 `test_spinning_guard` 4 失败豁免）。
2. `web` 下 `npm run build`：绿。
3. `packaging/build.ps1 -Quick` 实跑（同时验证 FR-4）：第 4 步 5 项 OK、分发文件复制、hub 自动重启。

### Step 6：真机验证（FR-1/2）
1. `GET /api/v1/memory/health`、`GET /api/v1/memory/query?limit=3`、`POST /api/v1/memory/observer/ingest` → 全 2xx；
2. 旧路径 `/api/memory/health` 仍 404（无 alias，符合契约）；
3. Rail 打开「记忆」：默认观测 tab 全区块在、切网关 tab 四区块正常、Rail 无「记忆观测」入口。

### Step 7：文档收尾与推送（FR-6）
1. 写 `docs/taskhub/test-memory-merge.md`（test 类，FR-1~FR-6 追溯）；
2. 回填 requirement/spec 追溯表用例名；README 若有双视图表述则同步、CHANGELOG Unreleased 记录；
3. `taskhub_doc_quality`：0 errors、追溯全覆盖；棘轮不回退；
4. 推进文档状态：requirement draft→approved；spec/api draft→review→approved；plan draft→approved（质量 ≥80 门控）；
5. advance：design → planning → ready；claim 拿 run_id，读 required_reads 落 ReadEvidence；
6. commit（引用 FR-n）→ push 过 pre-push 门控 → `taskhub_submit_result`。

---

## 验收清单（DoD）

- [ ] FR-1：单入口双 tab 默认观测，观测区块不回退
- [ ] FR-2：三处 v1 路径真机可用，`web/src` 静态扫描 0 旧路径
- [ ] FR-3：docstring 与路由一致
- [ ] FR-4：一键打包全流程无人工干预通过
- [ ] FR-5：pytest 全量只增不减 + `npm run build` 绿
- [ ] FR-6：文档链 approved / 质量 ≥80 / 追溯闭环 / pre-push 通过

## 回退

`git checkout main && git branch -D task-e4c3bc51`（已批准文档不回退；dist 产物可随时用 build.ps1 重建）。
