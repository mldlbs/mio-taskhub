# 可靠性验证报告：真服务 E2E + 并发 claim 压测

| 项 | 值 |
|---|---|
| 任务 | f72fde6e（branch task-f72fde6e，run cc0b82c9） |
| 来源 | docs/taskhub/system-assessment-20260930.md P1-4 |
| 日期 | 2026-09-30 |
| 测试 | tests/test_e2e_real_service.py（标记 `e2e`，默认排除，`pytest -m e2e` 运行） |
| 方法 | 起**真实 uvicorn 进程**（独立端口 + 独立 DB）+ 真实 HTTP 驱动 |

## 1. 为什么需要（评估报告的问题）

现有 969 个测试全是 `TestClient(app)` **进程内桩**（`test_integration.py` 亦然）：证明"模块逻辑正确"，但不证明"真实进程 + 真实网络 + 真实并发下成立"。`atomic_claim` 的乐观锁在代码层正确，**从未在真实并发下验证过**。

## 2. 测试设计

`RealServer`：`subprocess.Popen(uvicorn mio_taskhub.main:app)`，独立 free port，`MIO_TASKHUB_DB` 指向临时库，健康检查探根路径 `/healthz`（非 `/api/v1/healthz`）。模块级 fixture，测试结束 terminate 并尽力清理（Windows 文件句柄容错）。

| # | 用例 | 验证 |
|---|---|---|
| ① | `test_full_lifecycle_over_real_http` | 建单→注册→claim→heartbeat→submit_result→state=completed 全链（真实 HTTP） |
| ② | `test_concurrent_claim_no_duplicates` | 10 agent × 100 任务并发 claim：**零重复、零丢失、一一对应** |
| ③ | `test_long_task_survives_beyond_old_120s_window` | est=60min 任务在 agent 心跳后、offline 且 130s 无心跳，`effective_timeout > 130`（与 P1-2 联动） |
| ④ | `test_quantified_baseline` | 20 任务端到端，输出成功率 + P50/P95 |

## 3. 结果（实测）

| 指标 | 值 |
|---|---|
| 用例通过 | **4/4** |
| 并发认领重复数 | **0**（10 agent 抢 100 任务） |
| 并发认领丢失 | **0**（认领集合 == 任务集合） |
| 端到端成功率 | **100%**（20/20，另 100/100 认领全部提交成功） |
| claim 时延 | **P50 30.5ms / P95 32.5ms** |
| 全链 E2E | 通过（真实 HTTP，最终 state=completed） |

## 4. 结论

- **并发零重复认领被证明**（在真实进程 + 真实并发下）：`atomic_claim` 的条件 UPDATE + `rowcount` 校验成立。
- **全生命周期在真实服务下成立**。
- **长任务不再被 120s 窗口误杀**：P1-2 的窗口自适应修复经真实服务验证（`effective_timeout > 130`）。
- 建立**可量化基线**：成功率 100%、claim P50/P95 ≈ 30/32ms（后续回归可对比）。

## 5. 测试可信度边界（诚实声明）

本组测试**证明**：真实服务下的生命周期、并发认领唯一性、窗口自适应。
本组测试**未证明**（仍未覆盖）：
- **真实 agent 进程**执行（用 HTTP 模拟 agent 行为，未跑真实 opencode/codex 子进程）；
- **多进程并发**（同一进程内 10 线程；跨进程并发未测）；
- **数据规模**（100 任务量级；生产 278，未做更大规模）；
- **心跳回收的真实等待**（用例③直接调用 `effective_timeout` 判定，未真等 120s+ 观察 sweep 行为）。

## 6. CI/流程接入

- pytest 标记 `e2e`，`pyproject.toml` 加 `addopts = "-m 'not e2e'"` → 默认单测不跑（不拖慢）；`-m e2e` 单独运行。
- 默认全量：1005 passed + 1 skipped + **4 deselected**。

## 7. 证据档位

- **事实**：4/4 通过；重复 0、丢失 0；成功率 100%；P50/P95 实测值。
- **推断**：并发唯一性在更大规模/跨进程下仍成立（未测，规模外推）。
- **未证明**：真实 agent 进程、跨进程、更大数据规模（见 §5）。
