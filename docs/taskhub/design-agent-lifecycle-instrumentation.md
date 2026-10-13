# Agent 生命周期埋点设计（agent_lifecycle.*）

任务：T0.7（3dc9567e）｜上游映射：D1、03 §4、G3（src:G3 首启根因）｜状态：A1–A5 已闭环

## 1. 背景与问题

reaper 判死 `never started (no heartbeat)` 后无法区分三种根因：

| 场景 | 现象 | 缺失证据 |
|---|---|---|
| C1 agent 进程未启动 | claim 后无任何动静 | 不知进程是否存在过 |
| C2 agent 起了但没发心跳 | 本地活、Hub 静默 | agent 侧无本地发送记录 |
| C3 心跳发了但没到 Hub | 网络分区/Hub 不可达 | 「本地已发 ∧ Hub 未收」两侧对账 |

单机上 C2 与网络分区本质不可分，必须叠加**两侧证据**：agent 侧本地已发（sent>0）
∧ Hub 未收（event 表无对应 heartbeat）才可判定 C3。

## 2. 三类事件（agent_lifecycle.*）

| 事件 | 发射点 | 语义 | 关键字段 |
|---|---|---|---|
| `agent_lifecycle.agent_ready` | Hub，run 首次心跳（state CLAIMED→RUNNING） | agent 已连通并开始上报 | task_id、agent_id、attempt、progress |
| `agent_lifecycle.heartbeat_sent_count` | agent 侧（MCP `taskhub_heartbeat` 每次调用） | 本地已发计数 | sent_count、delivered（HTTP 是否送达） |
| `agent_lifecycle.process_alive` | Hub，收到 pid 后异步探测、结果态回填 | 进程存活结论 | pid、alive（true/false/null=unknown）、probe |

## 3. 决策记录（A1–A5）

- **A1 真相源 = 本地 jsonl，event 表单向回填。** 运行期只写
  `$MIO_HOME/lifecycle/**/*.jsonl`（fire-and-forget），不直接写 event 表；
  `scripts/ingest_lifecycle.py` 是唯一回填通道。避免双写导致幂等语义分裂，
  且 DB 不可达时证据仍在本地（观测零阻塞）。
- **A2 发射点最小化。** agent_ready 只认 Hub 首心跳（`run.state==CLAIMED`
  判定，heartbeat 是唯一写 `RunState.RUNNING` 的路径）；heartbeat_sent_count
  只记 agent 侧发送尝试（delivered 标志区分「发了没到」）；process_alive 只在
  pid 首见/变化时探测一次（去重门控），避免每跳一线程。
- **A3 隔离与降级沿用 reaper_decision 先例。** 模块内 Queue(1000) + daemon
  worker，`put_nowait` 全吞；写盘/探测失败 log 后丢弃。埋点任何失败都不冒泡
  ——心跳、submit、claim 关键路径零影响（DoD：埋点不得阻塞 submit）。
- **A4 文件布局按 pid/run 分文件。** `$MIO_HOME/lifecycle/<writer_pid>/<run_id>.jsonl`；
  行 = 单个 JSON（type/run_id/ts/pid/...）。pid 目录天然隔离多写入方
  （hub 进程 vs 各 agent 的 MCP 进程），run_id 做文件名隔离单 run 体积，
  两段拼出「谁对哪次 run 记了什么」。
- **A5 幂等键 = (type, run_id, ts)。** ts 为写入端 UTC 微秒级时间戳；
  ingest 预载 event 表中 `agent_lifecycle.*` 的键集合，重复即跳过——
  重放整个目录不产生重复行；写入端同键重复行同样只落一行。默认
  `--dry-run` 预览，`--apply` 落库（与 archive_spinning_tasks.py 同约定）。

## 4. 数据流

```
Hub 进程                          agent 侧（MCP 进程）
  首心跳 ──► agent_ready jsonl       taskhub_heartbeat ──► heartbeat_sent_count jsonl
  心跳带 pid ──► 异步探测线程 ──► process_alive jsonl        （body 自动附 pid=自身）
        │                                     │
        └──────── $MIO_HOME/lifecycle/<pid>/<run_id>.jsonl ─┘
                                  │
                    scripts/ingest_lifecycle.py --apply（幂等键回填）
                                  ▼
                          event 表 type LIKE 'agent_lifecycle%'
```

对账规则（C3 判定）：agent 侧 jsonl `heartbeat_sent_count` 有 `delivered=false`
（或 sent_count>0）∧ Hub event 表该 run 无 `heartbeat` 行 → 网络/Hub 侧丢；
两侧都有 → 不是传输问题。C1 判定：lifecycle 目录下该 run 无任何 agent 侧文件。

## 5. 边界

- 不改 reaper / timeout / 门控 / 事务语义（心跳端点只追加 fire-and-forget 调用）。
- 不改 MCP 工具契约（heartbeat body 增加可选 `pid` 字段为纯附加）。
- `process_alive.alive=null` 表示探测不可用（无 psutil 且平台探测失败），
  按 unknown 记录，不猜结论。
- jsonl 不做轮转（单机量级：每 run 首心跳 2 行 + pid 变化探测；sent 计数
  每跳 1 行）；如未来膨胀，轮转是后续任务的独立决策。

## 6. 验收映射（DoD）

- 真实 run 产生三类证据：Hub 首心跳 + 带 pid 的心跳 → agent_ready/process_alive；
  agent 侧心跳 → heartbeat_sent_count；随后 `ingest_lifecycle.py --apply` 双通道
  核验 `select type,count(*) from event where type like 'agent_lifecycle%'`。
- 本地真相源：`Get-ChildItem "$env:MIO_HOME\lifecycle" -Recurse` 存在 jsonl。
- 幂等：`python scripts/ingest_lifecycle.py --dry-run` 可重复执行，
  `--apply` 二次执行 0 新增。
- 失败形态：埋点阻塞 submit 路径，或本地无落盘。
