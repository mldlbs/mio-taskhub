# E1 回溯审计报告（FR-01）v2

| 字段 | 值 |
|---|---|
| 窗口起止（UTC） | 2026-09-05T01:24:30.492084+00:00 ~ 2026-11-04T01:24:30.492084+00:00 |
| 窗口天数 | 60 |
| 样本总数（success Run，60 天） | 173 |
| 审计范围内（有 workspace 或 deliverables） | 73 |
| out_of_scope（空 workspace 无 deliverables，或 [insight] 自动洞察消费类） | 100 |
| 严格口径可判定数（has_change + no_change） | 17 |
| 严格口径 no_change 数 / 命中率 | 0 / 0.0% |
| 宽口径可判定数（含 no_change_weak） | 56 |
| 宽口径 no_change 数 / 命中率 | 39 / 69.6% |
| ambiguous（窄窗有 commit 无法归因） | 0 |
| unknown（证据缺失） | 17 |
| 扩窗次数 | 0 |
| 生成时间 | 2026-11-04T01:24:30.492084+00:00 |
| 需求引用 | FR-01（任务 c8316737 requirement v1.0） |
| 只读声明 | sqlite 以 mode=ro 打开，无任何写操作；git 仅 log/rev-parse |

## 判定标签定义

- has_change：diff_summary.changed=true / 任务分支或 message 含 task_id 的 commit（宽窗 72h）/ deliverables 存在 / doc_paths·deliverables 文件 mtime 落在 [start-15min, finish+4h]，任一成立。
- no_change（严格）：无任何 has_change 证据，且 ≥1 强 no_change 源（diff_summary.changed=false 或 deliverables 缺失）+ git 仓内无任务相关 commit。
- no_change_weak（宽口径）：仅凭 git——宽窗无任务相关 commit 且窄窗零 commit，且文档不落窗或无登记产出。
- ambiguous：窄窗 [start-15min, finish+30min] 有任意 commit 但无法归因到本任务（agent 直接在主分支工作且 message 不含 task_id 的情形）。
- unknown：非 git 仓 / workspace 无效且无 A/C 证据。
- out_of_scope：空 workspace 且无 deliverables，或 [insight] 自动洞察消费类任务（本就无代码交付语义）。

## 结论（三选一）

- 严格口径：**<5%：问题不成立（A-2 证伪），触发分支 C：整体暂停**
- 宽口径（含弱证据，供对照，不作正式裁决依据）：**≥15%：问题成立（A-2 证实），触发分支 A：锁定自用**

## 判定附注（供触发点裁决书引用，不得省略）

1. 弱口径 no_change 的构成以「任务未建 task-<id> 分支、commit message 不含 task_id、未登记 doc_paths/deliverables」为主——这是**归因证据缺失**，不等于「没干活」。
2. 定性反证：no_change_weak 样本中的任务标题（拓扑可用性补强、空闲计划可用化、定时任务页重构、action_config 乱码修复等）与当前产品中实际存在且运行的功能吻合——改动大概率真实发生，只是 commit 无任务标记。
3. 主信号：**「Agent 自报 success 但实际未做」在强证据可判定样本中发生率为 0/13~16；而无可归因证据的成功 Run 占比约 60-78%**——真实痛点是「结果可追溯性缺失」（对应假设 A-10 可读性方向），不是「Agent 造假」（A-2 问B 方向）。

## 逐条明细（审计范围内）

| run | task | 任务状态 | agent | started (UTC) | finished (UTC) | workspace | A diff | 任务commit | 窄窗commit | 分支 | C 产物 | E 文档落窗 | 判定 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 6477f680 | c5bb23fd | COMPLETED | codex | 2026-09-06 12:00:58 | 2026-09-06 12:06:26 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| 7c151b52 | 1144ca80 | COMPLETED | codex | 2026-09-06 12:08:14 | 2026-09-06 12:12:56 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| 6d19f972 | 9a7882ce | COMPLETED | codex | 2026-09-06 12:13:55 | 2026-09-06 12:17:15 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| 08cafefa | 5a76a334 | COMPLETED | codex | 2026-09-06 12:23:22 | 2026-09-06 12:24:42 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| 79cf0f5a | 1e4facfd | COMPLETED | codex | 2026-09-06 12:32:37 | 2026-09-06 12:35:26 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| f510ac23 | ab4d216b | COMPLETED | codex | 2026-09-06 12:38:15 | 2026-09-06 12:39:17 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| d8be800f | 789719c0 | COMPLETED | codex | 2026-09-06 12:41:33 | 2026-09-06 12:45:00 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| 0384a162 | 34e87d9d | COMPLETED | codex | 2026-09-06 13:00:30 | 2026-09-06 13:00:44 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| c905fdc8 | 3ff0397a | COMPLETED | codex | 2026-09-06 13:24:02 | 2026-09-06 13:26:01 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| 25c817e4 | 4b06f7b4 | COMPLETED | codex | 2026-09-06 13:36:08 | 2026-09-06 13:37:45 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| f1ebeaf2 | c37c5486 | COMPLETED | codex | 2026-09-06 13:41:44 | 2026-09-06 13:43:54 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| 61cb9bfa | 1b5ae71d | COMPLETED | codex | 2026-09-06 13:46:14 | 2026-09-06 13:55:27 | /e/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| 8b355b13 | 3755be4b | QUEUED | hermes | 2026-09-07 07:24:07 | 2026-09-07 07:31:03 | E:/work/code/csps-app-fxyj/s… | - | 0 | 0 | - | - | has_change | has_change |
| 5b1ffd10 | 25ad3b81 | CANCELLED | re-smoke | 2026-09-22 03:11:17 | 2026-09-22 03:11:26 | C:\Users\admin\AppData\Local… | - | 0 | 0 | - | - | - | unknown |
| a9438a7b | 2fe56e6a | COMPLETED | opencode | 2026-09-27 05:31:12 | 2026-09-27 06:23:44 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | has_change | - | has_change |
| 11380d08 | 2fe56e6a | COMPLETED | opencode | 2026-09-27 06:26:28 | 2026-09-27 06:27:38 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | has_change | - | has_change |
| ece48036 | 8442e38d | COMPLETED | opencode | 2026-09-27 10:02:26 | 2026-09-27 11:01:08 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 01125bf6 | 286dd112 | COMPLETED | opencode | 2026-09-27 11:16:14 | 2026-09-27 12:58:52 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 448553cc | d4bdf530 | COMPLETED | opencode | 2026-09-27 13:54:18 | 2026-09-27 14:34:22 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 3060c267 | eb248b6a | COMPLETED | opencode | 2026-09-27 14:48:18 | 2026-09-27 15:49:24 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 5f960b5a | 87de55ae | COMPLETED | opencode | 2026-09-27 16:42:49 | 2026-09-27 17:26:08 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | has_change | - | has_change |
| bf1ab523 | 9322ef16 | COMPLETED | opencode | 2026-09-29 01:50:07 | 2026-09-29 01:55:29 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| f7880f3e | fe156b05 | COMPLETED | opencode | 2026-09-29 03:22:00 | 2026-09-29 05:37:44 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| d97bc187 | 6eecdad8 | COMPLETED | opencode | 2026-09-29 07:35:55 | 2026-09-29 07:57:49 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 57bfa290 | 6d7abd00 | COMPLETED | opencode | 2026-09-29 07:58:06 | 2026-09-29 07:58:22 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| e5485ed0 | d4671b14 | COMPLETED | opencode | 2026-09-29 08:14:40 | 2026-09-29 08:20:29 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 096d3ad9 | cf655a70 | COMPLETED | opencode | 2026-09-29 08:39:23 | 2026-09-29 08:39:39 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 26c8a0d9 | 7a10b3bb | COMPLETED | opencode | 2026-09-29 09:16:53 | 2026-09-29 09:19:52 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 996035e2 | 86e7cda0 | COMPLETED | opencode | 2026-09-29 09:24:04 | 2026-09-29 09:35:26 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 57efc182 | dd48a647 | COMPLETED | opencode | 2026-09-30 01:49:53 | 2026-09-30 01:52:13 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 1180d74e | 883ebd09 | COMPLETED | opencode | 2026-09-30 02:17:27 | 2026-09-30 02:17:50 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| a2f16d97 | 7d5bac28 | COMPLETED | opencode | 2026-09-30 02:39:37 | 2026-09-30 02:48:06 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| b4e65a61 | 299f1ad3 | COMPLETED | opencode | 2026-09-30 03:29:56 | 2026-09-30 03:30:13 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 919e07e6 | 0df294e8 | COMPLETED | opencode | 2026-09-30 03:53:59 | 2026-09-30 03:56:45 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | has_change | - | has_change |
| 7d320396 | 18c2c6e9 | COMPLETED | opencode | 2026-09-30 07:05:52 | 2026-09-30 07:21:48 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| c7c794bc | 0712a33e | COMPLETED | opencode | 2026-09-30 07:58:58 | 2026-09-30 08:16:37 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| e8b3d0a6 | d3886693 | COMPLETED | opencode | 2026-09-30 08:21:17 | 2026-09-30 08:31:32 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| f910201f | c32a2145 | COMPLETED | opencode | 2026-09-30 08:31:52 | 2026-09-30 08:46:02 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| cc0b82c9 | f72fde6e | COMPLETED | opencode | 2026-09-30 09:17:45 | 2026-09-30 09:33:26 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| ba02dd14 | b71206fe | COMPLETED | opencode | 2026-09-30 10:04:39 | 2026-09-30 10:14:36 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 55dfaf0e | 3b5128b6 | COMPLETED | opencode | 2026-09-30 10:30:46 | 2026-09-30 10:50:17 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| c2804180 | 5d4c8b68 | COMPLETED | opencode | 2026-09-30 13:36:10 | 2026-09-30 13:48:59 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 192ec988 | 0fe91462 | COMPLETED | opencode | 2026-09-30 14:23:36 | 2026-09-30 14:51:19 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 1d2274c0 | b7a55b74 | COMPLETED | opencode | 2026-09-30 15:49:22 | 2026-09-30 16:01:17 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 9139a0cf | 7d7ff97a | COMPLETED | opencode | 2026-09-30 16:03:22 | 2026-09-30 23:41:30 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 75453d73 | aa1c3f4d | COMPLETED | opencode | 2026-10-01 03:02:07 | 2026-10-01 03:04:29 | E:\work\code\agent-dev | - | 0 | 0 | Y | - | has_change | has_change |
| 2aeb8833 | 1213d8ad | COMPLETED | opencode | 2026-10-01 02:59:01 | 2026-10-01 03:17:29 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 1e17d70e | 417a723b | COMPLETED | opencode | 2026-10-01 08:04:39 | 2026-10-01 08:16:01 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| bfe41159 | e1199159 | COMPLETED | opencode | 2026-10-02 03:06:27 | 2026-10-02 03:19:30 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 4b74bb68 | a205128b | COMPLETED | idle-worker-op | 2026-10-02 10:15:23 | 2026-10-02 10:17:32 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| c9eb2634 | 79b856ef | COMPLETED | opencode | 2026-10-02 11:23:11 | 2026-10-02 11:35:02 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 562c420e | 5b4fa957 | COMPLETED | opencode | 2026-10-03 01:02:54 | 2026-10-03 01:30:50 | E:\work\code\agent-dev | - | 0 | 0 | - | - | - | no_change_weak |
| bb74b448 | c1d3be13 | FAILED | opencode | 2026-10-03 02:17:50 | 2026-10-03 02:48:07 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 8cf8f433 | e83cc9e2 | COMPLETED | opencode | 2026-10-03 02:50:10 | 2026-10-03 04:20:28 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | has_change | has_change |
| 2c725bc7 | 7fc61352 | COMPLETED | opencode | 2026-10-04 08:05:37 | 2026-10-04 14:02:03 | E:\work\code\agent-dev\mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 0e810ef4 | 8a966293 | COMPLETED | opencode | 2026-10-05 07:27:38 | 2026-10-05 08:15:04 | E:\work\code\agent-dev\mio-t… | - | 3 | 0 | Y | - | - | has_change |
| da3e88f7 | e4c3bc51 | COMPLETED | opencode | 2026-10-05 10:28:01 | 2026-10-05 10:29:10 | E:\work\code\agent-dev\mio-t… | - | 4 | 0 | Y | - | - | has_change |
| d0f67b07 | 516bd0c7 | COMPLETED | opencode | 2026-10-05 16:08:12 | 2026-10-05 17:03:49 | E:\work\code\agent-dev\mio-t… | - | 9 | 1 | Y | - | - | has_change |
| e4160bce | d1b54de0 | COMPLETED | verify-owner | 2026-10-07 03:39:01 | 2026-10-07 03:39:01 | E:/work/code/agent-dev/mio-t… | - | 0 | 0 | - | - | - | no_change_weak |
| 948b6a0c | 1a75627b | COMPLETED | workbuddy | 2026-10-10 08:55:27 | 2026-10-10 09:03:00 | E:\work\code\csps-app-servic… | - | 0 | 0 | - | - | has_change | has_change |
| 7f3e0e92 | 7317590c | COMPLETED | opencode | 2026-10-13 07:32:36 | 2026-10-13 07:40:42 | E:\work\code\agent-dev | - | 0 | 0 | Y | - | - | no_change_weak |
| 69cb14eb | 6147c3aa | COMPLETED | workbuddy-agen | 2026-10-14 06:18:48 | 2026-10-14 06:19:08 | E:/work/code/csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| ec1cae62 | 7b121850 | COMPLETED | workbuddy-agen | 2026-10-14 06:52:34 | 2026-10-14 06:53:06 | E:\work\code\csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| ab46dbb0 | 977d0cda | COMPLETED | workbuddy-agen | 2026-10-14 07:38:16 | 2026-10-14 07:38:47 | E:\work\code\csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| 636372a9 | dd50f111 | COMPLETED | workbuddy-agen | 2026-10-14 07:54:57 | 2026-10-14 07:56:04 | E:\work\code\csps-app-servic… | - | 0 | 0 | - | - | - | unknown |
| c4ebec44 | 8820176b | COMPLETED | opencode | 2026-10-16 01:09:21 | 2026-10-16 01:14:24 | E:\work\code\agent-dev\mio-t… | - | 29 | 0 | Y | - | - | has_change |
| f99a810b | d00d624b | COMPLETED | workbuddy-need | 2026-10-16 03:21:21 | 2026-10-16 03:34:04 | E:/work/code/csps-app-fxyj/s… | - | 0 | 0 | - | - | - | no_change_weak |
| af4567bd | d22e21e7 | FAILED | idle-worker-pr | 2026-10-19 03:04:32 | 2026-10-19 03:04:32 | E:/work/code/csps-app-fxyj/s… | - | 0 | 0 | - | - | - | no_change_weak |
| 144e0cc2 | 4497a790 | COMPLETED | hermes | 2026-10-21 00:56:08 | 2026-10-21 01:03:09 | E:/work/code/agent-dev/mio-t… | - | 3 | 0 | Y | - | - | has_change |
| 28cbf7d1 | a1aba9ef | COMPLETED | hermes | 2026-10-21 01:57:20 | 2026-10-21 02:06:35 | E:/work/code/agent-dev/mio-t… | - | 7 | 0 | Y | - | - | has_change |
| d9639f33 | d468e633 | COMPLETED | hermes | 2026-10-21 02:59:21 | 2026-10-21 03:00:47 | E:/work/code/agent-dev/mio-t… | - | 36 | 0 | Y | - | - | has_change |
| d8884330 | 2f0be5e7 | COMPLETED | opencode | 2026-10-27 06:03:34 | 2026-10-27 06:05:37 | E:\work\code\agent-dev\mio-t… | has_change | 2 | 0 | Y | no_change | has_change | has_change |
| 52e7caeb | a2626309 | COMPLETED | idle-worker-op | 2026-10-28 06:23:13 | 2026-10-28 06:34:10 | E:\work\code\agent-dev\mio-t… | has_change | 0 | 0 | - | - | - | has_change |

## out_of_scope 明细（空 workspace 且无 deliverables）

| run | task | title | finished (UTC) |
|---|---|---|---|
| 23350d5f | be15742f | 清理 mio-taskhub 测试残留数据 | 2026-09-06 05:16:14.816679 |
| bebd385c | 2f5db835 | [定时] 自动生成创意想法 | 2026-09-14 08:40:52.392452 |
| f8af25a7 | 061edb5f | [定时] 自动生成创意想法 | 2026-09-27 05:27:36.897037 |
| 0a272f42 | a712157a | [定时] 自动生成创意想法 | 2026-09-27 05:30:58.164114 |
| 5898d570 | d2d07452 | MCP claim task_id 探针（用后即弃） | 2026-09-27 08:07:24.048637 |
| 8ba74580 | 45499c80 | wire-probe MCP task_id 探针（用后即弃） | 2026-09-27 08:07:24.177195 |
| ac7f64de | db05763c | [insight] taskhub_task_failure_rate | 2026-10-01 00:22:48.438092 |
| 4ae6740c | a4d6b5ab | [insight] taskhub_task_success_rate | 2026-10-01 00:24:26.301339 |
| 9d8957e3 | d049aa0a | [insight] taskhub_task_failure_rate | 2026-10-02 03:28:30.472387 |
| 0e20c2a1 | c1ccca2c | [insight] taskhub_task_success_rate | 2026-10-02 03:34:26.790421 |
| 78925d20 | 4652def8 | [insight] taskhub_task_failure_rate | 2026-10-02 03:46:50.533871 |
| 3f77bbad | eddb4814 | [insight] taskhub_task_success_rate | 2026-10-02 03:49:36.240525 |
| f4d9b620 | 2a8f4de3 | [insight] taskhub_task_failure_rate | 2026-10-02 04:12:23.013292 |
| 5a1b2435 | 6ddfcca7 | [insight] taskhub_task_success_rate | 2026-10-02 08:15:48.187921 |
| 0b767455 | f0dc4e27 | [insight] taskhub_task_failure_rate | 2026-10-02 08:17:26.605865 |
| df2d4de3 | 19365eb8 | [insight] taskhub_task_success_rate | 2026-10-02 08:18:24.354466 |
| 423aab9b | 6c638419 | [insight] taskhub_task_failure_rate | 2026-10-02 08:23:55.590633 |
| 22018a74 | 6bd57c29 | [insight] taskhub_task_failure_rate | 2026-10-02 09:00:46.520953 |
| 2da8073a | 9943f076 | [insight] taskhub_task_success_rate | 2026-10-02 09:19:48.032480 |
| aafda11b | e59b8096 | [insight] taskhub_task_success_rate | 2026-10-02 09:36:54.526700 |
| 099b0e23 | f0ec340b | [insight] taskhub_task_success_rate | 2026-10-02 09:57:03.007069 |
| ae06b274 | 11f13703 | [insight] taskhub_task_success_rate | 2026-10-02 10:01:42.456582 |
| 5a941d4e | ad059149 | [insight] taskhub_task_success_rate | 2026-10-02 10:31:51.408812 |
| 819eab54 | 50cdd3b4 | 「Test Idea (SCAMPER)」想法评审 | 2026-10-04 04:22:19.189743 |
| b5d2080e | 273d1d94 | 「Idea #1 (SCAMPER): 如何提升 agent 在 Windows | 2026-10-04 04:22:19.552978 |
| 078400c4 | c9f18918 | 「Idea #2 (Analogy): 如何提升 agent 在 Windows | 2026-10-04 04:22:19.934980 |
| a0053723 | 72d7ec63 | 「Idea #3 (First-Principles): 如何提升 agent  | 2026-10-04 04:22:20.297862 |
| 6eaaee12 | 1b7a428f | 「Idea #1 (SCAMPER): 如何让桌面agent自动化操作更可靠减少 | 2026-10-04 04:22:20.687140 |
| 6990b418 | 6092e39f | 「Idea #2 (Analogy): 如何让桌面agent自动化操作更可靠减少 | 2026-10-04 04:22:21.045734 |
| a96b9d1e | e318c2cc | 「Idea #3 (First-Principles): 如何让桌面agent自 | 2026-10-04 04:22:21.402867 |
| 9b368a69 | d5dc6f58 | 「Idea #1 (SCAMPER): 如何设计一个跨平台的 agent 记忆共 | 2026-10-04 04:22:21.720863 |
| 4578fb5d | 8ddbcf9b | 「Idea #2 (Analogy): 如何设计一个跨平台的 agent 记忆共 | 2026-10-04 04:22:22.075389 |
| d7dcbf62 | 01bb119c | 「Idea #3 (First-Principles): 如何设计一个跨平台的  | 2026-10-04 04:22:22.434330 |
| d6bd6657 | 9e2f1887 | 「自动清理已完成任务」想法评审 | 2026-10-04 04:22:22.792506 |
| 01ed32f6 | e0b06129 | 「任务依赖自动检测」想法评审 | 2026-10-04 04:22:23.150179 |
| 6b35850a | 2d070d41 | 「智能任务分配建议」想法评审 | 2026-10-04 04:22:23.509943 |
| 22f9a1df | fcefd58f | 「probe」想法评审 | 2026-10-04 04:22:23.868659 |
| f0779bbe | f1dd05b8 | 「Observer-Gated Task Scheduling via Cros | 2026-10-04 04:22:24.230293 |
| 424f479e | a611736f | 「SpliceKanban: Feature Title as Alternat | 2026-10-04 04:22:24.602371 |
| 08c1bc43 | 65e9699d | 「游标即标题：讨论区增量游标作为重构任务的版本化标题协议」想法评审 | 2026-10-04 04:22:24.962985 |
| 7c8d9083 | 5b313271 | 「收益回馈循环：以目标模板的预期收益字段作为次日上下文模板的探索主题」想法评审 | 2026-10-04 04:22:25.322268 |
| dd7125ee | 5a071baf | 「判例回放：任务中心周度工作回放时间线作为可上诉的判例法系统」想法评审 | 2026-10-04 04:22:25.696569 |
| 688cc460 | 2956630a | 「P4 核对临时-NULL归一活体」想法评审 | 2026-10-04 04:22:26.058923 |
| a386c471 | 25f011e4 | 「【示例】怎么填想法驾驶舱（看完可删）」想法评审 | 2026-10-04 04:22:26.419726 |
| 120a2aa1 | b8bfbacf | 「想法驾驶舱结构化字段「一键生成草稿」（AI 起草 + 人工微调）」想法评审 | 2026-10-04 04:22:26.738194 |
| d575c193 | 3ba65396 | 「溯因式想法生成：以观测台素材为前提、目标模板为结论、上下文模板为背景的假设生成 | 2026-10-04 04:22:27.075681 |
| ca8976d7 | fbcb79b7 | 「双读干涉：同构目标—上下文模板的语义角色冲突筛选每日灵感」想法评审 | 2026-10-04 04:22:27.433891 |
| 683a5053 | 6d90840e | 「里程碑：TaskHub 功能冻结 → 转真实运行验证」想法评审 | 2026-10-04 04:22:27.808088 |
| 9740af44 | d0f11a4f | [G0] T0.4 关键文档入库（解除 docs/ 忽略） | 2026-10-10 11:47:11.785468 |
| 0942c0d5 | 9ae9c3dc | [G0] T0.3 引入覆盖率并记录基线（禁止无数字承诺） | 2026-10-10 12:18:44.715743 |
| 6e6d7e3b | 7dac69de | [UI] 素材面板去过程化——主视图只保留素材内容 | 2026-10-12 02:52:25.407966 |
| 9b0dc8c8 | 164e9081 | [UI] 素材面板 v2——日刊流重设计（选题/洞察全文/来源按日分节） | 2026-10-12 03:15:53.206139 |
| 6b495abc | 9784640a | [G0] T0.1 修复 CI 安装步并达成首次绿色 CI | 2026-10-13 04:27:27.980016 |
| 2ff38b38 | 23e5e280 | [G0] T0.6 取消原因落库（失败可归因前置） | 2026-10-13 06:12:26.288508 |
| 3bc46d3c | 59481bfa | [G0] T0.5 结构化日志补字段 + 质量分可回溯取证 | 2026-10-13 06:44:23.286755 |
| f042b178 | 59481bfa | [G0] T0.5 结构化日志补字段 + 质量分可回溯取证 | 2026-10-13 06:46:07.248368 |
| e56f90ab | 3dc9567e | [G0] T0.7 Agent 生命周期埋点实施（最高优先证据缺口） | 2026-10-13 07:56:17.497884 |
| 1fff9e6c | 79ae2e68 | [G0] T0.8 reaper 决策样本扩充 + 三分根因分析脚本 | 2026-10-13 08:23:37.107069 |
| dc74b371 | 4d2cd35d | [G0] T0.2 清除死引用用例与 flaky 根因 | 2026-10-13 08:55:50.118522 |
| aa4c82ed | ffbe184b | EmbedView 清晰化重构：彩色徽标+进度条+空态+卡片化 | 2026-10-13 09:10:22.177419 |
| 771e185b | ed9fa041 | StatsView 趋势图：关键指标时间序列可视化 v2 | 2026-10-13 09:18:02.712146 |
| e4c8599d | e15f502d | BoardView 场景分组：今日聚焦/逾期/全部 | 2026-10-13 09:20:22.192628 |
| 4121c1c7 | b1927cd1 | ObservabilityView 趋势图+SLO折线+场景分组 | 2026-10-13 09:24:00.417612 |
| 7bf04ddd | aacd68da | [G0] T0.9 基线冻结清单与一键复现脚本 | 2026-10-13 10:34:39.987453 |
| 53935fdd | 8ffd3169 | [G1] T1.1 04 实验设计评审通过并冻结 | 2026-10-14 01:46:53.768971 |
| 67765ccf | 5c85e5ed | [G1] T1.2 实现实验 harness（验证性代码） | 2026-10-14 02:36:48.949340 |
| 549ecebb | aa95b921 | [G1] T1.3 档位自检探针 + 复现一致性验证 | 2026-10-14 03:07:16.146941 |
| 73a4aea2 | aa95b921 | [G1] T1.3 档位自检探针 + 复现一致性验证 | 2026-10-14 03:09:59.224351 |
| cb9376a4 | 089a1fee | [G1] T1.4 D1 生产样本持续采集至 n≥100 首启样本 | 2026-10-14 03:48:32.900585 |
| 3f171936 | f2416d26 | [G1] T1.5 S4b 破坏性操作审批 + 逐轮 policy 预检 | 2026-10-14 03:54:27.516648 |
| 2859d2ae | 08c7722d | [G1] T1.6 B 系列资源审批（消耗真实 agent 额度） | 2026-10-14 03:59:23.352619 |
| ba69d9a4 | 64bf8291 | [G1] T1.7 决策：质量门/生命周期门是否新增 env 开关（P5） | 2026-10-14 04:06:06.238039 |
| 33a610a6 | 6538317f | [G2] T2.1 运行 M 系列（R=3 轮）并汇总 | 2026-10-14 07:28:19.933933 |
| 85746d03 | 46840672 | [G2] T2.3 P 系列观察性分析得 V2 | 2026-10-14 07:38:30.227208 |
| ee566c32 | d27f79e7 | [G2] T2.2 运行 B 系列得 V1（条件性） | 2026-10-14 07:49:31.730070 |
| c5f7212c | b50b777e | [G2] T2.4 D1 首启成功率统计与根因报告 | 2026-10-14 07:56:36.409791 |
| 53627050 | 1b7681bd | [G2] T2.5 实验报告与上游回写 | 2026-10-14 08:04:12.954004 |
| 97a96042 | 789ce682 | [G3] T3.1 D1 结果固化（自适应失败检测 + 有界 requeue + | 2026-10-14 08:35:55.939173 |
| ddb33161 | ec16e175 | [G3] T3.4 开源前置：许可与单一事实源 | 2026-10-14 10:34:15.278142 |
| 38fb2ccc | 1df168ff | coldstart-coldstart-1792029759-0 | 2026-10-15 02:02:40.045269 |
| c48852db | 636f333e | [G3] T3.5 零依赖首跑与 Quickstart 计时 | 2026-10-15 02:06:52.327260 |
| f0a20c9f | 7511d727 | [G3] T3.6 能力地图三选一落定 + 技术债清理 | 2026-10-15 02:12:19.253869 |
| 0537667e | 237dc133 | [G3] T3.2 D2-b 门控协议化（条件性，仅当 V1 成立） | 2026-10-15 02:14:56.671364 |
| e273ccd5 | 59623dd6 | [G3] T3.3 D3 可验证完成回执（条件性，仅当找到消费方） | 2026-10-15 02:15:10.141569 |
| 2476cebe | 1c684c1c | [G4] T4.1 版本号 / tag / CHANGELOG | 2026-10-15 03:49:04.250226 |
| a3f13afd | 8e0a0739 | [G4] T4.2 可复现构建与干净机验证 | 2026-10-15 03:57:37.993928 |
| bed0db64 | 86d6967a | [G4] T4.3 发布门 checklist 全绿 | 2026-10-15 05:35:32.284278 |
| 337d66c3 | 5d08ffb9 | 【需求23-F3】前端：附件层真实 URL（file/queryInfo 预览  | 2026-10-16 03:45:05.855998 |
| 4e7a12cf | 5007cf2f | [insight] taskhub_task_failure_rate | 2026-10-22 10:03:43.851824 |
| 19e1a997 | 595dea48 | [insight] taskhub_task_success_rate | 2026-10-22 16:04:38.597884 |
| b1b76605 | b2a2ea02 | [insight] taskhub_task_failure_rate | 2026-10-23 16:06:15.569605 |
| 7e5649c1 | 6ff947fb | [insight] taskhub_task_success_rate | 2026-10-26 07:44:41.676004 |
| 5288fe3b | f96fa0fd | [insight] taskhub_task_failure_rate | 2026-10-26 08:16:54.378983 |
| ab6820aa | d2aed384 | [insight] taskhub_task_failure_rate | 2026-10-29 06:51:28.434106 |
| 2559d589 | 60de9fab | [insight] taskhub_task_failure_rate | 2026-11-01 06:38:59.161206 |
| 4794b58e | a63e8f4c | [insight] taskhub_task_success_rate | 2026-11-01 06:42:37.389410 |
| 19ddecb2 | 0d284b6b | [insight] taskhub_task_failure_rate | 2026-11-02 07:38:17.034053 |
| 93cacdc8 | 6137db05 | [insight] taskhub_task_success_rate | 2026-11-02 07:50:55.032695 |
| 974552c4 | 072840a9 | [insight] taskhub_task_failure_rate | 2026-11-03 08:43:46.608639 |
| b4603267 | 228492b6 | [insight] taskhub_task_success_rate | 2026-11-03 16:02:29.879407 |
