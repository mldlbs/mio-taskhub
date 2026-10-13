# 设计规格：claim 注入 lessons

## 文档信息

| 项 | 值 |
| --- | --- |
| 任务 ID | `28f8fceb` |
| 对应需求 | `docs/taskhub/28f8fceb/requirement.md`（FR-1 / FR-2 / FR-3） |
| 版本 | v1.0 |
| 日期 | 2026-10-10 |
| 状态 | approved |

## 模块职责与边界

### 涉及模块

| 模块 | 现有职责 | 本次改动 |
| --- | --- | --- |
| `mio_taskhub/api/task_lessons.py`（新增） | — | 只读查询：同 workspace 已完成任务的三类线索提取 |
| `mio_taskhub/api/claim.py` | 任务认领（`ready` → `implementing`） | 响应体追加 `lessons` 字段 |
| `mio_taskhub/memory_store.py` | 经验存储（已实现，未启用） | 接上任务完成回调，供后续检索 |
| `mio_taskhub/api/tasks.py` | 任务 CRUD | 完成后触发经验写入 |

### 边界（明确不碰）

- **不改** `read_evidence.py`：读证机制已在用（96 条 / 42 个 Run），不重复造。
- **不改** `doc_quality.py` / `doc_lifecycle.py`：文档链门控与本任务正交。
- **不改** `Run` 模型：不加 `base_sha` / `post_sha` 字段。
- **不改** `task_stages.py` 的门控：本次不新增任何阶段门槛。
- **不改** `ops/git_sync.py`：它只做 ADR 投影，与本任务无关。

### 数据流

```
任务完成 (submit_result 成功)
   └─> _write_experience(task)        # 写 memory_store JSONL
         └─> 从 files/deliverables 提取布局
         └─> 从 run.result 提取测试线索

agent claim
   └─> task_lessons.recent_lessons(workspace, project)
         ├─> 查同 workspace 近期已完成任务
         ├─> 提取 test_hints / file_layout / pitfalls
         └─> 截断 + 限条数
   └─> 响应体并入 lessons              # 异常 → 空数组，不阻断
```

## 详细设计

### 一、`task_lessons.py` 接口设计

| 函数 | 签名 | 说明 |
| --- | --- | --- |
| `recent_lessons` | `(db, workspace, project, limit=5, max_chars=200) -> dict` | 主入口，返回 `lessons` 字典 |
| `_extract_test_hints` | `(results: list[str], max_chars: int) -> list[str]` | 从历史 result 提取测试线索 |
| `_extract_file_layout` | `(path_lists: list[list[str]], max_chars: int) -> list[str]` | 按目录前缀聚合文件布局 |
| `_extract_pitfalls` | `(failures: list[dict], max_chars: int) -> list[str]` | 提取已知坑 |

### 二、返回结构

```json
{
  "test_hints": ["pytest tests/ -q（1235 passed）"],
  "file_layout": ["mio_taskhub/api/", "tests/"],
  "pitfalls": ["doc_gate_exempt 迁移要把存量任务全部置 1"],
  "source_count": 4
}
```

| 字段 | 类型 | 空态| 截断规则 |
| --- | --- | --- | --- |
| `test_hints` | string[] | `[]` | 单条 `max_chars`（默认 200） |
| `file_layout` | string[] | `[]` | 同上 |
| `pitfalls` | string[] | `[]` | 同上 |
| `source_count` | int | `0` | — |

总条数上限 `limit`（默认 5），每个列表各自取前 `limit` 条。

### 三、测试线索提取规则

| 优先级 | 特征关键词 | 提取方式 |
| ---: | --- | --- |
| 1 | `pytest` / `unittest` / `npm test` / `npm run build` / `mvn test` / `gradle` / `vitest` / `jest` / `lint` | 截取所在句子 |
| 2 | `passed` / `failed` / `ALL PASS` / `N passed` | 截取所在句子 |
| 3 | `lint 0 errors` / `编译通过` / `build 成功` | 整句保留 |

匹配为**大小写不敏感**；同一句只保留一次；去重后按出现顺序取前 N条。

### 四、`claim` 集成方式

**挂载点选 `read_evidence.build_claim_context`。** 核对发现 claim 端点有**两个响应构造点**：

| 响应点 | 位置 | 场景 |
| --- | --- | --- |
| 已有 Run 复用 | `api/tasks.py:302-306` | agent 重复 claim 同一任务 |
| 正常认领 | `api/tasks.py:331-335` | 抢到新任务 |

两处都调用了 `read_evidence.build_claim_context(task)`（`read_evidence.py:196`）并`resp.update(...)`。把 `lessons` 挂进该函数即可**一处覆盖两条路径**，无需改 `claim.py`。

| 项 | 决策 | 理由 |
| --- | --- | --- |
| 挂载点 | `build_claim_context` 返回值追加 `lessons` 键 | 两个 claim 响应点已共用此函数，一处生效 |
| 是否改 `claim.py` | **不改** | 无需，两条路径自动覆盖 |
| 是否改 `read_evidence.py` | 改，但仅**追加一个键** | 不动 `required_reads` / `documents` / `branch` 等既有键 |
| 失败处理 | `build_claim_context` 内 `try/except`，异常返回空 lessons | FR-2 明确要求不阻断claim |
| 性能 | 复用既有 task 查询（`build_claim_context` 已持有 `task` 对象），仅多查同 workspace 历史任务 | 避免额外 RPC |
| 字段名 | `lessons` | 新增字段，旧客户端忽略即可 |

**注意 `claim.py` 内还有第三处调用**：`background.py:418` 的后台自动派单走`_claim_for`，**不经过 `build_claim_context`**。该路径同样需要 lessons 时，得在派单事件里另行注入（本次可先不做，见「待确认事项」）。

**不引入任何新的 `HTTPException`。** 这是本设计的硬约束，验收时会grep 核对。

### 五、经验写入（FR-3）

| 项 | 设计 |
| --- | --- |
| 触发时机 | `submit_result` 成功路径的 `_handle_success` 之后 |
| 写入内容 | 项目名、测试方式、文件布局、已知问题 |
| 存储 | `memory_store.add_entity`（既有 JSONL，**不新增表**） |
| 失败处理 | 写入失败只记日志，不影响任务状态 |
| 检索时机 | `claim` 时按 `project` 调`search_entities` 补充 `pitfalls` |

`memory_store` 现有实现核对：

| 函数 | 行号位置 | 可否直接用 |
| --- | --- | --- |
| `add_entity` | `memory_store.py:139` | 可直接用，但签名 `(name, etype, observations, relations)` **无 project 参数** |
| `search_entities` | `memory_store.py:94` | 可直接用；project 过滤靠 observations 文本子串匹配（`:115-119`） |
| `experience_reuse` | `memory_store.py:257` | 可直接用（记录复用关系） |
| `health` | `memory_store.py:281` | 可直接用（验证不再死代码） |

**由此得出的实现约束**：项目名必须写进 `observations` 任一条，否则后续按 `project` 检索不到。`etype` 须用 `_KIND_MAP`（`memory_store.py:32-40`）的合法值，经验类传 `experience`（映射到实体类型 `experience`）。

### 六、降级路径

| 场景 | 行为 |
| --- | --- |
| workspace 为空 | 退化用 `project` 匹配 |
| 两者都为空 | `lessons` 全空，`source_count=0` |
| workspace 非 git 仓 | **不影响** —— 本设计不依赖 git，只读 DB 里的 task/run |
| 历史任务数超限 | 只取最近 `limit*3` 条参与提取 |
| `memory_store` 目录不可写 | 记日志降级，不影响主流程 |

### 七、测试设计

| 用例 | 验证点 |
| --- | --- |
| `test_lessons_empty_when_no_history` | 无历史时返回空数组、`source_count=0`，**不抛异常** |
| `test_lessons_extracts_test_hints` | 从含 `pytest` 的 result 中提取线索 |
| `test_lessons_truncates_long_text` | 超长文本被截断到 `max_chars` |
| `test_lessons_respects_limit` | 条数不超过 `limit` |
| `test_lessons_falls_back_to_project` | workspace 为空时用 project 匹配 |
| `test_claim_includes_lessons_field` | claim 响应含 `lessons` 字段 |
| `test_claim_survives_lessons_failure` | 注入逻辑抛异常时 claim 仍 200 |
| `test_no_new_422_added` | grep `HTTPException(422` 数量不增（自查项） |

## 待确认事项

| 项 | 说明 | 影响 |
| --- | --- | --- |
| lessons 是否需要 MCP 侧暴露 | 当前只走 REST，MCP 工具暂不加 | 若 agent 主要走 MCP，需补`taskhub_lessons` 工具 |
| `limit` 默认值 | 现定5 条 × 200 字= 上限约 1000 字 | 若实测响应变大可下调 |
| 经验是否需人工审核 | 目前直接写入 JSONL，无审核 | 若经验质量差需加过滤 |
