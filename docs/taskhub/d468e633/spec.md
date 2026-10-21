# 模块设计 · 窄门禁（空 diff 拦截）

## 文档信息

| 项 | 内容 |
|---|---|
| 文档版本 | v1.1 |
| 状态 | approved |
| 最后更新 | 2026-10-20 16:32 |
| 负责人 | 项目作者（单人） |
| 关联任务 | d468e633 |
| 上游需求 | `docs/taskhub/d468e633/requirement.md`（FR-1 ~ FR-11） |
| 接口契约 | `docs/taskhub/d468e633/api.md` |
| 实施方案 | `docs/taskhub/narrow-gate-implementation-plan-20261020.md` |

## 模块职责与边界

### 做什么

- 新增一个**只读采集 + 纯判定**的模块 `mio_taskhub/diff_gate.py`：给定「前快照」与「后快照」，输出「本次是否有改动」与「为什么不能判定」。
- 在 `submit_result` 成功路径上接入一个**前置门禁**：命中即 422 拒绝，不改 run 终态、不消耗 attempt。
- 把判定结果与标注**落三处**（WebSocket 事件 / 执行历史 / Run 标注），使人能看到。
- 前端在**既有界面**（TaskDetail 抽屉）内渲染拦截卡片与标注行，不新增面板。

### 不做什么

- 不跑测试、不做任何 CI 集成（需求 N-1）。
- 不存 diff 全文，不存 patch（需求 N-2）。
- 不新增任务状态、不改合法转换表（需求 N-4）。
- 不改 Agent 调用协议（需求 N-5）。
- 不在本模块内做「是否值得拦」的产品判断——该判断由环境开关与豁免规则承担，模块只负责事实判定。

### 边界归属

| 关注点 | 归属 | 理由 |
|---|---|---|
| 「本次是否有改动」的事实判定 | `diff_gate.py`（纯函数 + 只读采集） | 可单测、与产品策略解耦 |
| 「该不该拦」的策略 | `diff_gate.py` 的豁免规则 + `MIO_DIFF_GATE` | 策略可一键回退，事实不可 |
| 拒绝后的状态处理 | 既有 `api/runs.py` 状态机 | 不新增状态语义 |
| 界面呈现 | `web/src/components/TaskDetail.jsx` | 复用既有抽屉 |

## 输入 / 输出

### 模块对外接口

| 函数 | 输入 | 输出 | 副作用 |
|---|---|---|---|
| `snapshot(workspace: str) -> Snapshot` | 工作区路径 | `{head, porcelain, files, insertions, deletions, ok, error}` | 只读 git 调用 |
| `differs(before: Snapshot, after: Snapshot) -> bool` | 前后快照 | 是否发生变化 | 无 |
| `check_diff_gate(db, task, run, body) -> Verdict` | 任务、Run、提交体 | `{blocked, skipped, reason, message, files_changed, ...}` | 读库（查并发活跃 run） |
| `merge_annotations(existing, new_items) -> list` | 既有标注、新标注 | 合并后的标注列表 | 无 |

### 数据结构

```python
# Snapshot：一次采集的原始结果
{
  "ok": bool,            # 采集是否成功（失败即 fail-open）
  "error": str,          # 失败原因（git 不存在 / 非仓库 / 超时）
  "head": str,           # git rev-parse HEAD 的 sha（空串 = 无 commit）
  "porcelain": str,      # git status --porcelain 全量输出
  "files": int,          # porcelain 行数（变更文件数）
  "insertions": int,     # git diff --numstat 的新增行合计
  "deletions": int,      # git diff --numstat 的删除行合计
  "paths": [str],        # 变更文件路径列表（上限 50 条）
  "captured_at": str,    # 采集时刻 ISO
}

# Verdict：判定结论
{
  "blocked": bool,       # 是否应拒绝
  "skipped": bool,       # 是否因豁免/无法采集而跳过
  "reason": str,         # 机器可读原因码
  "message": str,        # 人可读文案（三段式）
  "files_changed": int,
  "insertions": int,
  "deletions": int,
  "mode": str,           # 实际生效的开关值 off/warn/block
}
```

### 落库字段

| 表 | 字段 | 类型 | 说明 |
|---|---|---|---|
| `run` | `diff_summary` | JSON | `{before: Snapshot, after: Snapshot, changed: bool, reason: str}` |
| `run` | `annotations` | JSON | 列表，每项 `{code, level, message, at}`；`level ∈ {block, warn}` |
| `run` | `rejections` | int | 本 run 累计被拒次数，默认 0 |
| `task` | `diff_gate_exempt` | bool | 显式豁免开关，默认 false；迁移把存量置 true |

## 详细设计

### 一、采集层

```python
# mio_taskhub/diff_gate.py（新建）
GIT_TIMEOUT_SEC = 5
MAX_PATHS = 50

def _git(workspace: str, *args: str) -> tuple[int, str, str]:
    """只读执行 git。返回 (returncode, stdout, stderr)。超时抛 TimeoutError。"""
    # 参照 ops/git_sync.py:38-50 的 subprocess 模式：
    #   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows 下不弹黑窗
    # 关键差异：必须 cwd=workspace 且 timeout=5s，且**不吞异常**（要拿到失败原因做标注）

def snapshot(workspace: str) -> Snapshot:
    """采集一次快照。任何异常都转成 ok=False + error 原因，绝不向上抛。"""
    if not workspace or not os.path.isdir(workspace):
        return Snapshot(ok=False, error="workspace 不存在或为空")
    try:
        rc_head, head, _ = _git(workspace, "rev-parse", "HEAD")
        rc_st, status, _ = _git(workspace, "status", "--porcelain")
        rc_num, numstat, _ = _git(workspace, "diff", "--numstat", "HEAD")
    except subprocess.TimeoutExpired:
        return Snapshot(ok=False, error="git 调用超时")
    except FileNotFoundError:
        return Snapshot(ok=False, error="git 不可用")
    ...
```

采集要点：

- 只用**只读**子命令：`rev-parse` / `status --porcelain` / `diff --numstat`。
- `rev-parse HEAD` 在**空仓库**（尚无 commit）会返回非零 → 此时 `head` 记空串，不算采集失败（空仓库 + 有未跟踪文件也算「有改动」）。
- `status --porcelain` 会同时列出**未跟踪文件**（`??` 前缀）——这正是「Agent 新建了文件但没提交」的场景，必须计入。
- `diff --numstat HEAD` 在无 HEAD 时会失败 → 降级为 0 行，不影响判定（porcelain 已能判定）。

### 二、判定层

```python
def differs(before: Snapshot, after: Snapshot) -> bool:
    if not (before.ok and after.ok):
        return True          # 采集不完整 → 不判定为「无改动」（fail-open 方向）
    if before.head != after.head:
        return True          # 有新 commit
    if before.porcelain != after.porcelain:
        return True          # 工作区改动有变化
    return (after.insertions + after.deletions) > 0
```

`differs` 采不到信息时返回 `True`（即「视为有改动」），是**故意的**：宁可漏放假完成，也不能把「采集失败」变成「拦截成功」。

### 三、豁免与策略层

```python
EXEMPT_TASK_KINDS = {TaskKind.REVIEW, TaskKind.CHANGE_TRACKING}
GATE_MODES = ("off", "warn", "block")

def gate_mode() -> str:
    return os.environ.get("MIO_DIFF_GATE", "warn").strip().lower() or "warn"

def check_diff_gate(db, task, run, body) -> Verdict:
    mode = gate_mode()
    if mode == "off":
        return Verdict(blocked=False, skipped=True, reason="gate_off", ...)
    if task is None:
        return Verdict(blocked=False, skipped=True, reason="no_task", ...)
    if not task.workspace:
        return Verdict(blocked=False, skipped=True, reason="no_workspace", ...)
    if task.diff_gate_exempt:
        return Verdict(blocked=False, skipped=True, reason="task_exempt", ...)
    if task.task_kind in EXEMPT_TASK_KINDS:
        return Verdict(blocked=False, skipped=True, reason="task_kind_exempt", ...)
    if _has_concurrent_run(db, task):          # 同 workspace 另一个活跃 run
        return Verdict(blocked=False, skipped=True, reason="concurrent_run", ...)
    after = snapshot(task.workspace)
    if not after.ok:
        return Verdict(blocked=False, skipped=True, reason="collect_failed", message=after.error)
    before = _before_snapshot(run)             # 取自 run.diff_summary["before"]
    if before is None:
        return Verdict(blocked=False, skipped=True, reason="no_before_snapshot", ...)
    changed = differs(before, after)
    if changed:
        return Verdict(blocked=False, skipped=False, reason="has_changes", ...)
    if mode == "warn":
        return Verdict(blocked=False, skipped=False, reason="empty_diff_warn", ...)
    return Verdict(blocked=True, skipped=False, reason="empty_diff",
                   message="拒绝提交：声明 success=true，但本次 run 期间工作区零改动（0 文件 / 0 行）")
```

**豁免优先级**：`off` 开关 > 无 task > 无 workspace > 显式豁免 > task_kind > 并发冲突 > 采集失败 > 无前快照。任一命中即跳过，**并写明 reason**，事后能回答「为何没拦」。

**并发判定**：同一 `workspace` 下存在另一个 `state ∈ {claimed, running}` 的 run → 跳过。原因是两个 run 的前后快照会互相污染，判定不可信。

### 四、快照时机

| 时机 | 位置 | 动作 | 失败处理 |
|---|---|---|---|
| run 创建 | `api/runs.py:87-100` `create_run` | 采集前快照写入 `run.diff_summary["before"]` | 仅记 warning，不阻断 claim |
| submit | `api/runs.py:271-318` | 采集后快照 | 采集失败 → 跳过判定 + 标注 |

前快照**必须在 run 创建时拍**：若等到 submit 才拍，无法区分「本次 run 的改动」与「run 开始前工作区就有的脏」。

### 五、门禁接入点与顺序

`submit_result` 现有顺序：`404` → `assert_run_owner` → `assert_run_submittable` → `success` → **ReadEvidence 门控** → 写 run → 状态流转。

变更门禁**插在 ReadEvidence 之后、写 run 之前**（读证门禁纯查库、更便宜且更基础，先报读证问题）。

```python
    # 变更门禁（窄门禁）
    if success and task is not None:
        verdict = check_diff_gate(db, task, run, body)
        run.diff_summary = _merge_diff_summary(run.diff_summary, verdict)
        if verdict.blocked:
            run.rejections = (run.rejections or 0) + 1
            run.annotations = merge_annotations(run.annotations, [{
                "code": "run.empty_diff", "level": "block",
                "message": verdict.message, "at": _now().isoformat(),
            }])
            db.add(run)
            # 可见性：① WS 事件（entity 必须是 task）② 执行历史 ③ run 标注（上面已写）
            emit_event(db, type="run.result_rejected", entity="task",
                       entity_id=task.id, run_id=run.id,
                       payload={"code": "run.empty_diff", "message": verdict.message,
                                "rejections": run.rejections, "max_rejections": MAX_REJECTIONS})
            db.add(HistoryEvent(task_id=task.id, type="rejected",
                                payload=json.dumps({"message": verdict.message,
                                                    "rejections": run.rejections},
                                                   ensure_ascii=False)))
            db.commit()          # ★ 必须先落库再抛：get_session 退出不 commit
            if run.rejections >= MAX_REJECTIONS:
                run.exit_code = 1
                run.state = RunState.FINISHED
                run.finished_at = _now()
                run.result = verdict.message
                _handle_failure(task, run, m1_events, payload)   # 走既有重试
                db.commit()
            raise HTTPException(422, detail={
                "code": "run.empty_diff",
                "message": verdict.message,
                "hint": "先在工作区实际完成改动（提交或保留改动均可）再 submit_result；"
                        "若本任务确无代码产出，用 PATCH /tasks/{id} 设置 diff_gate_exempt=true。",
                "files_changed": 0,
                "rejections": run.rejections,
                "max_rejections": MAX_REJECTIONS,
            })
        elif verdict.skipped or verdict.reason == "empty_diff_warn":
            run.annotations = merge_annotations(run.annotations, [{
                "code": verdict.reason, "level": "warn",
                "message": verdict.message or _skip_message(verdict),
                "at": _now().isoformat(),
            }])
```

配套调整：`api/runs.py:294-296` 的 `payload` / `m1_events` 初始化**上移到门禁之前**（超限判失败时要复用 `_handle_failure`）。

### 六、可见性三链路

| 链路 | 写什么 | 前端消费 | 效果 |
|---|---|---|---|
| WS 通知 | `Event(type="run.result_rejected", entity="task")` | `App.jsx:161-167` | 右上角红色 toast |
| 执行历史 | `HistoryEvent(type="rejected")` | `TaskDetail.jsx:582-589` → `HistRow` | 「动态」tab 红色「已拦截」行 |
| 每次执行 | `Run.annotations` | `TaskDetail.jsx:376-395` | 完成报告里的拦截卡片 / 标注行 |

三个**必踩的坑**（实现时逐条对照）：

1. `db.py:52-54` 的 `get_session` 退出时只 close 不 commit → **抛 422 前必须先 `db.commit()`**。
2. `events.py:43-48` 的 `MESSAGE_TYPES` 不含 `run` → 事件必须 `entity="task"`，否则广播成 `event_update`，前端只处理 `task_update`/`idea_update`/`memory_update`/`update_status`（`App.jsx:161-173`）→ **toast 不弹**。
3. 拦截事件**不要**塞 `TaskEvent`：`TaskDetail.jsx:594` 走 `compositeLabel(ev.to_state, ev.to_stage)`，`constants.js:66-68` 对 `null,null` 会渲染成 `null·null` → 改走 `HistoryEvent`。

### 七、前端接线

| 位置 | 改动 | 目的 |
|---|---|---|
| `web/src/api.js:27-30` | `ApiError` 构造器补 `if (detail.code) this.error_code = detail.code` | 后端一律用 `code`，前端此前只读 `detail.error` → 现有 409 门禁码前端根本拿不到（顺带修既有缺陷） |
| `web/src/api.js:43` | msg 构造优先取 `detail.message` | 否则整个 detail 被 JSON.stringify 成一行乱码 |
| `web/src/App.jsx:164-167` | 加 `ev.type === 'run.result_rejected'` 分支 → `addToast(..., 'danger')` | 拦截的打断式通知 |
| `web/src/components/TaskDetail.jsx:40-57` | `HIST_TYPE_META` 加 `rejected: { label: '已拦截', tone: 'danger' }` | 执行历史红行 |
| `web/src/components/TaskDetail.jsx:376-395` | 渲染 `run.annotations`：`block` → 红框拦截卡；`warn` → 琥珀提示行（文案须含「不影响放行」） | 归档与常驻提示 |
| `web/src/index.css` | 复用既有 `.run-report` / `.errorbar` | 不新增 CSS 文件 |

## 异常与边界情况

| 场景 | 处理 | 是否拦截 |
|---|---|---|
| `git` 未安装 / 不在 PATH | `FileNotFoundError` → `ok=False` | 否（标注「git 不可用」） |
| workspace 不是 git 仓库 | `rev-parse` 非零退出 | 否（标注「非 git 仓库」） |
| 空仓库（无任何 commit） | `head` 记空串，porcelain 仍可用 | 按 porcelain 判定 |
| git 调用超时（>5s） | `TimeoutExpired` → `ok=False` | 否（标注「采集超时」） |
| 提交前工作区已脏 | 前快照在 run 创建时拍 → 不会被算成本次改动 | 正常判定 |
| Agent 已 commit 改动 | `head` 变化 → 算有改动 | 正常放行 |
| Agent 新建未跟踪文件 | `porcelain` 含 `??` 行 → 算有改动 | 正常放行 |
| 同一 workspace 两个活跃 run | 跳过判定 | 否（标注「并发冲突」） |
| 连续被拒 3 次 | 达到 `MAX_REJECTIONS` → 判 run 失败并交既有重试 | 第 3 次起不再 422，转失败流程 |
| Agent 一直不重试 | run 停在 running，被看门狗超时回收 | 走既有超时路径，不新增语义 |
| 判定为「无改动」但 Agent 确实做了文档类改动 | 文档类任务应置 `diff_gate_exempt=true` | 否（豁免后跳过） |

## 涉及文件清单

| 文件 | 动作 | 说明 |
|---|---|---|
| `mio_taskhub/diff_gate.py` | 新增 | 采集 + 判定 + 豁免，纯函数为主 |
| `mio_taskhub/models.py` | 改 | `Run` 加 3 字段；`Task` 加 `diff_gate_exempt` |
| `mio_taskhub/migrations.py` | 改 | `_migrate_run_diff` + `_migrate_task_diff_gate`，幂等，存量置豁免 |
| `mio_taskhub/api/runs.py` | 改 | `create_run` 拍前快照；`submit_result` 插门禁 |
| `mio_taskhub/api/tasks.py` | 改 | `editable` 与 bool 白名单加 `diff_gate_exempt`；`_task_detail` 的 `runs[]` 增补 3 字段 |
| `web/src/api.js` | 改 | 错误码读取 + message 优先 |
| `web/src/App.jsx` | 改 | 拦截 toast 分支 |
| `web/src/components/TaskDetail.jsx` | 改 | 拦截卡片 + 标注行 + `HIST_TYPE_META` |
| `tests/test_run_diff_gate.py` | 新增 | 10 条用例，见 `test` 文档 |
