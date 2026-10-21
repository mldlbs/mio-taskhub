# 实现计划 · 窄门禁（空 diff 拦截）

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.2 |
| 状态 | approved |
| 最后更新 | 2026-10-20 16:32 |
| 负责人 | 项目作者（单人） |
| 关联任务 | d468e633 |
| 需求 | `docs/taskhub/d468e633/requirement.md`（FR-1 ~ FR-11） |
| 设计 | `docs/taskhub/d468e633/spec.md` |
| 契约 | `docs/taskhub/d468e633/api.md` |

## 实施原则

- **一次只做一件事**：按批次推进，每批次有独立可验证的产出，不并行铺开。
- **可回退**：任何一步出问题，`MIO_DIFF_GATE=off` 立即全量放行，不需回滚代码或数据。
- **证据优先**：拦截是否生效**查库确认**，不以界面显示为准。
- **引用 FR-n**：代码与测试注释引用需求编号，便于追溯。

## 批次 0 · 流程前置（已完成）

| 步骤 | 动作 | 完成条件 |
|---|---|---|
| 0.1 | 在 taskhub 建任务并 scaffold 文档链 | 任务 `d468e633`，requirement/spec/api/test 已落盘 |
| 0.2 | 写 requirement / spec / api / plan 并推进到 approved | 四份文档质量分 ≥ 80、errors = 0 |
| 0.3 | 推进任务阶段到 ready | `brainstorming → design → planning → ready` 全部通过门控 |
| 0.4 | 建分支 | 分支名 `task-d468e633`（钩子按 `task-<8位hex>` 关联任务） |

## 批次 A · 后端

| 步骤 | 动作 | 对应 FR | 文件 | 完成条件 |
|---|---|---|---|---|
| A1 | `Run` 加 3 字段（`diff_summary` JSON / `annotations` JSON / `rejections` int）；`Task` 加 `diff_gate_exempt` bool | FR-1 FR-6 FR-10 | `models.py:262-274` / `:127-179` | 模型可导入，无字段名冲突 |
| A2 | 写迁移：`_migrate_run_diff` + `_migrate_task_diff_gate`，幂等，存量 task 全置豁免 | FR-10 | `migrations.py` | 老库启动后新列存在、存量豁免为真、重复执行不报错 |
| A3 | 新建 `diff_gate.py`：`snapshot` / `differs` / `check_diff_gate` / `merge_annotations` | FR-1 FR-2 FR-3 FR-4 | 新文件 | 纯函数可单测；git 异常不外抛 |
| A4 | `create_run` 拍前快照（best-effort，失败不阻断 claim） | FR-1 | `api/runs.py:87-100` | run 创建后 `diff_summary.before` 有值或为 null 且不抛错 |
| A5 | `submit_result` 插门禁：判定 → 落三处记录 → **先 commit 再抛 422** → 超限判失败 | FR-2 FR-3 FR-4 FR-5 FR-6 FR-8 | `api/runs.py:271-318` | 空改动 + success → 422 `run.empty_diff`；run 状态与 attempt 未变；`Event` 与 `HistoryEvent` 两表有记录 |
| A6 | `tasks.py`：`editable` 与 bool 白名单加 `diff_gate_exempt`；`_task_detail` 的 `runs[]` 增补 3 字段 | FR-4 FR-10 | `api/tasks.py:218-238` / `:191-197` | PATCH 能置豁免；任务详情能取到 diff_summary/annotations/rejections |
| A7 | 新增 `GET /runs/{run_id}/diff-evidence` | FR-2 FR-3 | `api/runs.py` | 返回 `would_block` / `reason` / 前后快照 |

## 批次 B · 前端

| 步骤 | 动作 | 对应 FR | 文件 | 完成条件 |
|---|---|---|---|---|
| B1 | `ApiError` 读 `detail.code`；msg 优先取 `detail.message` | FR-8 | `web/src/api.js:27-30` / `:43` | 现有 409 门禁码前端可读；422 不再显示 JSON 乱码 |
| B2 | toast 分支：`run.result_rejected` → danger toast | FR-6 | `web/src/App.jsx:164-167` | 被拦时右上角出现红色 toast |
| B3 | `HIST_TYPE_META` 加 `rejected` | FR-6 | `TaskDetail.jsx:40-57` | 「动态」tab 出现红色「已拦截」行 |
| B4 | 完成报告渲染 `run.annotations`：`block` → 红框拦截卡；`warn` → 琥珀提示行（含「不影响放行」） | FR-6 FR-7 | `TaskDetail.jsx:376-395` | 三种标注都能正确渲染；标注不弹 toast |

## 批次 C · 测试

| 步骤 | 动作 | 对应 FR | 完成条件 |
|---|---|---|---|
| C1 | 新建 `tests/test_run_diff_gate.py`，覆盖 TC-1 ~ TC-10 | FR-1 ~ FR-11 | 10 条全过 |
| C2 | 迁移幂等用例 | FR-10 | 老库场景通过 |
| C3 | 门控冒烟：`.smoke_ws/gate_smoke.sh` 增补窄门禁正反路径 | FR-2 FR-3 | 冒烟 11 项全过 |

**跑测纪律**（踩过的坑）：

- 全量跑加 `--basetemp=<独立目录>`：否则 pytest 清理 `pytest-of-admin/garbage-*` 触发 safe-delete 批量删除保护，进程在 teardown 被杀、拿不到汇总行。
- 并行跑用 `MIO_TEST_DB_PATH=<不同路径>` 隔离，否则 `PermissionError: 另一个程序正在使用此文件`。
- 失败名单落盘再读：`pytest --lf -q -rf > out.txt`，别只看末尾几行。
- 用 `git stash` 对照判断失败是否本次引入；已知 pre-existing 失败：`test_spinning_guard` 的 4 个 webhook_guard 用例。

## 批次 D · 灰度与验收

| 步骤 | 动作 | 完成条件 |
|---|---|---|
| D1 | `MIO_DIFF_GATE=warn`（默认）上线 | 判定有记录、无 422 |
| D2 | 用 3–5 个真实 run 观察 | 零误伤；跳过判定的原因码都合理 |
| D3 | 切 `MIO_DIFF_GATE=block` | 空改动被拒；有改动正常放行 |
| D4 | 观察一个真实周期 | **能指着一例「拦下了本会放过的假完成」**（FR-2 的最终判据） |
| D5 | 汇总：拦截次数 / 误伤次数 / 跳过原因分布 | 写入 CHANGELOG |

## 涉及文件清单

| 文件 | 批次 | 动作 |
|---|---|---|
| `mio_taskhub/models.py` | A1 | 改 |
| `mio_taskhub/migrations.py` | A2 | 改 |
| `mio_taskhub/diff_gate.py` | A3 | 新增 |
| `mio_taskhub/api/runs.py` | A4 A5 A7 | 改 |
| `mio_taskhub/api/tasks.py` | A6 | 改 |
| `web/src/api.js` | B1 | 改 |
| `web/src/App.jsx` | B2 | 改 |
| `web/src/components/TaskDetail.jsx` | B3 B4 | 改 |
| `tests/test_run_diff_gate.py` | C1 | 新增 |
| `.smoke_ws/gate_smoke.sh` | C3 | 改 |

## 风险与回退

| 风险 | 表现 | 回退动作 |
|---|---|---|
| 误伤合法完成 | 频繁 422、任务推不动 | `MIO_DIFF_GATE=off` 立即放行；重新定判据（不删代码） |
| 采集失败被当拦截 | 提交全被拒 | 判据已设计 fail-open；核对 `diff_summary.reason` |
| Agent 无限重试 | 反复提交 | `MAX_REJECTIONS=2` 超限判失败 |
| 事件没落库 | 人看不到拦截 | 核对 `get_session` 提交时序；**查库确认**，不能只看界面 |
| 迁移影响存量任务 | 老任务行为变化 | 存量已全置豁免；必要时再跑一次迁移修正 |

## 后续独立立项（本期不做）

- 测试执行器（真跑 pytest）——结果只做标注不拦。
- P1-1 成功率口径失真（失败被清洗为取消）。
- P1-2 评审自审绕过（`self_review` 通道）。
