> 软件项目文档链 · 7 件套  
> 上游：[测试验收](docs/taskhub/grab-mode/test.md) | 下游：—  
> 约定：正文**分点 + 换行**（每点一行，`-` 列表）；指引注释处填内容，未用章节删除。

# 部署运维（Runbook）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v0.1 |
| 最后更新 | 2026-10-07 12:00 |
| 状态 | draft |
| 负责人 | 高志谦 |
| 适用范围 | 抢单模式的部署、配置、灰度与回滚；后台线程与数据库迁移的运维要点 |
| 明确不含 | 前端大厅 UI（B 批）的部署；多实例 / 跨机部署（**明确不支持**） |

## 1. 环境与依赖

| 项 | 值 |
|---|---|
| 运行环境 | Windows 10/11（本机已验证）；Python 3.12（`mio-agent-runtime` 自带 uv 解释器）或仓内 `.venv` |
| 运行时依赖 | 同既有：FastAPI / SQLModel / SQLAlchemy / SQLite。**无新增第三方依赖** |
| 存储 | SQLite，默认 `~/.mio_taskhub/taskhub.db`，可用 `MIO_TASKHUB_DB` 指定；迁移到 staging 时用该变量指向独立文件 |
| 端口 | 默认 48620，可用 `MIO_TASKHUB_PORT` 覆盖 |
| 绑定 | 默认 `127.0.0.1`；**非 loopback 必须同时 `--auth`**，否则启动被拒（既有保护，不要绕过） |
| 外部服务 | **无**。不依赖任何网络服务 |
| 迁移依赖 | 启动时自动执行 `migrations.run_migrations`（加列 + 建表 + 建索引，幂等） |
| 新增配置 | `MIO_GRAB_*` 共 8 项（见 §3），**全部有默认值，不配也能跑** |

## 2. 部署步骤

1. 跑全量测试，确认不新增失败（已知既存失败：`tests/test_spinning_guard.py` 的 4 个 `webhook_guard`）
   ```bash
   cd E:/work/code/agent-dev/mio-taskhub
   ./.venv/Scripts/python.exe -m pytest tests/ -q -rf
   ```
2. 停旧 hub（**页面会断几秒**，前端会报 `Failed to fetch`，属正常）
3. 重新打包 exe（`dist` 目录改名保留旧版，不删除）
   ```bash
   ./scripts/build_exe.bat
   ```
4. 启动 hub（**必须用 WMI**，`Start-Process` / `&` 起的进程会被回收；且注意单实例 mutex 会让重复启动静默return）
   ```bash
   ([wmiclass]'Win32_Process').Create("`"$exe`" hub --worker")
   ```
5. **下一步工具调用里**复查进程与端点，确认真的起来了
   ```bash
   tasklist.exe | grep -i mio-taskhub
   curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:48620/api/v1/tasks
   ```
6. 验证迁移已生效（新增列应存在）
   ```bash
   curl -s "http://127.0.0.1:48620/api/v1/tasks" | head -c 200
   ```
7. 抽查抢单大厅与统计端点返回 200（即使还没有grab 任务）
   ```bash
   curl -s -o /dev/null -w "hall=%{http_code}\n" "http://127.0.0.1:48620/api/v1/tasks/hall?agent=probe"
   curl -s -o /dev/null -w "stats=%{http_code}\n" "http://127.0.0.1:48620/api/v1/grab/stats"
   ```
8. 提醒用户刷新页面（旧前端不带抢单入口，但接口已就绪）

## 3. 配置项

| 配置 | 默认 | 说明 |
|---|---|---|
| `MIO_GRAB_DEFAULT_WINDOW_SECONDS` | 600 | 抢中后开工窗口（秒） |
| `MIO_GRAB_SURGE_INTERVAL` | 300 | 悬赏阶梯间隔（秒） |
| `MIO_GRAB_BOUNTY_STEP` | 5 | 每阶梯涨幅 |
| `MIO_GRAB_BOUNTY_CAP` | 100 | 悬赏封顶；`<= 0` 时悬赏维度不计分 |
| `MIO_GRAB_DECLINE_DAILY_LIMIT` | 20 | 每 agent 每日拒单上限 |
| `MIO_GRAB_COOLDOWN_SECONDS` | 120 | 失约后冷却（**软惩罚，只影响排序**） |
| `MIO_GRAB_HYBRID_DISPATCH_AFTER` | 900 | hybrid 入池后多久转派单（补齐 `grab_deadline_at`） |
| `MIO_GRAB_HALL_COMPETITOR_WINDOW` | 300 | 竞争人数统计窗口（秒） |
| `MIO_HALL_DEFAULT_LIMIT` | 50 | 大厅默认返回条数（上限 200） |

- 配置变更对**已入池任务立即生效**（不做配置快照），故调度中改配置不会导致半旧半新。
- 所有项均可留空走默认值；未配置不报错。

## 4. 回滚

### 4.1 触发条件

- 新增后台线程导致既有 `dispatch` 任务派单行为变化（用 TC-14 清单核对）。
- 出现大面积 `503 db_locked`（锁冲突打满）。
- 大厅排序错乱或任务被重复占用。
- 统计口径异常导致看板误导。

### 4.2 回滚步骤

1. 停 hub（同 §2 步骤 2）
2. 代码回退到上一版本；`dist` 旧目录直接改名回来即可
3. 重启并复查端点
4. **数据库不回滚**——新增列与 `graattempt` 表保留即可，旧版代码会忽略它们
5. 若数据被污染（任务卡在异常状态）：
   ```sql
   -- 先 SELECT 确认再UPDATE；不要盲跑
   SELECT id, state, stage, grab_mode FROM task WHERE state NOT IN ('completed','failed','cancelled');
   ```
6. 记录：回滚原因、版本、是否需要数据修正

> ⚠️ 迁移**无自动降级**。确需降级时手工 `ALTER TABLE task DROP COLUMN ...`（SQLite 3.35+），**执行前确认该列无数据依赖**（`grab_mode` 降级后抢单代码不再可用）。

## 5. 监控与告警

| 指标 | 来源 | 阈值 / 判断 |
|---|---|---|
| hub 健康 | `GET /healthz` | 非 200 即告警 |
| 线程存活与失败 | `GET /api/v1/status`（`thread_health`） | `grab-bounty` / `grab-window` 任一不在列表即告警 |
| 线程**最近成功时间** | 线程健康指标 | 超过 3 个周期（>90s）无成功推进即告警（**光有心跳不算健康**） |
| 池深度 | `GET /api/v1/grab/stats` 的 `pool_depth` | 持续 > 500 需评估；为 0 且有 ready 任务则派单可能异常 |
| 平均等待 | `avg_wait_seconds` | > 3600 说明任务滞销，考虑提高 `MIO_GRAB_SURGE_INTERVAL` 频率 |
| 抢单成功率 | `success_rate` | 持续 < 0.1 说明池子太挤或任务不适配 |
| 失约率 | `miss_rate` | > 0.3 说明开工窗口过短或 agent 能力不足，考虑调 `MIO_GRAB_DEFAULT_WINDOW_SECONDS` |
| 锁冲突 | 端点 503 `db_locked` 计数 | 出现即查后台线程是否长事务持锁 |
| 限流 | 429 `rate_limited` | 批量脚本需降频（全局 120 req/min/IP） |

- **WS 广播不保证送达**（无 outbox 补发），所以**告警不能依赖事件流**，必须靠轮询上述端点。

## 6. 常见故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 大厅 404 | 路由前缀写错（漏 `/api/v1`） | 正确路径 `/api/v1/tasks/hall` |
| 大厅 400 `request.invalid_param` | `agent` 缺失或 `limit <= 0` | 补 `agent`；`limit` 取 1..200 |
| 抢单 422 `grab.not_grab_mode` | 任务是 `dispatch` 模式 | 改用 `POST /tasks/claim`，或让发布者改 `grab_mode=grab` |
| 抢单 409 `grab.task_taken` | 被别人先抢到 | 读响应里的 `winner` 换任务；**不要重试同一请求** |
| 抢单 409 `grab.agent_busy` | 该 agent 已有其他在途 run | 先完成在手任务；不会返回不相关 run |
| 大厅里看不到某任务 | 不是 `queued+ready`，或 `grab_mode=dispatch` | 查任务 `state` / `stage` / `grab_mode` |
| hybrid 任务永不被派单 | 缺 `grab_deadline_at` | 后台 tick 会兜底补齐；查 `grab_missed` / 线程日志 |
| 悬赏不涨 | 已达 `bounty_cap`；或任务不在池（`pool_entered_at` 为空） | 查 `bounty` 与 `last_surge_level` |
| 统计分位显示 0 | 无样本时应为 `null` | 若为 0 说明口径被改坏，按 Spec §4.13 核对 |
| 心跳 403 `run.not_owner` | 传的 `agent` 与 run 持有者不符 | 用 claim 时那个 agent 名 |
| 心跳 409 `run.not_active` | run 已结束 | 刷新任务状态，不要试图改终态 |
| 提交 409 `run.result_already_submitted` | 成功已交接过 | 正常；查任务详情确认最终状态 |
| 提交 422 | **Read Evidence 门控**（非权限问题） | 先 `taskhub_read_document` 补读 required 文档 |
| 端点连不上但页面之前能用 | hub 是单实例 mutex，重复启动会**静默 return** | `tasklist \| grep mio-taskhub` 查在跑实例，别怀疑参数 |
| 迁移后旧任务行为变了 | 存量任务应全为 `dispatch` | 查 `SELECT grab_mode, COUNT(*) FROM task GROUP BY grab_mode`，非 `dispatch` 的存量行需修正 |
| 后台线程重复加价 | CAS 失效 | 确认 `WHERE last_surge_level = :expected` 条件在更新语句里 |
| 回收把正在开工的任务放回池 | CAS 失效 | 确认回收与心跳**都**以 `run.state` 为 CAS 条件 |
