# 事件一致性模型（Event Consistency Model）

> 任务 c32a2145（P1-3）。来源：docs/taskhub/system-assessment-20260930.md。
> **本文档纠正了评估报告的一处误判**（见 §7）。

## 1. 结论摘要

系统存在**两条独立的事件传播机制**，各自语义不同、都是有意的设计：

| 机制 | 用途 | 一致性等级 | 载体 |
|---|---|---|---|
| **A. 事件日志 + 提交后广播** | 任务/想法/讨论等业务变更 → WebSocket 推送 | **持久化：事务内原子**；**推送：至多一次、尽力投递** | `event` 表 + `broadcast_for_event` |
| **B. 事务性发件箱（Outbox）** | ADR 变更 → Git 仓库投影 | **标准 outbox**：业务写与 outbox 同事务；投递可重试 | `outboxevent` 表 + `git_sync` worker |

**不存在"事件会丢失"的问题**：两条机制的**持久化都是事务内的**。

## 2. 机制 A：事件日志 + 提交后广播（审计结论）

### 实现（mio_taskhub/events.py）

```python
def emit_event(db, type, entity, entity_id, run_id, payload) -> Event:
    e = Event(...); db.add(e); return e         # 与业务同事务

def install_broadcast_hooks(engine):
    event.listen(SASession, "after_flush",  _collect_event_for_broadcast)   # 收集
    event.listen(SASession, "after_commit", _broadcast_after_commit)        # 提交后广播
```

`after_flush` 把本次 flush 的 Event 收集到 `_pending_broadcasts`；`after_commit` 在**事务成功提交后**逐个 `broadcast_for_event`。`after_rollback` 不清空缓冲——但因 rollback 时不会触发 `after_commit`，缓冲会在下个事务 flush 后一并清理；实测回滚后 `_pending_broadcasts` 为空、事件未落库、未广播。

### 实测语义（失败注入，2026-09-30）

| 场景 | 结果 | 证据 |
|---|---|---|
| 正常提交 | 事件落库 + 广播 1 次；缓冲清空 | `events=1, broadcast=[1], pending=0` |
| 业务回滚 | 事件**未落库、未广播** | `type=t2 count=0, broadcast=[]` |
| WS 广播抛异常（真实 `broadcast_for_event`） | **commit 成功、数据与事件均落库** | `commit OK, task=1, event=1` |
| payload 非法 JSON | **commit 仍成功** | `commit OK` |

**结论**：机制 A 是 **"事务内持久化 + 提交后尽力推送"**。事件日志（`event` 表，生产 1429 行、20+ 类型、按天连续）是可重放的**事实来源**；WebSocket 推送是叠加在持久日志之上的**可选通知**，其失败不影响一致性。

## 3. 机制 B：事务性发件箱（ADR → Git 同步）

- `adr.py::_record_evolve_outbox / _record_action_outbox`：ADR 变更时 `db.add(OutboxEvent(...))`，**与业务同事务**。
- `git_sync.py`：worker 轮询 `OutboxEvent(status=PENDING)` → `_process_event` 投影到 Git → 置 `SYNCED`；失败置 `FAILED`（`retry_count<max_retries` 时回退 `PENDING` 重试）；`cleanup_old_outbox_events` 清理 30 天前的 `SYNCED/FAILED`。

这是**教科书式的 outbox**：至少一次投递 + 幂等消费（`_process_event`）+ 重试 + 保留期清理。

**生产数据**：`outboxevent` 0 行；库中仅 1 个 ADR（`idea_type=ADR`）、1 条 `ADR_ACTION` 变更。0 行与"已同步并被 30 天清理"或"该路径未被触发"均自洽 —— **无法据此判断 outbox 失效**。

## 4. 一致性等级（正式表述）

- **A（任务事件）**：持久化 **强一致**（与业务同事务）；对外推送 **至多一次 / 尽力**。
- **B（ADR→Git）**：**最终一致**（事务性 outbox + 重试投递）。
- **跨机制**：互不依赖，各自独立成立。

## 5. 与评估报告的差异（纠正）

| 评估报告原表述 | 实际情况（本任务审计） |
|---|---|
| "outbox 未接入主流程、悬空死代码" | **错误**。Outbox **已接入** ADR→Git 同步链（adr.py 写、git_sync.py 投递），是完整可用的机制；0 行是"未触发/已清理"，非"死表" |
| "事件一致性无保障" | **过强**。事件日志持久化是事务内的；无保障的仅是 WS 推送的**时延/可达性**，而这本就是"尽力推送"的设计意图 |

## 6. 处置决定

**不接入 outbox、不删除 outbox**——两者都不对：

- **不删**：机制 B 是真实在用的 ADR 同步链，删除会破坏 ADR→Git 投影。
- **不接**：机制 A 的事件日志已提供事务性持久化，无需再为其叠加 outbox。

**唯一要做的加固（防御性，非现存 bug）**：`_broadcast_after_commit` 未对 `broadcast_for_event` 自身加 try/except。虽然真实 `broadcast_for_event` 内部已吞异常（实测 commit 不受影响），但若被重构/打桩后异常冒泡，会**从 `db.commit()` 抛出**——而数据已提交，调用方误判失败可能触发重复写。加固为 after_commit 内 **逐事件 try/except**，确保广播路径永不反向影响已提交事务。

## 7. 证据档位声明

- **事实**：`emit_event` 在事务内；`after_commit` 触发广播；失败注入四场景结果；`adr.py`/`git_sync.py` 的 outbox 读写路径存在；`outboxevent` 0 行。
- **推断**：0 行 = 未触发或已按 30 天清理（两者无法从静态数据区分）。
- **被纠正**：评估报告的"outbox 悬空死代码"判断**不成立**——审计发现它是 ADR 同步链的正常组件。
