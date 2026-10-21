# 窄门禁（空 diff 拦截）可实施方案 v1.0

> 日期：2026-10-20 ｜ 上游决策：`product-decision-20261020.md` §1 / §1.1
> 目标：让"声明成功但本次零改动"的提交**被服务端拦下**，且**人能在界面上看到**
> 原则：只在"明确的假完成"上拦截；其余一律标注；**宁可门是哑的，不能门是堵的**

---

## 0. 边界（做什么 / 不做什么）

**做**：① 服务端自行采集工作区改动证据（不采信 Agent 自报）；② 空改动 + `success=true` → 422；③ 拦截事件对**人可见**（toast + 执行历史 + 每次执行卡片）；④ 标注项（测试未跑等）**只提示不拦**。

**不做**：不跑测试（测试执行器独立立项，见 §7 批次 B）；不新建 UI 面板；不自动预填 ReviewPanel；不把 diff 全文入库。

---

## 1. 判据定义（先定义清楚，再写代码）

### 1.1 "本次 run 期间有改动"的精确定义

```
changed = (head_after != head_before)          # agent 提交了 commit
       OR (porcelain_after != porcelain_before) # 只留工作区改动（未提交）
       OR (numstat_insertions + numstat_deletions > 0)
```

- **为什么要两个快照**：只比 HEAD 会漏掉"改了但没 commit"的常见情况；只比工作区会漏掉"已 commit"的情况。两个都拍，任一变化即算有改动。
- **快照时机**：**run 创建时**拍 `before`（`create_run`，`api/runs.py:87-100`），**submit 时**拍 `after`。
  - 为什么不在 submit 时才拍：否则"run 开始前工作区就脏"会被算成"本次有改动"→ 漏放（放行假完成）。先拍 before 才能判"本次"。
  - before 快照失败**不阻断 claim**（best-effort），后续判据退化为"只看 after 是否非空"（偏 fail-open，可接受）。
- **采集命令**（`git -C <workspace>`）：
  - `rev-parse HEAD` → head sha
  - `status --porcelain` → 工作区状态字符串
  - `diff --numstat HEAD` → 增删行数（仅统计，不入库明细）

### 1.2 豁免与 fail-open（一张表，避免误伤）

| 条件 | 处理 | 理由 |
|---|---|---|
| `task.workspace` 为空 | **跳过** | 无工作区无从采集 |
| workspace 非 git 仓库 / git 不可用 | **跳过 + 标注**「无法采集 diff」 | 采集失败不得变成拦截 |
| `task.task_kind ∈ {idea_review, change_tracking}` | **跳过** | 评审/变更跟踪本无代码产出（`models.py:60-63`） |
| `task.diff_gate_exempt = True` | **跳过** | 用户显式豁免，可回溯（同 `doc_gate_exempt` 先例） |
| 存量任务（迁移前） | 自动置 `diff_gate_exempt=1` | 保住历史行为，与 `doc_gate_exempt` 迁移同策略 |
| 同一 workspace 同时有 ≥2 个活跃 run | **跳过 + 标注**「并发冲突」 | 快照互相污染，判据不可信 |

### 1.3 拦截判据（唯一一条）

```
success == True  且  未豁免  且  可采集  且  changed == False   →  422 run.empty_diff
```

### 1.4 拒绝次数上限（防无限重试）

- `Run.rejections` 计数；`MAX_REJECTIONS = 2`。
- `rejections < 2` → 422，**不消耗 attempt**，Agent 可在同一 run 内改完再提交。
- `rejections >= 2` → 本次执行判**失败**（`exit_code=1`、state=FINISHED、reason=`empty_diff_repeated`），交既有重试机制（`_handle_failure`，`api/runs.py:380`）→ 任务按 `max_retries` 走重试。
- 这样既不会无限循环，也不会把"被拦"混进"任务失败"口径（对接 P1-1）。

---

## 2. 后端改动清单

| # | 文件:位置 | 改什么 |
|---|---|---|
| B1 | `mio_taskhub/models.py:262-274` | `Run` 新增 3 字段：`diff_summary`(JSON dict)、`annotations`(JSON list)、`rejections: int = 0` |
| B2 | `mio_taskhub/models.py:127-179` | `Task` 新增 `diff_gate_exempt: bool = False`（紧邻 `doc_gate_exempt:162`） |
| B3 | `mio_taskhub/migrations.py` | 新增 `_migrate_run_diff(conn)` + `_migrate_task_diff_gate(conn)`，在 `run_migrations` 列表注册（`:16-35`）；幂等写法照抄 `_migrate_read_evidence`（`:64-74`）：先 `PRAGMA table_info` 查列再 `ALTER TABLE`；存量 task 全置 `diff_gate_exempt=1` |
| B4 | **新建** `mio_taskhub/diff_gate.py` | 纯函数模块（便于单测）：`snapshot(workspace)->dict` / `differs(before, after)->bool` / `collect(workspace)->dict` / `check_diff_gate(db, task, run, body)->Verdict`；subprocess 模式照抄 `ops/git_sync.py:38-50`（`creationflags=CREATE_NO_WINDOW`），但要 `cwd=workspace` 且**不吞异常**（要拿到失败原因） |
| B5 | `mio_taskhub/api/runs.py:87-100` `create_run` | best-effort 拍 before 快照写入 `run.diff_summary["before"]`；失败仅记 warning，不阻断 claim |
| B6 | `mio_taskhub/api/runs.py:271-318` `submit_result` | **插入变更门禁**（见 §3） |
| B7 | `mio_taskhub/api/tasks.py:218-225` | `editable` 列表加 `"diff_gate_exempt"`；`:231` 的 bool 白名单同步加上（否则传 `true` 会被原样赋值成字符串） |
| B8 | `mio_taskhub/api/runs.py` | 拦截时写 3 条记录（见 §4 可见性三条链路） |

### 2.1 环境开关（分阶段上线，降低误伤风险）

`MIO_DIFF_GATE` = `off` | `warn` | `block`，**默认 `warn`**（只标注不拦）。
- 先 `warn` 跑 3–5 个真实 run，确认不误伤 → 再切 `block`。
- 判定逻辑：`warn` 模式下命中判据只写标注 + 放行；`block` 模式才 422。
- 出问题立刻回 `off`（不需回滚代码、不需迁移）。

---

## 3. `submit_result` 插入点（精确到行）

现有顺序：`404`(`:275`) → `assert_run_owner`(`:280`) → `assert_run_submittable`(`:281`) → `success`(`:282`) → **ReadEvidence 门禁**(`:284-288`) → 写 run(`:289-293`) → `_handle_success/_handle_failure`(`:297-302`)

**新顺序**：把变更门禁插在 **`:288` 之后、`:289` 之前**。
- 读证门禁在前：它纯查 DB、更便宜也更基础，先报读证问题。
- 变更门禁在后：要跑 git，最贵，放最后。

**需配套的一处上移**：`:294-296` 的 `payload = {...}` / `m1_events = []` 初始化**要上移到门禁之前**（超限判失败时要复用 `_handle_failure`）。

代码骨架（伪代码，字段名以实际模型为准）：

```python
# ── 变更门禁（窄门禁 2026-10-20）────────────────────────────
if success and task is not None:
    verdict = check_diff_gate(db, task, run, body)      # diff_gate.py
    if verdict.blocked:
        run.rejections = (run.rejections or 0) + 1
        run.annotations = merge_annotations(run.annotations, [{
            "code": "run.empty_diff", "level": "block",
            "message": verdict.message, "at": _now().isoformat(),
        }])
        db.add(run)
        # 可见性三条链路（见 §4）—— 注意 entity 必须是 "task"
        emit_event(db, type="run.result_rejected", entity="task",
                   entity_id=task.id, run_id=run.id,
                   payload={"code": "run.empty_diff",
                            "message": verdict.message,
                            "rejections": run.rejections,
                            "max_rejections": MAX_REJECTIONS})
        db.add(HistoryEvent(task_id=task.id, type="rejected",
                            payload=json.dumps({...}, ensure_ascii=False)))
        db.commit()          # ★ 关键：先落库再抛异常（见 §5 坑位①）
        if run.rejections >= MAX_REJECTIONS:
            run.exit_code = 1
            run.state = RunState.FINISHED
            run.finished_at = _now()
            run.result = verdict.message
            _handle_failure(task, run, m1_events, payload)  # 走既有重试
            db.commit()
        raise HTTPException(422, detail={
            "code": "run.empty_diff",
            "message": "拒绝提交：声明 success=true，但本次 run 期间工作区零改动（0 文件 / 0 行）",
            "hint": "先在工作区实际完成改动（提交或保留改动均可）再 submit_result；"
                    "若本任务确无代码产出，用 PATCH /tasks/{id} 设置 diff_gate_exempt=true。",
            "files_changed": 0,
            "rejections": run.rejections,
            "max_rejections": MAX_REJECTIONS,
        })
```

> `detail` 用 `code` 键（与后端既有 `run.not_owner` / `run.result_already_submitted` 一致）；前端目前只读 `detail.error`，F1 顺手修。

---

## 4. 可见性三条链路（"我怎么看到"的答案）

| 链路 | 写哪张表 | 写入点 | 前端消费处 | 效果 |
|---|---|---|---|---|
| WS 通知 | `Event` | `emit_event(type="run.result_rejected", entity="task")` | `App.jsx:161-167` | 右上角**红色 toast** |
| 执行历史 | `HistoryEvent(type="rejected")` | 门禁内 `db.add` | `TaskDetail.jsx:582-589` → `HistRow` | 「动态」tab **红行** |
| 每次执行 | `Run.annotations` | 门禁内写 run | `TaskDetail.jsx:376-395` | **拦截卡片 / 提示行** |

- 「标注项」（如"未检测到测试执行"）只走第 3 条链路，**绝不弹 toast**（否则每次执行闪一次，噪声淹没信息）。
- 「拦截」三条全走（要打断注意力）。

---

## 5. 三个必须知道的坑（照抄实现会踩）

**坑位①：抛 422 前必须先 `db.commit()`**
`db.py:52-54` 的 `get_session` 是 `with Session(engine) as session: yield session`——退出时 **close 而非 commit**，未提交的事务会被丢弃。
若先 `emit_event` 再 `raise HTTPException`，**事件会随会话回滚消失，人永远看不到拦截**。
→ 门禁里必须 `db.commit()` 后再抛。

**坑位②：`entity` 必须是 `"task"`，不能用 `"run"`**
`events.py:43-48` 的 `MESSAGE_TYPES` 只有 task/idea/discussion/memory。`entity="run"` 会广播成 `event_update`，而前端只处理 `task_update` / `idea_update` / `memory_update` / `update_status`（`App.jsx:161-173`）→ **前端直接忽略，toast 不弹**。

**坑位③：别用 TaskEvent 承载拦截事件**
`TaskDetail.jsx:594` 用 `compositeLabel(ev.to_state, ev.to_stage)` 渲染时间线；`constants.js:66-68` 对 `null,null` 会渲染出 `null·null`。
→ 拦截事件走 **`HistoryEvent`**（复用现成 `HistRow`，只加 1 行 `HIST_TYPE_META`），别塞 TaskEvent。

---

## 6. 前端改动清单

| # | 文件:位置 | 改什么 |
|---|---|---|
| F1 | `web/src/api.js:27-30` | `ApiError` 构造器补 `if (detail.code) this.error_code = detail.code`（现只读 `detail.error`，而后端一律用 `code` → **现有 409 门禁码前端根本拿不到，这是既有缺陷，顺手修**） |
| F2 | `web/src/api.js:43` | msg 构造优先取 `detail.message`：`detail?.message \|\| detail?.detail \|\| detail?.error \|\| JSON.stringify(detail)`（否则整个 detail 被 JSON 化成一行乱码） |
| F3 | `web/src/api.js:11` | 422 通用文案保留作兜底（无 `detail.message` 时才用） |
| F4 | `web/src/App.jsx:164-167` | 加分支 `else if (ev.type === 'run.result_rejected') addToast('「'+ev.entity_id+'」提交被拒：声明成功但无代码改动', 'danger')` |
| F5 | `web/src/components/TaskDetail.jsx:40-57` | `HIST_TYPE_META` 加 `rejected: { label: '已拦截', tone: 'danger' }` |
| F6 | `web/src/components/TaskDetail.jsx:376-395` | 「完成报告」渲染 `run.annotations`：`level==='block'` → 红框「已拦截」卡（原因 + 下一步）；`level==='warn'` → 琥珀「提示」行，**必须写明"不影响放行"** |
| F7 | 样式 | 复用现有 `.run-report` / `.errorbar`（`index.css` 已有），**不新增 CSS 文件** |

---

## 7. 实施批次与验收

### 批次 0 · 流程前置（不做则 push 被拦）
- 在 taskhub 建任务 → `scaffold_docs` → 写 `requirement` / `spec` / `api` / `plan` → 推进到 `approved`（`LIFECYCLE_GATE` 与 pre-push 钩子都会查）。
- 分支命名 `task-<8位hex>`。

### 批次 A · 后端（B1–B8 + 迁移）
- 验收：`MIO_DIFF_GATE=block` 下，构造"success + 零改动" → 422 且 **`Event` 表有 `run.result_rejected` 记录**（查库确认落库，证明坑位①已避开）；有改动 → 200；`diff_gate_exempt=true` → 200；非 git 目录 → 200 + 标注。

### 批次 B · 前端（F1–F7）
- 验收：被拦时右上角弹红色 toast；任务详情「动态」tab 出现红色「已拦截」行；「完成报告」该次执行显示红框拦截卡；标注只出现在完成报告、**不弹 toast**。

### 批次 C · 测试
新增 `tests/test_run_diff_gate.py`，至少覆盖：
1. success + 零改动 → 422
2. success + 有改动（未 commit）→ 200
3. success + 已 commit（HEAD 变）→ 200
4. `diff_gate_exempt=true` → 200
5. `task_kind=idea_review` → 200
6. workspace 非 git 目录 → 200 + 标注
7. 被拒后 agent 实际改动再提交 → 200（同一 run 内可重试）
8. 连续拒绝达上限 → run 判失败、任务进入重试
9. 迁移幂等：老库启动后新增列存在、存量任务 `diff_gate_exempt=1`
10. 拦截事件已落库（对应坑位①）

**跑测纪律**（踩过坑）：全量跑加 `--basetemp=<独立目录>`（否则 pytest 清理触发 safe-delete 批量删除保护，进程被杀拿不到汇总）；并行跑用 `MIO_TEST_DB_PATH=<不同路径>` 隔离；失败名单落盘再读 `pytest --lf -q -rf > out.txt`。

### 批次 D · 灰度与观察
- `MIO_DIFF_GATE=warn` 跑 3–5 个真实 run → 确认零误伤 → 切 `block` → 观察一个真实周期 → 用决策 doc §1 验收第 4 条（"能指着一例拦下过的假完成"）判断成败。

---

## 8. 风险与回退

| 风险 | 表现 | 处置 |
|---|---|---|
| 误伤合法完成 | 频繁 422、任务推不动 | `MIO_DIFF_GATE=off` 立即回退（不删代码、不需迁移）；再按"只拦明确假完成"重新定判据 |
| 采集失败被当拦截 | 提交全被拒 | 判据已设计为 fail-open；`collect()` 抛错 → 跳过 + 标注，不拦 |
| Agent 无限重试 | 反复提交 | `MAX_REJECTIONS=2` 超限判 run 失败 |
| Agent 提交了改动 | 门户前就脏 | before/after 双快照已覆盖（§1.1） |
| 并发 run 污染快照 | 误判有改动 | 同一 workspace 多个活跃 run → 跳过 + 标注 |
| 事件没广播 | 人看不到拦截 | 坑位①②③ 三条硬约束（§5），验收时**查库确认**而不是只看界面 |

---

## 9. 与既有缺陷的关系（一次改动多收口）

| 既有问题 | 本方案是否收口 |
|---|---|
| P1-3 验收空心化（无变更识别、采信自报 success） | **部分收口**：本次 run 的改动由服务端自行采集（不采信自报），自报 success 仍采信但被空 diff 拦截兜住 |
| P1-2 自审绕过 | 无关（另一项） |
| P1-1 口径失真 | 无关，但 §1.4 特意不把"被拦"混进"失败"口径 |
| 前端拿不到后端错误码 | **顺带修复**（F1） |

---

## 10. 后续独立立项（本期不做）

- **测试执行器**：真跑 pytest 需要解决"跑哪个测试、怎么隔离工作区、耗时上限、失败如何归因"——独立任务，且其结果**只做标注不拦**（按 §0 总原则）。
- **P1-1 口径失真**、**P1-2 自审绕过**：决策 doc §4 前置止血，本方案不依赖它们，可并行推进。
