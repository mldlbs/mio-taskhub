# mio-taskhub 架构全面分析报告

> 分析日期：2026-09-10 | 版本：0.1.0 | 代码行数：~9,400（后端+测试）

---

## 1. 架构概览

### 系统定位
本地跨 Agent 任务协调中心（Single-user Service），为多个 AI Agent（opencode、claude-code、codex 等）提供统一的任务调度、状态管理、知识图谱和实时通知能力。

### 核心业务目标
- 多 Agent 任务协调：注册→领取→心跳→提交的完整生命周期
- 状态机驱动：20+ 种合法状态转换，强制执行业务规则
- 实时通知：WebSocket 广播任务变更
- 知识持久化：Memory JSONL + ADR Git 投影

### 架构风格
**分层单体架构 + 事件驱动组件**

| 层 | 组件 |
|---|---|
| 表现层 | React SPA (33 components) + FastAPI REST/WS |
| 应用层 | API 路由 (17 modules) + Background Jobs (4 threads) |
| 领域层 | State Machine (21 transitions) + Models (18 tables) |
| 基础设施层 | SQLite + JSONL + Git + MCP |

### 架构优点
- **状态机严格**：所有状态变更必须经过 `validate_transition()`，不可绕过
- **Outbox 模式**：事件持久化 → 自动广播，保证一致性
- **模块化清晰**：P0-P5 重构后职责边界明确
- **本地优先**：SQLite + 文件系统，零外部依赖即可运行

### 架构缺点
- **单进程瓶颈**：所有后台任务（4 threads）共享同一进程
- **SQLite 并发限制**：WAL 模式下读写并发仍受限
- **前端单文件**：App.jsx 458 行，承担路由+状态+视图切换
- **无 Docker 支持**：仅 Windows PyInstaller 打包

### 适用场景
✅ 本地开发/测试环境多 Agent 协调
✅ 小团队（1-5 人）Agent 工作流
❌ 高并发生产环境（>100 agents）
❌ 多租户 SaaS 部署

---

## 2. 业务架构分析

### 核心业务流程
```
用户想法 → idea (new/fermenting/formed)
  → breakdown → tasks (queued)
    → agent claim → run (claimed/running)
      → heartbeat → progress update
        → submit_result → completed/failed
          → review → done
            → ADR git projection
```

### 领域模型设计
**18 个 ORM 模型，3 个核心聚合：**

| 聚合根 | 实体 | 值对象 |
|--------|------|--------|
| Task | Run, Subtask, GitRef, TaskEvent, TaskReview, HistoryEvent | TaskState, TaskStage, TaskKind |
| Idea | IdeaChange, IdeaHistory, Discussion, DiscussionMessage | IdeaStatus, IdeaType |
| Agent | (无子实体) | AgentStatus |

### 业务耦合度评估

| 耦合点 | 严重度 | 说明 |
|--------|--------|------|
| Idea ↔ Task | 中 | `idea_id` 外键 + breakdown 流程 |
| Task ↔ Run | 高 | 1:N 关系，Run 生命周期依赖 Task 状态 |
| Agent ↔ Run | 高 | 心跳检测 + 超时判定 |
| ScheduledJob ↔ Task | 中 | `create_task` action 类型直接创建 Task |

### 风险点
1. **Idea 模块膨胀**：`ideas.py` 1214 行，包含 ADR、模板、搜索、评分、批量操作等 6+ 个职责
2. **业务逻辑散落**：状态转换逻辑分布在 `state_machine.py`、`transitions.py`、`api/tasks.py` 三处
3. **Memory Gateway 耦合**：`memory_store.py` 直接操作 JSONL 文件，与 API 层无清晰边界

### 优化建议
- Idea 模块拆分为：`ideas.py` (CRUD) + `adr.py` (ADR 操作) + `idea_scoring.py` (评分)
- 统一状态转换入口：所有转换必须通过 `transitions.apply_transition()`
- Memory Gateway 抽象为接口，支持未来替换存储后端

---

## 3. 系统架构分析

### 模块评分

| 模块 | 行数 | 内聚性 | 耦合度 | 评分 |
|------|------|--------|--------|------|
| `state_machine.py` | 319 | 高（纯逻辑） | 低（无内部依赖） | 9/10 |
| `models.py` | 447 | 高（ORM 定义） | 低（仅 SQLAlchemy） | 8/10 |
| `api/tasks.py` | 671 | 中（混合职责） | 中 | 6/10 |
| `api/ideas.py` | 1214 | 低（6+ 职责） | 高 | 4/10 |
| `background.py` | 300 | 中（4 阶段调度） | 中 | 7/10 |
| `events.py` | 112 | 高 | 低 | 8/10 |
| `db.py` | 65 | 高 | 低 | 9/10 |
| `cron_engine.py` | 263 | 高 | 低 | 8/10 |
| `mcp_server.py` | 920 | 中（25+ tools） | 低（仅 HTTP） | 7/10 |

### 重构建议
1. **`api/ideas.py` (1214行)** → 拆分为 4 个模块（P0 优先级）
2. **`mcp_server.py` (920行)** → 拆分为 `mcp_server.py` + `mcp_tools.py`
3. **`api/tasks.py` (671行)** → 已在 P2 拆分，保持现状

---

## 4. 数据架构分析

### 数据模型
- **SQLite**：18 张表，WAL 模式，QueuePool(5, 10)
- **JSONL**：Memory 知识图谱（`~/.mio_taskhub/memory.jsonl`）
- **Git**：ADR 投影（`docs/adr/`）

### 数据一致性
| 机制 | 实现 | 评估 |
|------|------|------|
| ORM 事务 | SQLModel Session | ✅ 强一致性 |
| Event Outbox | `after_flush` → `after_commit` | ✅ 最终一致性 |
| Git 投影 | 后台线程异步同步 | ⚠️ 可能延迟 |
| Memory JSONL | 文件锁 `threading.Lock` | ⚠️ 进程内互斥 |

### 数据风险
1. **SQLite 单写**：高并发写入时 WAL 可能积累 checkpoint
2. **JSONL 无索引**：查询需全表扫描，O(n) 复杂度
3. **无备份机制**：数据库文件无自动备份

### 优化方案
- 添加 SQLite 定期 checkpoint 命令
- Memory 考虑迁移至 SQLite FTS5 全文搜索
- 实施数据库自动备份（每日快照）

---

## 5. 性能分析

### 请求链路
```
Client → FastAPI → Router → Dependency → DB Session → SQLite
                 ↓
            WebSocket ← after_commit hook ← Event
```

### 性能瓶颈 TOP 10

| # | 瓶颈 | 影响 | 优先级 |
|---|------|------|--------|
| 1 | SQLite WAL checkpoint | 写入延迟 | P1 |
| 2 | Memory JSONL 全表扫描 | 查询 O(n) | P1 |
| 3 | Board summary 聚合查询 | 每次多表 JOIN | P2 |
| 4 | `ideas.py` 单文件 1214 行 | 模块加载慢 | P2 |
| 5 | WebSocket 广播同步阻塞 | 事件广播延迟 | P2 |
| 6 | Cron engine 10s 轮询 | 任务触发延迟 | P3 |
| 7 | Heartbeat sweep 10s 轮询 | 超时检测延迟 | P3 |
| 8 | 无连接池监控 | 资源泄漏不可见 | P3 |
| 9 | 无查询缓存 | 重复查询无优化 | P3 |
| 10 | 前端 33 组件全量加载 | 首屏渲染慢 | P3 |

### 优化优先级
- **P1**：SQLite WAL checkpoint + Memory 索引
- **P2**：Board summary 缓存 + WebSocket 异步广播
- **P3**：前端懒加载 + 查询缓存

---

## 6. 可扩展性分析

### 横向扩展能力
| 维度 | 当前 | 评估 |
|------|------|------|
| Agent 数量 | 受 SQLite 连接限制 | ⚠️ ~50 agents 上限 |
| 任务吞吐 | 单进程调度 | ⚠️ ~100 tasks/s 上限 |
| API 并发 | QueuePool(5,10) | ⚠️ ~50 concurrent requests |
| 前端用户 | 单用户设计 | ✅ 符合预期 |

### 扩展方案
1. **短期（3 月）**：优化连接池参数，支持 ~100 agents
2. **中期（6 月）**：引入 Redis 缓存层，提升并发
3. **长期（1 年）**：如需多用户，考虑 PostgreSQL + 任务队列（Celery/RQ）

---

## 7. 高可用分析

### SPOF 清单

| SPOF | 影响 | 缓解措施 |
|------|------|----------|
| SQLite 文件 | 数据丢失 | ⚠️ 无自动备份 |
| 主进程崩溃 | 所有后台任务停止 | ⚠️ 无自动重启 |
| WebSocket 服务 | 实时通知中断 | ✅ 客户端自动重连 |
| MCP Server | Agent 通信中断 | ✅ HTTP 重试机制 |

### HA 优化建议
1. 实施 SQLite 自动备份（每小时快照）
2. 添加进程守护（systemd/supervisord）
3. 实施优雅关闭信号处理
4. 添加 readiness probe 检查 DB 连接

---

## 8. 安全架构分析

### 安全机制

| 机制 | 实现 | 评估 |
|------|------|------|
| 身份认证 | Bearer Token | ⚠️ 无过期/刷新 |
| 授权 | 无（单用户） | ✅ 符合设计 |
| SQL 注入 | SQLModel ORM | ✅ 参数化查询 |
| XSS | React 自动转义 | ✅ DOMPurify 额外保护 |
| CSRF | 无（API-only） | ✅ 适用 |
| SSRF | MCP Server HTTP 调用 | ⚠️ 无 URL 白名单 |
| 数据泄露 | 本地文件存储 | ✅ 无网络暴露 |

### 安全风险等级：**低**
- 主要风险：MCP Server 的 SSRF 风险（可调用任意 URL）
- 建议：添加 URL 白名单或代理过滤

---

## 9. 云原生与 DevOps 分析

### 当前状态
| 维度 | 状态 | 评分 |
|------|------|------|
| Docker | ❌ 无 | 0/10 |
| Kubernetes | ❌ 无 | 0/10 |
| CI/CD | ✅ GitHub Actions | 6/10 |
| 构建 | ✅ PyInstaller | 5/10 |
| 环境一致性 | ⚠️ 仅 Windows | 3/10 |

### DevOps 成熟度评级：**3/10**

### 改进建议
1. **P0**：添加 Dockerfile + docker-compose.yml
2. **P1**：CI/CD 增加 lint + type check + coverage
3. **P2**：添加 staging 环境自动部署
4. **P3**：Kubernetes Helm chart

---

## 10. 可观测性分析

### 当前能力

| 维度 | 实现 | 评分 |
|------|------|------|
| Logging | 结构化 JSON (JSONFormatter) | 7/10 |
| Metrics | Prometheus format (`/metrics`) | 6/10 |
| Tracing | ❌ 无分布式追踪 | 0/10 |
| 告警 | ❌ 无告警体系 | 0/10 |
| 健康检查 | `/healthz` + `/readyz` | 7/10 |

### 可观测性评分：**5/10**

### 缺失能力
1. 分布式追踪（OpenTelemetry）
2. 告警规则（Prometheus AlertManager）
3. 日志聚合（ELK/Loki）
4. 性能 Profiling（py-spy）

---

## 11. 技术债分析

### 技术债清单

| # | 债务 | 风险等级 | 影响范围 | 偿还成本 |
|---|------|----------|----------|----------|
| 1 | `ideas.py` 1214 行 | P1 | 维护性 | 2h |
| 2 | `mcp_server.py` 920 行 | P2 | 可读性 | 1h |
| 3 | App.jsx 458 行 | P2 | 前端维护 | 4h |
| 4 | 无 Docker 支持 | P1 | 部署 | 2h |
| 5 | 无类型注解覆盖 | P2 | 代码质量 | 8h |
| 6 | 硬编码 Windows 路径 | P2 | 跨平台 | 1h |
| 7 | 测试不覆盖 MCP 工具 | P3 | 质量保证 | 4h |
| 8 | `test_two_agents_race` 被排除 | P3 | 测试可靠性 | 2h |

### 偿还路线图
- **本月**：#1 + #4（ideas 拆分 + Docker）
- **下月**：#2 + #3（mcp 拆分 + 前端重构）
- **Q4**：#5 + #6 + #7 + #8（类型注解 + 跨平台 + 测试补全）

---

## 12. 成本分析

### 成本构成

| 类型 | 当前成本 | 说明 |
|------|----------|------|
| 云资源 | ¥0 | 本地运行 |
| 人力 | ~2h/周 | 维护+迭代 |
| 存储 | <1MB | SQLite + JSONL |
| 运维 | ¥0 | 无外部服务 |

### 降本建议
- 当前已是最优成本结构（本地优先）
- 如需扩展，考虑 Serverless 方案（Cloudflare Workers + D1）

---

## 13. 风险分析

### 风险矩阵

| 风险 | 等级 | 影响 | 缓解措施 |
|------|------|------|----------|
| SQLite 数据损坏 | P1 | 数据丢失 | 定期备份 + WAL checkpoint |
| 单进程崩溃 | P1 | 服务中断 | 进程守护 + 健康检查 |
| MCP SSRF | P2 | 安全漏洞 | URL 白名单 |
| 前端单文件膨胀 | P2 | 维护困难 | 组件拆分 |
| 测试覆盖不足 | P2 | 回归风险 | 补充测试 |
| Windows 依赖 | P2 | 跨平台受限 | Docker 化 |
| 无分布式追踪 | P3 | 故障定位难 | OpenTelemetry |
| 无告警体系 | P3 | 问题发现延迟 | Prometheus AlertManager |

---

## 14. 架构评分

| 维度 | 评分 | 说明 |
|------|------|------|
| 可扩展性 | 7 | 单机设计合理，但受限于 SQLite |
| 性能 | 7 | 本地场景足够，高并发需优化 |
| 可维护性 | 8 | P0-P5 重构后模块清晰 |
| 安全性 | 7 | 单用户场景安全，SSRF 需加固 |
| 高可用 | 5 | 无自动恢复、无备份 |
| 成本控制 | 10 | 零外部成本 |
| 技术先进性 | 7 | FastAPI + SQLModel + 状态机 |
| 可观测性 | 5 | 有日志+指标，缺追踪+告警 |

### 总分：56/80 → **70/100**

### 综合评级：**良好架构**（70-79）
> 适合本地单用户场景，架构设计合理，代码质量良好。主要短板在运维层面（高可用、可观测性），这是单用户本地服务的合理取舍。

---

## 15. 最终结论

### 架构优势
1. **状态机设计严谨**：21 种转换 + 验证器，业务规则不可绕过
2. **模块职责清晰**：P0-P5 重构后，29 个模块各司其职
3. **事件驱动解耦**：Outbox + auto-broadcast 保证最终一致性
4. **零外部依赖**：SQLite + 文件系统即可完整运行
5. **测试覆盖良好**：505 个测试，覆盖核心业务逻辑

### 架构问题
1. **`ideas.py` 膨胀**：1214 行，6+ 职责，需拆分
2. **无 Docker 支持**：仅 Windows PyInstaller 打包
3. **高可用缺失**：无自动恢复、无备份、无进程守护
4. **可观测性不足**：无分布式追踪、无告警体系
5. **前端单文件**：App.jsx 458 行，路由+状态+视图混合

### 优化优先级排序

#### P0（立即处理）
- `ideas.py` 拆分（6+ 职责 → 4 个模块）

#### P1（短期处理，1-2 周）
- 添加 Dockerfile + docker-compose.yml
- SQLite 自动备份机制
- `mcp_server.py` 拆分

#### P2（中期处理，1-2 月）
- 前端 App.jsx 组件化重构
- 类型注解覆盖率提升
- MCP SSRF 防护

#### P3（长期规划，3-6 月）
- OpenTelemetry 分布式追踪
- Prometheus AlertManager 告警
- 测试覆盖率提升至 80%+
- 跨平台支持（Linux/macOS）

### 架构演进路线图

| 时间 | 里程碑 | 目标 |
|------|--------|------|
| 3 月 | Docker 化 + ideas 拆分 | 可容器化部署，代码可维护 |
| 6 月 | 前端重构 + 类型注解 | 代码质量 80+ |
| 1 年 | 可观测性完善 | 生产级监控告警 |
| 3 年 | 如需扩展 → 微服务化 | 支持多用户/高并发 |

### CTO 视角总评

> **mio-taskhub 是一个设计良好的本地 Agent 协调中心。** 状态机驱动的架构保证了业务规则的严格性，事件驱动的解耦设计为未来扩展预留了空间。作为 v0.1.0，它的完成度很高——核心功能完整、测试覆盖良好、代码结构清晰。
>
> **主要风险不在架构本身，而在运维层面。** 对于本地单用户场景，当前的高可用缺失是合理取舍；但如果未来需要支持团队协作或多 Agent 集群，需要提前规划 Docker 化和可观测性建设。
>
> **建议下一步：** 先完成 `ideas.py` 拆分（P0），再添加 Docker 支持（P1），为后续迭代打下基础。
