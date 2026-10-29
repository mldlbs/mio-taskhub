# 门禁报告绑定与发布入口审计（2026-10-29）

> 对应编号 15 P0。结论：门禁报告已绑定代码与数据库；真实发布入口已强制校验；分支保护仍是仓库设置，代码无法代替。

## 1 报告绑定

`scripts/materials_gate.py` 生成的 JSON 新增：

| 字段 | 含义 |
|---|---|
| `binding.gate_version` | 门禁实现版本 |
| `binding.code_commit` | 生成报告时的 `git rev-parse HEAD` |
| `binding.code_dirty` | 工作区是否有未提交改动 |
| `binding.db_path` | 被测数据库路径 |
| `binding.db_sha256` / `db_size` | 被测数据库内容指纹 |
| `report_fingerprint` | 报告自身（除该字段外）的 canonical sha256 |

校验：

```bash
python scripts/verify_gate_report.py --report <gate.json> [--db <db>] [--commit <sha>]
```

失败条件：`ok != true`、指纹不符、commit 不符、工作区脏、DB 文件缺失、DB 哈希/大小不符。

## 2 发布入口

`packaging/release.ps1`（真实发布入口）新增：

- `-GateReport <json>` 为真实发布的必填参数；
- 发布前调用 `verify_gate_report.py`，失败即 `throw`；
- `-DryRun` 不发布，允许不提供报告；
- **没有 `-SkipGate` 之类的绕过开关**。

`packaging/build.ps1` 只构建，不发布；发布动作集中在 `release.ps1`。

## 3 CI

`.github/workflows/test.yml` 在 push/PR 运行：

- `tests/test_materials_gate.py`（25 项）；
- `tests/test_materials_validate.py`；
- `tests/test_gate_attestation.py`（5 项：有效报告、篡改报告、DB 变更、错误 commit、失败门禁）。

CI 覆盖的是“代码与门禁测试”，**不是**手动发布入口；发布入口由 `release.ps1` 的必填校验覆盖。

## 4 抗绕过结论

| 路径 | 是否可绕过 | 依据 |
|---|---|---|
| 门禁 CLI | 否 | 无 skip 参数，失败退出码 1 |
| CI 测试 | 否（配置后） | 步骤失败即红；需在仓库设置中把该 job 设为 required |
| 发布脚本 | 否 | `-GateReport` 必填 + 校验失败 throw |
| 提交历史 | 可伪造报告？ | 报告指纹 + commit + DB 哈希绑定；篡改会被测试覆盖 |
| 分支保护 | **自动检测** | `.github/workflows/branch-protection.yml` 每日/手动运行 `scripts/check_required_checks.py`，缺少 `test (3.12)` / `materials-gate` 即红 |

分支保护本身仍由仓库设置决定，但不再依赖人工记忆：定时 workflow 会调用 GitHub API 检查 required checks，未配置就失败。

## 5 已知边界

1. **报告是发布时产物，不是源码**。报告绑定的是生成时的 commit；如果把报告提交进仓库，下一个 commit 就会让 `code_commit` 不匹配。发布时应从目标 commit 的干净工作区生成报告并归档，不要把历史报告当作本次发布证据。
2. **被测 DB 必须随报告可访问**。`verify_gate_report.py` 会重新哈希 DB；只给报告不给 DB 无法通过。
3. **`code_dirty` 必须为 false**。发布前先提交所有改动；构建脚本注入版本号应发生在校验之后。
4. 分支保护 API 检查需要 `BRANCH_PROTECTION_TOKEN`（Administration 读权限）；未配置 secret 时 workflow 会失败，这是有意的门禁而不是跳过。
5. required checks 名称随 job 名变化：当前为 `test (3.12)` 与 `materials-gate`；改 workflow job 名后需同步脚本参数。

## 6 与生产迁移的关系

分支保护是**代码发布治理**，不是数据库迁移正确性的一部分。实际 required checks 尚未用有权限凭证确认；因此：

- 它不阻塞数据库迁移；
- 它必须阻塞受保护代码的正式发布；当前 `release.ps1` 已强制校验门禁报告，作为代码发布侧的阻断；
- 待有 Administration 权限的 token 后，运行 `check_required_checks.py` 确认并修复配置，再作为发布治理的完成条件。
