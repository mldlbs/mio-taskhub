# 🗓️ 想法落地闭环 P4 实现计划（验收清单 19 项核对）

## 文档信息

| 项 | 值 |
|---|---|
| 任务 | eb248b6a（branch task-eb248b6a） |
| 需求 | docs/taskhub/eb248b6a/requirement.md（FR-30~FR-33） |
| 设计 | docs/taskhub/design-idea-landing.md（v1.3） |
| 策略 | 只读核对优先，缺陷才动代码；叠分支 task-d4bdf530 之上 |

## 步骤

| # | 步骤 | 产出 | 关联 FR |
|---|---|---|---|
| ① | 建分支 `task-eb248b6a`（基于 task-d4bdf530 当前 tip 36bfe31）+ 登记 requirement/plan 并批准 | 文档链就绪 | FR-30 |
| ② | **测试覆盖类项**：把 19 项中可由用例证明的逐项映射到测试名，单跑这些用例确认绿（`.venv\Scripts\python.exe -m pytest <names> -q`） | 项→测试名映射表 | FR-30 |
| ③ | **活体类项**：对无测试可证的项做真实调用/操作——hub `127.0.0.1:48620` curl（PATCH 并发、行动项 convert 幂等、dismiss 复活、mode=review 422 等）、前端行为核验（构建产物/代码断言） | 活体响应片段 | FR-30 |
| ④ | 汇总 19 行证据表写入 `docs/taskhub/acceptance-audit-p4.md`；有缺陷 → 修复+补用例后重验该项 | 核对报告 | FR-31/33 |
| ⑤ | 全量回归 `.venv\Scripts\python.exe -m pytest -q`（后台日志法，≥924+1）+ `npm run build` | 回归证据 | FR-33 |
| ⑥ | 勾选设计稿 19 项复选框 + 清单尾附报告链接 | 设计稿更新 | FR-32 |
| ⑦ | 提交（报告/勾选/缺陷修复分批）、push `task-eb248b6a`、submit、done | 闭环 | FR-32 |

## 证据映射（预填，执行中校正）

| 清单项（简） | 预期证据 |
|---|---|
| 旧 idea NULL 字段无异常 | 测试：NULL 归一化系列（test_ideas_api 等）+ 活体 GET idea |
| PATCH 新字段进 IdeaChange diff | 测试：diff 键断言用例 |
| Mio 超时仅假设区灰显 | 测试：hypotheses degraded 用例 |
| 断链灰显+可解除 | 测试：broken 用例 |
| 并发 PATCH 无丢更新 | 测试：test_patch_assumption_concurrent_no_lost_update |
| 行动项转任务幂等 | 测试：convert 幂等用例 |
| mode=review 条目不足 422 | 测试：五段门控 422 用例 |
| 转任务含验收标准 | 测试：convert 断言 |
| dismiss 复活（服务端） | 测试：dismiss 复活用例 |
| 优先级序生效 | 测试：next_action 优先级用例 |
| 高风险默认勾红队 | 测试：词表命中默认值用例 |
| 评审快照 roles+prompt | 测试：snapshot 隔离用例 |
| 任务图有环降级 / >20 折叠 | 测试：P0 图用例 |
| 任务图多层闭包/cycles/truncated | 测试：test_idea_topology_p3 用例 |
| 复盘区 summary+明细+三计数 | 测试：test_idea_retrospective_p3 用例 |
| 关联表迁移+双写+逐键不变 | 测试：test_assumption_links_p3 用例 |
| mode=free 行为回归 | 测试：free 模式回归用例 |
| MCP open_discussion 透传 mode/roles | 测试：MCP 透传用例 |
| 全量 pytest 绿 | 全量日志 |

## 测试命令

```powershell
.venv\Scripts\python.exe -m pytest -q --tb=short tests\<names>   # 步骤②定点
# 全量（后台落盘）：Start-Process -FilePath ".venv\Scripts\python.exe" -ArgumentList @("-m","pytest","-q","--tb=short") -RedirectStandardOutput $env:TEMP\p4_full_pytest.log -NoNewWindow -PassThru
```

## 风险与对策

| 风险 | 对策 |
|---|---|
| 项无法找到测试覆盖 | 归入活体类实测；再缺则视为缺陷按 FR-33 处置 |
| 活体 hub 非 build19 | 只读接口行为一致即可，记录实际构建号 |
| 勾选 diff 触发 FR 门 | requirement 已枚举 FR-1~29，勾选行引用的 FR-26/27/28 已覆盖 |
| 两实例抢测试库 | 全量回归串行、用既有后台日志法 |
