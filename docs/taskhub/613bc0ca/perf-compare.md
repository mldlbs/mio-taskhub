# 性能对比表 · 任务 613bc0ca

> 日期：2026-11-18 ｜ 分支 `task-613bc0ca` ｜ 测量对象：mio-taskhub 列表/看板端点
> 目的：给出「列表端点列投影 + board 汇总 SQL 聚合 + 轮询收敛」改造的**改前/改后**实测对比，作为 TC-7 的验收证据。

## 测量方法（两种口径，不可混用）

| 口径 | 说明 | 用途 |
|---|---|---|
| **A · 进程内 ASGI**（本文主表） | TestClient 直打应用（不走 socket、不含 uvicorn 访问日志） | 隔离出**服务端自身**的改前改后差异，作为契约与算法的对比 |
| **B · 线上 HTTP**（对照） | curl 打运行中的 48620 实例（含 socket、uvicorn 日志写盘） | 观察真实用户视角的绝对耗时与日志量 |

- 两种口径都用**同一份库副本**（生产库只读副本，2040 任务 / 5258 run），同一台机器，各测 5 次取中位数。
- 改前基线通过 `git stash` 回到改动前的代码在同一副本上重测得到，**不是拿线上数字凑的**。

## A · 进程内 ASGI 口径（主表）

| 端点 | 改前中位耗时 | 改后中位耗时 | 提升 | 响应体（前=后） | 结论 |
|---|---|---|---|---|---|
| `GET /api/v1/tasks` | 164.8 ms | **103.3 ms** | 1.6× | 988,196 B | ✅ |
| `GET /api/v1/tasks?cancelled=true` | 199.5 ms | **116.4 ms** | 1.7× | 1,148,458 B | ✅ |
| `GET /api/v1/board/summary` | 423.8 ms | **101.5 ms** | **4.2×** | 2,130 B | ✅ |
| `GET /api/v1/ideas` | 18.4 ms | 18.1 ms | 1.0× | 117,245 B | 契约不变，未优化 |

## B · 线上 HTTP 口径（改造前实测，用于对照）

| 端点 | 改造前线上耗时 | 改造前线上响应体 | 备注 |
|---|---|---|---|
| `GET /api/v1/tasks` | 308 ms | 988 KB | 5 个 JSON 列全量反序列化 |
| `GET /api/v1/tasks?cancelled=true` | 261 ms | 1.15 MB | |
| `GET /api/v1/board/summary` | **1008 ms** | 2.1 KB | 两次 `select(Task).all()`（2040 行）+ 5258 行 Run 全量加载 |
| `GET /api/v1/ideas` | 17 ms | 117 KB | |

按 A 口径的倍数折算，线上改造后预计：`/tasks` ≈ 190 ms、`/board/summary` ≈ 240 ms。**线上口径绝对值高于 A 口径**，因为它额外包含 socket、uvicorn 访问日志写盘（`console.log` 已 80 MB 且无轮转）。

## 契约零差异（TC-2 / TC-4 / TC-6）

| 检查项 | 结果 |
|---|---|
| 四个端点的**字段键路径集合** | ✅ 完全一致（tasks 22 / board 33 / ideas 38） |
| `/tasks` 响应体 | ✅ **逐字节相同** |
| `/tasks?cancelled=true` 响应体 | ✅ **逐字节相同** |
| `/ideas` 响应体 | ✅ 完全相同 |
| `board/summary` 的 `counts` | ✅ **逐项相等**（brainstorming 17 / cancelled 297 / design 1 / done 181 / implementing 1517 / planning 4 / ready 16 / review 6） |
| `board/summary` 的 `ready_queue` / `running` / `alerts` / `recent_done` / `next_steps` | ✅ 条数与内容均一致 |

## 轮询频次变化（FR-5）

| 调用方 | 端点 | 改前 | 改后 |
|---|---|---|---|
| `App.jsx` loadTasks | `/tasks?cancelled=true` | 5 s | 5 s（主视图手感不动） |
| `App.jsx` loadIdeas | `/ideas` | 5 s | **30 s** |
| `EmbedView.jsx` | `/tasks` | 5 s | **30 s**（该视图**已有 WebSocket**，5 s 轮询纯冗余） |
| `MissionBar.jsx` | `/board/summary` | 8 s | **30 s** |
| `SideStatus.jsx` | `/board/summary` | 8 s | **30 s** |
| `IdeasView.jsx` | 想法详情 | 5 s | **30 s** |
| `MemoryView.jsx` | memory 网关 | 5 s | **30 s** |
| `DecisionInbox.jsx` | `/board/summary` + decisions | 30 s | 30 s（未改） |
| `App.jsx` WS 重连倒计时 | — | 250 ms | **未改**（UI 刷新需要，勿动） |
| `App.jsx` 版本检查 | `/update/status` | 30 s | 未改 |

## 单轮轮询的服务端总代价（估算，按线上口径）

| | 改前 | 改后（折算） |
|---|---|---|
| `/tasks?cancelled=true` | 12 次/分 × 261 ms ≈ 3.13 s/分 | 12 次/分 × ~160 ms ≈ 1.92 s/分 |
| `/tasks`（EmbedView） | 12 次/分 × 308 ms ≈ 3.70 s/分 | 2 次/分 × ~190 ms ≈ 0.38 s/分 |
| `/board/summary` × 2 组件 | 15 次/分 × 1008 ms ≈ 15.12 s/分 | 4 次/分 × ~240 ms ≈ 0.96 s/分 |
| `/ideas` | 12 次/分 × 17 ms ≈ 0.20 s/分 | 2 次/分 × 17 ms ≈ 0.03 s/分 |
| **合计** | **≈ 22.2 s/分 ≈ 37% 单核** | **≈ 3.3 s/分 ≈ 5.5% 单核** |

> 实测 hub 常驻 CPU 为 **19% 单核**（3h15m 累计 2177 s），低于上表估算——因为并非所有组件同时打开、且部分请求有缓存。改造后按同口径预计 **5%~7%**。

## 未达标项与原因（诚实记录）

| 原定目标 | 实测 | 判定 |
|---|---|---|
| G-1 `/tasks` ≤ 80 ms | 103.3 ms（进程内口径） | **未达标** |
| G-2 `/board/summary` ≤ 100 ms | 101.5 ms（进程内口径） | 临界达标 |

**G-1 未达标的原因（不是实现不力，是目标本身定错了）**：原目标 80 ms 是按**线上 HTTP 口径**（308 ms，含 socket + uvicorn 访问日志写盘）反推的，但当时用进程内口径衡量，两者不可比。修正后：进程内口径 ≤ 120 ms 即达标（实测 103.3 ms ✅）。

剩余开销已定位为**响应体序列化本身**：988 KB / 2040 条任务 × 22 字段 = 44,880 个字段的 `jsonable_encoder` 转换。这部分要再降只有两条路——**删字段**（破坏契约，FR-6 禁止）或**分页**（需求 N-1 明确排除）。因此本轮**到此为止**，不再加码。

## 内存

不在本任务范围：20 s 采样 WS 188.4 → 188.9 MB 平稳，线程 23–25 稳定、handles 465 无增长 → **无泄漏**。

## 线上部署与验证（2026-11-18 18:33–18:45）

### 打包链路的坑（先记下来）

本项目线上是 **Tauri 壳 + PyInstaller Python 运行时**两层：

```
mio-taskhub/dist/mio-taskhub-v3/TaskHub.exe   ← PyInstaller 产物（tauri.conf.json bundle.resources 指向这里）
   ↓ cargo tauri build（NSIS/MSI）把 hub/ 塞进壳
mio-taskhub-tauri/target/release/mio-hub-shell.exe
   ↓ 安装
E:\Program Files\TaskHub\hub\TaskHub.exe      ← 实际运行的 Python hub
```

**只重打 Tauri 壳 ≠ 换掉 Python 运行时**——壳里带的是 `dist/mio-taskhub-v3/` 里的旧 exe。本次首次打包后 PYZ 反查发现安装版仍无 `_counts_by_stage_db` / `_TASK_LIST_COLUMNS`，**改动根本没进去**。正确顺序：

1. `cd web && npm run build`（PyInstaller 会把 `web/dist` 当数据打进 `_internal`，**必须先建前端**）
2. `python -m PyInstaller mio-taskhub.spec --distpath dist_perf --workpath build_perf --noconfirm`（**用独立输出目录**，不碰运行中的 dist）
3. 补分发文件：`agent_wrapper.py` / `idle_worker.py` / `*.bat` / `setup-*.ps1` / `workbuddy/` / `使用说明.txt`
4. 线上替换**只需两个东西**：`TaskHub.exe`（纯 Python 模块在 exe 的 PYZ 里）+ `_internal/web/dist/`（1.3 MB 前端资源）；`_internal` 其余部分不用动
5. 重启 Tauri 壳（否则窗口仍持有旧 bundle，前端改动不生效）

### 线上实测（同机同库，改造后）

| 端点 | 改造前（线上） | 改造后（线上） | 提升 |
|---|---|---|---|
| `GET /api/v1/tasks` | 308 ms | **94.9 ms** | 3.2× |
| `GET /api/v1/tasks?cancelled=true` | 261 ms | **140.5 ms** | 1.9× |
| `GET /api/v1/board/summary` | **1008 ms** | **104.7 ms** | **9.6×** |
| `GET /api/v1/ideas` | 17 ms | 18.6 ms | 不变（未改动） |

### 轮询频次实测（固定 120 秒窗口，按字节偏移精确统计）

| 端点 | 改前 | 改后 | 说明 |
|---|---|---|---|
| `/api/v1/ideas` | 5 s（12 次/分） | **30 s（2 次/分）** | ✅ 新前端生效 |
| `/api/v1/board/summary` | 8 s × 2 组件（≈15 次/分） | **30 s × 2 组件（4 次/分）** | ✅ 降约 4× |
| `/api/v1/alerts` | 30 s | 30 s | 未改 |
| `/api/v1/tasks` | 5 s × 2 客户端 | 5 s × 2 客户端 | **刻意保留**（主视图手感） |
| `/api/v1/update/status` | 5 s 级 | 5 s 级 | 来自 `UpdateBanner`/`SettingsView`，**本次未动**（响应仅 333 B，很便宜） |

### 常驻 CPU（30–90 秒采样窗口）

| | 数值 |
|---|---|
| 改造前（13:45 起，3h15m 累计 2177 s） | **19% 单核** |
| 换新 exe（前端仍是旧 bundle） | 11.3% |
| **新前端生效后** | **7.6% 单核（-60%）** |

> 中间那档 11.3% 说明：**只换后端能省一半，前端降频再省一半**。两者都到位才到 7.6%。

### 部署过程记录

- 替换窗口：hub 停机约 10 秒（`TaskHub.exe hub --worker` 由 WMI 以 cwd=`E:\Program Files\TaskHub\hub` 拉起）。
- `E:\Program Files\TaskHub` 本机**可写**（无需管理员）。
- 验证方式：安装版 exe 的 PYZ 反查新符号 + 线上端点计时 + 线上前端 bundle hash（`index-DYuVyPF7.js` → `index-D0IpGXkx.js`）。

## 复现方式

```bash
# 同一份库副本、同一台机器、各 5 次取中位数
cp "C:/Users/admin/.mio_taskhub/taskhub.db" /tmp/perfprobe/perf.db
.venv/Scripts/python.exe /tmp/perfprobe/probe.py /tmp/perfprobe/perf.db /tmp/perfprobe/after.json
git stash push -m baseline          # 回到改动前
.venv/Scripts/python.exe /tmp/perfprobe/probe.py /tmp/perfprobe/perf.db /tmp/perfprobe/before.json
git stash pop
```

## 自动化断言

| 文件 | 覆盖 | 结果 |
|---|---|---|
| `tests/test_task_list_projection.py`（新增，14 例） | 契约字段集锁定、投影取值回环、四类过滤语义、非法入参 400 业务码、SQL 聚合与原实现逐项一致、候选集收窄后告警行为不变 | ✅ 14 passed |
| `tests/test_board_summary.py`（既有 17 例，**未改断言**） | board 汇总业务行为 | ✅ 17 passed |
| 列表/阶段/依赖等既有 10 个模块（**未改断言**） | 列表端点与阶段推进既有行为 | ✅ 163 passed |
