# 发布前 Checklist 报告（T4.3 / G4 收口）

> 取证日：2026-10-14 · 工具：`scripts/release_checklist.py --ci` · commit `d677782`（CI run 38684036460）

## 1. 七项门槛逐项核对

| # | 门槛 | 判定 | 证据 |
|---|---|---|---|
| ① | CI 全绿 | **是** | 最新 run `38684036460` = **success**（GitHub Actions / windows-latest / py3.12） |
| ② | 无已知红 | **是** | 本地全量 `failed=0`（1372+ passed） |
| ③ | 许可合规 | **是** | `git ls-files LICENSE` = LICENSE；内容含 `MIT License` |
| ④ | README 一致 | **是** | `gen_readme_stats.py --check` = OK（v0.6.0 / 161 端点 / 47 工具 / 1212 用例 / 23,086 行） |
| ⑤ | 首跑达标 | **部分** | 本机冷启 n=5 PASS（3.9s/0.13s）；**外部 3 台干净机待人工取证**（`t35-coldstart-report.md`） |
| ⑥ | 有 Release | **是** | tag `v0.6.0` + `dist/mio-taskhub-win64.zip` SHA256 `9c56ab76…` + CHANGELOG v0.6.0 |
| ⑦ | 已知限制 | **是** | 能力地图 §9 逐项定档（保留19/延后3/删除1），0 项待定 |

**结果：6 项「是」+ 1 项「部分」→ all_green = false**。

## 2. 本轮为达标所做的修复（CI 三轮）

CI 从「4 失败」修到「全绿」，根因与修复：

| 轮 | 失败 | 根因 | 修复 |
|---|---|---|---|
| 1 | 4 项 | 脚本打印中文，CI stdout=cp1252 → `UnicodeEncodeError` | `first_start_stats.py`/`run_m.py`/`summarize.py` 强制 UTF-8 输出 |
| 2 | 3 项 | ① 漏 `run_b.py`；② `list_processes_under` 未展开 8.3 短名；③ CI checkout 不取 tag | ① `run_b.py` 补 UTF-8；② 改用 `os.path.realpath`；③ workflow `fetch-depth: 0` + tag 测试容错 |
| 3 | 3 项 | ① `gen_readme_stats` 打印 drift 崩；② `write()` 一次性 replace 二次失效致 README LOC 漂移；③ `insight_remediator` 后台建 ready 任务污染 claim 用例（F2） | ① 补 UTF-8；② regex 重写 `write()`；③ conftest 关 `MIO_INSIGHT_AUTOTASK` |

## 3. 判定（Go / No-Go）

- **`03` §7 D1** 为「部分成立」，**未解锁 D2-a**；本 checklist 仅判「是否具备发布前置」。
- **6/7 是 + ⑤部分**：⑤的「本机 n=5」已 PASS，仅缺**外部 3 台干净机**人工取证（不可由 agent 代跑）。
- **发布判定**：**暂不对外发布**，直至 ⑤ 外部取证补齐；其余 6 项已全绿。
- **纪律**：⑤未补齐前，对外**不得**宣称「3 台干净机验证通过」；**不得**称「生产级」（`03` §6.2 / `01` §4.3）。

## 4. 复现

```powershell
python scripts/release_checklist.py --ci      # 人类可读（含 GitHub CI 查询）
python scripts/release_checklist.py --ci --json
```
