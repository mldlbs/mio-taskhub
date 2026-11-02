# 活跃队列文档豁免收敛（2026-11-02）

> 对象：`doc_gate_exempt=1` 且状态为 `QUEUED/CLAIMED/RUNNING` 的 25 条任务。
> 目标：让活跃任务不再默认跳过文档门禁；终态任务不动。

## 1 变更前

| 指标 | 数值 |
|---|---|
| `doc_gate_exempt=1` 总数 | 383 / 1,977（19.4%） |
| 其中活跃（QUEUED/CLAIMED/RUNNING） | **25** |
| 活跃总数 | 45 |
| 活跃豁免占比 | 55.6% |

25 条中：BRAINSTORMING 12、PLANNING 5、IMPLEMENTING 4、REVIEW 1、DONE 3（历史遗留但状态仍 QUEUED）。

## 2 备份与快照

| 项 | 路径 |
|---|---|
| 变更前整库备份 | `~/.mio_taskhub/preflight/doc-gate-exempt-20261102/taskhub-before.db`（41.2 MB） |
| 变更前 25 条明细 | `~/.mio_taskhub/preflight/doc-gate-exempt-20261102/targets-before.json` |
| 逐条结果 | `~/.mio_taskhub/preflight/doc-gate-exempt-20261102/patch-results.json` |

## 3 执行方式

逐条 `PATCH /api/v1/tasks/{id}`，body `{"doc_gate_exempt": false}`，走正常 API，因此每条都产生 `task_updated` 事件，可审计。

| 结果 | 数量 |
|---|---|
| 成功且回读为 false | 25 |
| 失败 | 0 |

变更后：

| 指标 | 数值 |
|---|---|
| `doc_gate_exempt=1` 总数 | **358**（-25） |
| 其中活跃 | **0** |

## 4 变更后效果（只读副本推演）

对 21 条还有下一阶段的任务调用 `_check_lifecycle_gate`：

| 结果 | 数量 | 说明 |
|---|---|---|
| 会被拦 | 10 | requirement/spec/api/plan 仍是 draft 或未登记 |
| 仍放行 | 11 | 未登记任何文档，门禁 fail-open |
| 无下一阶段 | 4 | 已在 DONE |

被拦的 10 条全部是 `多源感知及AI智能决策中心` 下的旧任务，本来已停摆数周；收敛不会新增阻塞，只是把「静默跳过」变成「推进时明确报缺」。

**重要限制**：门禁对「完全没登记文档」的任务仍然放行。所以豁免收敛只对已登记文档的任务生效；要让所有任务都受约束，需要门禁三档改造。

## 5 回滚

```sql
-- 只回滚这 25 条
UPDATE task SET doc_gate_exempt = 1
WHERE id IN (
  '51afca88','55003158','16b23408','087d8645','57ead2cc','29b483eb','2ee5e668',
  'b52767d6','ddbd66e1','918e0ca1','5ab9e4f2','825d28c2','3755be4b','98af2e86',
  '475bf989','d0390a04','6a6f348f','dc483d0e','c059c54a','1715f0b8','244b863f',
  'f625fce9','9189e5a5','2d0fee2d','28f8fceb'
);
```

回滚后建议重启或等待连接刷新；`doc_gate_exempt` 每次读取都从 DB 取，无需迁移。

## 6 结论

活跃队列的显式豁免已清零，383 条降到 358 条；剩余 358 条全部是终态任务（CANCELLED 169 / COMPLETED 132 / FAILED 57），不再影响门禁。

下一步是门禁三档：让「登记了文档就必须 approved」覆盖 light，并决定是否要求所有新任务必须先有 requirement 文档。
