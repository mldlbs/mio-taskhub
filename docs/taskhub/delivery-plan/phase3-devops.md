# 环境、构建、CI、发布前置、运行与回滚（Phase 3 / 任务 #7）

> 编制：陆上线（部署运维工程师）｜取证日 2026-11-06
> 对象：`E:\work\code\agent-dev\mio-taskhub` 现有 v0.6.1 代码库。本章只描述现有环境与现有链路的发布前置、缺口与修补项，不新增功能、不改源码、不重设计部署形态。
> 事实基线：`docs/taskhub/_delivery-fact-pack.md`；已完整读取 `docs/taskhub/frozen-scope-20261021.md`、`build-release-checklist.md`、`feature-prune-inventory-20261028.md`、`diff-gate-grayscale-log.md`、`docs/roadmap/02-current-state-audit.md`（取证日 2026-10-10）、`docs/roadmap/05-execution-plan.md`（2026-10-10）、`docs/roadmap/t42-repro-build-report.md`、`docs/roadmap/t43-release-checklist-report.md`；归档底稿 `delivery-plan/phase1-architecture.md`、`phase2-backend.md`、`phase2-data.md` 存在且已读。
> 源码/配置复核：`pyproject.toml`、`.github/workflows/test.yml`、`.github/workflows/branch-protection.yml`、`packaging/build.ps1`、`packaging/release.ps1`、`packaging/run.py`、`mio-taskhub.spec`、`Dockerfile`、`README.md`、`scripts/gen_readme_stats.py`、`scripts/release_checklist.py`、`scripts/repro_build.py`、`scripts/make_zip.py`、`scripts/make_setup.py`、`scripts/check_installer_bundle.py`、`scripts/check_required_checks.py`、`web/src/api.js`、`mio_taskhub/main.py`、`mio_taskhub/db.py`、`mio_taskhub/ops/backup.py`、`mio_taskhub/release_assets.py`、`tests/conftest.py`、`mio_taskhub/version.py`、`.gitignore`、`git ls-files` / `git check-ignore` 输出、`docs/ops/DEPLOYMENT-20261029.md`、`docs/ops/DEPLOYMENT-20261101-ledger-ui.md`。
> 表达口径：文档门控只称「结构完整性校验」与「服务端强制」。冻结清单 8 项不解冻，唯一投入项为窄门禁 `MIO_DIFF_GATE`。不确定处标【待确认】。工期只给区间与依赖，不给日历日期。Star / Trending / fork / 转发数不进入任何发布判据。

---

## 0. 本章范围与三条硬约束

| 约束 | 内容 | 落点 |
|---|---|---|
| 定位不变 | 本地单用户、零联网、零外部依赖、Windows x64 优先、单进程单库单端口 48620 | §1、§9 |
| 冻结不变 | 8 项冻结清单不解冻；`MioDigestJob` / `ContractJob` 默认仍开不擅自扩大冻结范围；唯一投入项是窄门禁灰度观察 | §6.2、§9 |
| 回滚先行 | 任何一次产物替换必须有「部署前备份 + 可核对的 sha256 + 分层回退步骤」，无回滚不发布 | §6.3、§11 |

**发布形态**：本系统没有服务端集群、没有流量切分维度、没有第二实例（hub 用命名 mutex 保证单实例，见 `packaging/run_hub.py` 相关实现）。发布动作是「单台 Windows 机上替换运行目录并重启进程」，不是蓝绿/滚动/金丝雀。被否决的备选方案与理由见 §9。

---

## 1. 环境规划

### 1.1 环境矩阵

| 环境 | OS | Python | Node | 依赖来源 | 端口 | 隔离要求 |
|---|---|---|---|---|---|---|
| 开发机 | Windows x64（取证时仓库位于 `E:\work\code\agent-dev\mio-taskhub`，用户目录 `C:\Users\admin`） | 3.12.14（`.venv`，`docs/roadmap/02` §1.2 实跑值）；`pyproject.toml` 声明 `>=3.10` | Vite 5 / React 18 构建；Docker 构建阶段用 `node:20-alpine` | `pyproject.toml` + `uv.lock`（已 git 跟踪）+ `web/package-lock.json`（已 git 跟踪），`.venv` 本地安装 | 48620（hub 默认 `--host 127.0.0.1 --port 48620`，`main.py:469-471`） | 跑测试必须设 `MIO_TEST_DB_PATH` 指向独立临时库（`tests/conftest.py:7-13`）；全量 pytest 必须加 `--basetemp` 独立目录 |
| CI | `windows-latest` + `py3.12`（matrix，`test.yml:11-14`）；分支审计 job 为 `ubuntu-latest`（`branch-protection.yml:10`） | `actions/setup-python@v5` 3.12 | 无（CI 不构建前端；仓库内 workflow 无 npm 步骤） | `pip install -e ".[dev]"`（当前绕过 `uv.lock`）；`pip install pytest` 再装一次 | 不监听（默认排除 e2e，`addopts = "-m 'not e2e'"`；TestClient 不起真实端口） | `MIO_TASKHUB_DB` 当前设在 `${{ runner.temp }}/test_taskhub.db`，但被 `conftest.py:11` 无条件覆盖，实际无效（见 §3.3） |
| 干净机首跑 | Windows x64，**无 Python、无 Node、无预装依赖** | 不需要 | 不需要 | 绿色版 `dist/mio-taskhub-win64.zip` 解压，或单文件 `mio-taskhub-setup.exe` 安装 | 48620 必须未被占用；`net_guard.is_loopback_bind` 默认只绑 loopback | 用户数据目录 `%USERPROFILE%\.mio_taskhub` 首次创建；与开发机生产库物理隔离；卸载默认保留用户数据 |
| 打包机 | Windows x64 + `.venv`（含 PyInstaller）+ Node/npm | `.venv\Scripts\python.exe`（`build.ps1` 明令不得用 `sys.executable`） | npm（`web/package-lock.json`） | 同开发机；`packaging/build.ps1` 前端 `npm install` + `npm run build`；PyInstaller `mio-taskhub.spec` | 构建期需杀掉占用 `dist/` 的 `TaskHub.exe`（`build.ps1:12-21`）；验证期启动 48620 | 构建期与运行期共用一台机；`CODEBUDDY_SAFE_DELETE_ENABLED=0` 仅对本次构建命令生效，禁止持久化（`pyinstaller-rebuild-windows` skill §2） |

**打包机的硬路径约束（实测）**：`packaging/build.ps1:66` 把主程序 PyInstaller 解释器写成绝对路径 `E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe`，而 `build.ps1:97/140/155/165` 用的是 `$root\.venv\Scripts\python.exe`。换机器或换仓库路径时，第 66 行会指向不存在的解释器并终止构建。该项列入 §4.4 缺口与 §10 任务卡。

### 1.2 数据目录与服务目录（两个目录不能混用）

| 目录 | 解析方式 | 内容 |
|---|---|---|
| `%USERPROFILE%\.mio_taskhub` | `mio_taskhub/db.py` 的 `DB_PATH = os.environ.get("MIO_TASKHUB_DB")` 或 `~/.mio_taskhub/taskhub.db`；`ops/backup.py` 的 `~/.mio_taskhub/backups/` | `taskhub.db`（含 `-wal` / `-shm`）、`console.log`、`runtime.log`、`backups/`、`night_plans/latest.json`、`deploy-*/` 历史运行目录副本 |
| `MIO_HOME` | `mio_taskhub/mio_runtime.py::home()`：`os.environ.get("MIO_HOME")` 或默认 `~/.mio-intelligence` | mio-intelligence runtime 侧的 `memory.jsonl`、`experience_reuse.jsonl`、`lifecycle/`、`creativity/`、`value-discovery/`、`research_serve.log`；本系统对这些路径只读，且相关能力属冻结域 |
| 安装目录 | `%LOCALAPPDATA%\Programs\mio-taskhub`（安装包路径，README §下载） | `TaskHub.exe`、`uninstall.exe`、`_internal/`、`workbuddy/`、分发脚本 |

`tests/conftest.py:17-20` 把 `MIO_HOME` 设为临时目录 `mio_home_tests_missing`，让 policy check fail-open、不拉起子进程；该设置只在测试进程内生效，不改变生产目录定义。

### 1.3 健康检查与优雅停机

| 项 | 实现 | 判据 |
|---|---|---|
| 存活 | `GET /healthz` → `{status:"ok"}`，不查库 | HTTP 200 |
| 就绪 | `GET /readyz` → `{status,db,backups:{count,latest}}`，`check_connection()` 执行 `SELECT 1` | 异常返回 503 `{status:degraded,db:error}` |
| 备份份数 | `ops/backup.py::get_backup_status()` 回显到 `/readyz` | 正常态应显示 31 份上限内的实际份数与最新时间 |
| 优雅停机 | `ThreadRegistry.stop_all()` 按 `reversed(entries)` 反向 `stop()` + `join(timeout=5)`；`lifespan` 退出序列为后台 job 逐个 stop → `stop_all()` → `stop_git_sync_worker()` / `stop_night_runner()` / `stop_cron_engine()` → `engine.dispose()` → `shutdown_otel()`（`phase2-backend.md` §4.3） | 停机前先停止接入，再等后台线程收尾；禁止直接 `taskkill` 作为常规停机手段 |
| 资源与伸缩 | 单进程；SQLite WAL + QueuePool（`pool_size=5` / `max_overflow=10`）；限流 `MIO_TASKHUB_RATE_LIMIT` 默认 120 req/min | 不设容器资源配额，不设水平伸缩；三条触发条件不成立（见 §9） |

---

## 2. 依赖钉死策略

### 2.1 `pyproject.toml` 现有钉死项（源码复核）

| 类别 | 项 | 版本 | 是否精确钉死 |
|---|---|---|---|
| 运行依赖 | `fastapi` / `starlette` / `uvicorn[standard]` / `sqlmodel` / `SQLAlchemy` / `apscheduler` / `pydantic` / `mcp` / `croniter` | `0.141.1` / `1.6.0` / `0.52.4` / `0.0.42` / `2.0.52` / `3.11.3` / `2.13.5` / `1.29.1` / `6.2.4` | 是（`==`，共 9 项） |
| 运行依赖 | `websockets` / `python-multipart` / `opentelemetry-sdk` / `opentelemetry-exporter-prometheus` / `opentelemetry-instrumentation-fastapi` / `opentelemetry-instrumentation-sqlalchemy` / `opentelemetry-instrumentation-httpx` | `>=12.0` / `>=0.0.9` / `>=1.25` / `>=0.46b0` ×4 | 否（下限，共 7 项，可漂移） |
| dev 依赖 | `pytest` / `httpx` / `pytest-cov` | `>=8.0` / `>=0.27` / `>=5.0` | 否（下限，共 3 项） |
| 构建后端 | `setuptools` | `>=68` | 否（下限） |
| 锁文件 | `uv.lock`（1838 行，`version = 1` / `revision = 3`，每个包带 sdist + wheel 的 sha256） | 已 `git ls-files` 确认跟踪 | 是，但**当前 CI 与 build.ps1 均不使用** |
| 前端锁 | `web/package-lock.json`（1738 行） | 已 `git ls-files` 确认跟踪 | 是，`docker` 阶段却用了 `npm install --frozen-lockfile`（非 npm 参数，见 §2.4） |

钉死原因（`pyproject.toml:12-14` 注释）：CI 全新装到 `sqlmodel 0.0.48` / `SQLAlchemy 2.1.4` 后 32 个测试因 naive datetime 校验收紧挂掉；`==` 组合是本地全量 1113 passed 验证过的组合（T0.1 / 9784640a，与事实包 1601 用例为不同日期不同基线）。

### 2.2 `pip install -e ".[dev]"` 的正确写法

历史故障（不可推翻）：`.github/workflows/test.yml` 安装步曾写成 `pip install -e ".[dev]" 2>/dev/null || pip install -e .`，Windows runner 的 `run:` 默认走 PowerShell，`2>/dev/null` 被解析为重定向到 `D:\dev\null`，该步 exit 1，53 次 CI 全红且 `Run tests` 一次都没执行（`docs/roadmap/02` §5.2）。G0 阶段修复后 CI 已绿、覆盖率本地基线 80.0%（事实包 §2；`docs/roadmap/04-review-20261014.md` 记 `g0_manifest.json` 基线 1284 passed / 1 skipped / 4 deselected / 0 failed、coverage 80.0%）。

当前 `test.yml` 已经为该步加 `shell: bash`，`2>/dev/null` 在 bash 下不再触发 PowerShell 路径解析，安装步可通过。但 `|| pip install -e .` 仍在：它会在 `.[dev]` 安装失败时静默降级为不带 dev 依赖的安装，把真实错误吞掉。正确写法与可复制原文：

```yaml
      - name: Install dependencies
        shell: bash
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
```

```powershell
# PowerShell 本机 / 打包机等价写法（丢弃错误流用 PowerShell 语法，不用 /dev/null）
pip install -e ".[dev]"
```

要点：① 不加 `2>/dev/null`，不吞安装错误；② 不加 `|| pip install -e .`，不让 dev 依赖缺失被降级掩盖；③ `".[dev]"` 的双引号在 Windows shell 下必须保留；④ 后续单独的 `pip install pytest` 与 dev 依赖 `pytest>=8.0` 重复，应删除；⑤ 若采用 `uv.lock` 作为唯一来源，安装步改为 `uv sync --locked --extra dev` 或 `pip install --require-hashes -r <由 lock 导出的 requirements>`，判据见 §12【待确认】④。

### 2.3 可复现构建还缺什么

`docs/roadmap/t42-repro-build-report.md` 已验证的是**产物内容可复现**：同 commit（`d916a20` / tag v0.6.0）两次干净构建，原始逐字节 805/807 一致、`scripts/repro_build.py compare-norm` 归一化 807/807 一致，差异仅 PE `TimeDateStamp` / `CheckSum` 与 `base_library.zip` 成员 mtime，属 PyInstaller 已知非确定性、可解释。该报告验证的是「同一份依赖装出来的产物」，不是「依赖解析本身可复现」。当前缺口：

| # | 缺口 | 证据 | 修补项（判据） |
|---|---|---|---|
| D-1 | 依赖解析未锁死：9 项 `==` 之外有 10 项 `>=`（含 4 个 `opentelemetry-instrumentation-*`、`websockets`、`python-multipart`、3 个 dev、build backend）与全部传递依赖 | `pyproject.toml`；`uv.lock` 存在但安装路径绕过 | 发布机上以 `uv.lock` 为唯一来源，判据：`uv lock --check` 或 `uv sync --locked` 返回 0，且 `uv.lock` 与 `pyproject.toml` 无 diff |
| D-2 | hash 未参与安装校验 | `uv.lock` 内含 sha256，pip 路径未使用 | 判据：安装命令带 `--require-hashes`，或 `uv sync --locked` 成功且无网络降级 |
| D-3 | 版本号四个来源无一致性校验 | `pyproject.toml:7` = `0.6.1`；`mio_taskhub/version.py` = `0.6.1`；`packaging/build.ps1:24-47` 的 `-Version` 会覆写 `version.py` 而不改 `pyproject.toml`；`README.md` 顶部 `v0.6.1`；`git tag -l` 已有 `v0.6.1` | 判据：新增一条校验命令，比较 `pyproject.toml` / `version.py` / tag / README 四处版本，不一致即 exit 1；`build.ps1 -Version` 必须与 `pyproject.toml` 同步 |
| D-4 | `build.ps1:66` 解释器绝对路径硬编码 | `packaging/build.ps1:66` | 判据：把该行改为 `$root\.venv\Scripts\python.exe`，或在非默认路径的打包机上完整跑通 `build.ps1 -Quick` |
| D-5 | `.ps1` 编码要求未进门禁 | `build.ps1` 首字节为 UTF-8 BOM（`﻿#`） | 判据：发布前检查 `build.ps1` / `release.ps1` 为 UTF-8 with BOM，缺 BOM 时中文与中文文件名解析失败 |
| D-6 | `npm` 锁文件参数错误（仅 Docker 路径） | `Dockerfile:5` 为 `npm install --frozen-lockfile 2>/dev/null \|\| npm install`；`--frozen-lockfile` 不是 npm 参数（npm 用 `npm ci`） | 判据：若保留 Docker 路径，改为 `npm ci`；若不保留，从发布面移除该路径并同步 README 部署栏（见【待确认】⑧） |

### 2.4 制品版本与追溯

| 项 | 现状 | 发布判据 |
|---|---|---|
| 制品清单单一事实源 | `mio_taskhub/release_assets.py::all_release_assets()` 返回 `("mio-taskhub-win64.zip", "mio-taskhub-installer/mio-taskhub-setup.exe")`；`packaging/release.ps1:44-52` 从该模块读取，不另抄一份 | 发布资产集合与该返回值逐项相等 |
| SHA256 | `Get-FileHash dist\mio-taskhub-win64.zip -Algorithm SHA256`；t42 报告记录 v0.6.0 zip 为 `9c56ab763d2ed1196ba3dd2dbb0d493206ca3d976fb1dbd441fd29a1f8eea35` | 新版本重新计算并写进 Release 说明；产物不入库（`dist*/` 被 `.gitignore:22` 忽略） |
| 归一化构建对比 | `scripts/repro_build.py manifest-norm` / `compare-norm` | 同 commit 两次干净构建 `compare-norm` 差异为 0；原始差异必须逐条可解释 |
| 版本注入 | `build.ps1 -Version <x.y.z>` 覆写 `version.py` | 注入后必须跑 §2.3 D-3 的四处一致性校验 |

---

## 3. CI 流水线

### 3.1 现状逐步复核（`.github/workflows/test.yml`）

| 阶段 | 步骤原文 / 配置 | 复核结论 |
|---|---|---|
| 触发 | `on: push / pull_request: branches: [main, master]` | 只覆盖主干与主干 PR；`task-<8位>` 开发分支不触发（`docs/roadmap/02` §5.1 已记） |
| job `test` | `runs-on: windows-latest`；`strategy.matrix.python-version: ["3.12"]` | 单 OS 单版本；`fetch-depth: 0`（取全部历史 + tag，`test_version_consistency` 依赖） |
| 安装 | `shell: bash`；`python -m pip install --upgrade pip`；`pip install -e ".[dev]" 2>/dev/null \|\| pip install -e .`；`pip install pytest` | G0 已加 `shell: bash` 修复 `D:\dev\null` 根因；`2>/dev/null` 与 fallback 仍在，会静默降级（§2.2） |
| 测试 | `python -m pytest tests/ -v --tb=short -k "not test_two_agents_race and not test_webhook_guard"`；`env: MIO_TASKHUB_DB: "${{ runner.temp }}/test_taskhub.db"` | 用 `-k` 回避 `test_two_agents_race` 与 4 个 `webhook_guard` 死引用用例（注释：`CronEngine._should_skip_webhook_generation` 已改名）；未跑覆盖率；未加 `--basetemp` |
| 产物 | `if: failure()` → `actions/upload-artifact@v4`，`path: .pytest_cache/` | 只上传 pytest 缓存，无 junit / coverage / 构建产物归档 |
| job `materials-gate` | `windows-latest` + py3.12；同样安装步；`python -m pytest tests/test_materials_gate.py tests/test_materials_validate.py tests/test_gate_attestation.py tests/test_release_entry.py -q` | 素材门禁 4 个专项文件，独立 job |
| 分支保护审计 | `.github/workflows/branch-protection.yml`：`schedule: cron "0 6 * * *"` + `workflow_dispatch`，`ubuntu-latest`；`python scripts/check_required_checks.py --repo ... --branch main --require "test (3.12)" --require "materials-gate"`；`env: GH_TOKEN: ${{ secrets.BRANCH_PROTECTION_TOKEN }}` | 每天一次审计 required checks 是否配置，不是发布卡点；依赖 `BRANCH_PROTECTION_TOKEN` secret |
| 本地发布门 | `scripts/release_checklist.py --ci` 7 项（CI 全绿 / 0 红 / LICENSE / README 一致 / 首跑达标 / 有 Release / 已知限制） | t43 报告（2026-10-14，commit `d677782`）结论：6 项「是」+ ⑤ 首跑「部分」，`all_green = false`，**暂不对外发布** |

**质量门禁现状**：`addopts = "-m 'not e2e'"` 默认排除 4 个 e2e；覆盖率按需执行 `python -m pytest -q --cov=mio_taskhub --cov-report=term-missing --cov-report=xml`，未进 `addopts`、未进 CI。事实包与 `04-review-20261014.md` 的「覆盖率 80.0%」是 G0 本地冻结基线数值，不是 CI 门禁阈值。全量 pytest 基线按任务书口径：1235 passed / 1 skipped / 4 deselected + 4 个 pre-existing 失败。

**全量 pytest 数字口径（四个不同文件 / 不同取证日，单一数字不作门禁）**：本章及发布门引用任何测试基线数字时必须同时给出文件与取证日。

| 出处 | 取证日 | 输出行 | 本章用法 |
|---|---|---|---|
| 任务书基线 | — | 1235 passed / 1 skipped / 4 deselected + 4 个 pre-existing 失败 | 本章主口径 |
| `docs/roadmap/02-current-state-audit.md` §4.1 / 附录 A.3（v0.1） | 2026-10-10 | `4 failed, 1260 passed, 1 skipped, 4 deselected, 2 warnings in 393.28s`；收集总数 1269（`1265/1269 collected`） | 历史快照，§3.1 现状复核引用 |
| `docs/roadmap/04-review-20261014.md` §冻结基线（HEAD `ac21dfc0`） | 2026-10-14 | `1284 passed / 1 skipped / 4 deselected / 0 failed`（458.15s）、coverage 80.0% | G0 冻结基线，仅用于说明覆盖率数字来源 |
| `docs/roadmap/t43-release-checklist-report.md` 门槛 ② | 2026-10-14 | 本地全量 `failed=0`（`1372+ passed`） | 发布门报告，说明当时无已知红 |

核实结论（2026-11-06 只读复核）：`docs/roadmap/02-current-state-audit.md` 当前磁盘版本的 §4.1、§7 C4、§11、附录 A.3 均为 1260 / 1269 / 1 skipped，**没有 `1372 passed / 3 skipped`**；`1372+ passed` 只出现在 `t43-release-checklist-report.md:10`，`3 skipped` 在 `docs/roadmap/` 全目录无匹配。四个数字来自四个不同文件与日期，不是同一文件的不同版本快照；引用时不合并、不互相替代。

### 3.2 缺口与最小修补项

| # | 缺口 | 最小修补 | 本期归类 |
|---|---|---|---|
| C-1 | 安装步 `2>/dev/null \|\| pip install -e .` 静默降级，dev 依赖缺失可被掩盖 | 换成 §2.2 的三行原文；删掉重复的 `pip install pytest` | **本期必做** |
| C-2 | CI 未跑覆盖率，80.0% 只是本地记录，无法在干净环境复现 | 明确二选一：加一步 `--cov=... --cov-report=xml` 并归档 `coverage.xml`；或书面登记「覆盖率不设 CI 门禁，仅本地基线」 | **本期必做**（决策 + 留痕），阈值是否设 80.0% 见【待确认】② |
| C-3 | `scripts/gen_readme_stats.py --check` 未进 CI，而 G3 / T3.4 判据明确要求「CI 校验漂移即红」 | 在 `test` job 增加一步 `python scripts/gen_readme_stats.py --check`（脚本已强制 UTF-8 输出，可直接在 Windows CI 跑） | **本期必做** |
| C-4 | `-k` 排除项长期存在（1 个 race + 4 个 `webhook_guard` 死引用），与「发布门无已知红」冲突 | 逐条登记：修复死引用或迁移用例（T0.2 / F4 口径），或书面列为「已知红例外」并在发布 checklist 中显式列出 | **本期必做**（登记与收口动作），是否修复用例见【待确认】③ |
| C-5 | `MIO_TASKHUB_DB` 在 CI 无效：`tests/conftest.py:7-11` 读取 `MIO_TEST_DB_PATH` 后无条件 `os.environ["MIO_TASKHUB_DB"] = _TEST_DB`，覆盖 workflow 设的值 | workflow 改设 `MIO_TEST_DB_PATH: "${{ runner.temp }}/ci_test.db"`；并行跑时每个 job/worker 用不同路径 | **本期必做** |
| C-6 | 未加 `--basetemp`；pytest 清理 `garbage-*` 会触发 safe-delete 批量删除保护、teardown 被杀 | CI 全量命令追加 `--basetemp=<runner.temp 下独立目录>`；本地验收命令同步 | **本期必做** |
| C-7 | `uv.lock` / `web/package-lock.json` 已入库但不参与安装，传递依赖可漂移 | 见 §2.3 D-1/D-2 | 【待确认】④ |
| C-8 | 无版本号一致性校验（D-3）、无 SHA256 归档、无 CHANGELOG 校验步骤 | 见 §5 判据表 | 部分本期必做，部分【待确认】⑤ |
| C-9 | `branch-protection.yml` 只做每日审计，且 required check 名 `test (3.12)` 绑定 matrix 命名；`BRANCH_PROTECTION_TOKEN` 是否已配置未核实 | 保留审计；发布卡点由本地 `release_checklist.py --ci` 承担 | 【待确认】⑥ |
| C-10 | GitHub Actions 无人工审批卡点、无制品归档 | 本地单用户分发，Release 动作由 `packaging/release.ps1` 人工触发且 `-DryRun` 可预演 | 【待确认】⑦ |

### 3.3 CI 与本地验收命令（可复制原文）

```powershell
# 本地全量（隔离 DB + 独立 basetemp，避免 safe-delete 批量删除保护）
$env:MIO_TEST_DB_PATH = "$env:TEMP\mio_release_full.db"
.\.venv\Scripts\python.exe -m pytest -q --basetemp="$env:TEMP\mio_basetemp_release"

# 覆盖率（G0 基线口径）
.\.venv\Scripts\python.exe -m pytest -q --cov=mio_taskhub --cov-report=term-missing --cov-report=xml

# README 数字一致性
.\.venv\Scripts\python.exe scripts\gen_readme_stats.py --check

# 发布门 7 项
.\.venv\Scripts\python.exe scripts\release_checklist.py --ci
```

```yaml
# CI 建议的 env（替换 test.yml 现有的 MIO_TASKHUB_DB）
        env:
          MIO_TEST_DB_PATH: "${{ runner.temp }}/ci_test.db"
```

### 3.4 门禁清单（本期必做 / 待确认）

| 门禁 | 状态 | 失败处理 |
|---|---|---|
| 安装步无静默降级 | 本期必做 | 安装失败即 job 失败，不允许 fallback |
| `Run tests` 实际执行且 `0 failed`（例外需显式列出） | 本期必做 | 回到 §3.2 C-4 收口 |
| `gen_readme_stats.py --check` 通过 | 本期必做 | 漂移即红，先跑 `--write` 再提交 |
| 测试隔离（`MIO_TEST_DB_PATH` + `--basetemp`） | 本期必做 | 出现 `PermissionError: [WinError 32]` 或 teardown 被杀即判定隔离失效 |
| 覆盖率是否设为 CI 阈值 | 【待确认】② | 未定阈值前不得对外宣称 CI 覆盖率达标 |
| 人工审批卡点 / 制品归档 / SHA256 上传 | 【待确认】⑦ | 无卡点时，发布由人工执行 `release.ps1` 并留存命令输出 |

---

## 4. 构建、分发与校验

### 4.1 PyInstaller 流程（`packaging/build.ps1`，源码复核）

| 步 | 动作 | 关键原文 / 路径 |
|---|---|---|
| 0 | 杀掉占用 `dist/` 的进程 | `Get-WmiObject Win32_Process \| Where-Object { $_.Name -eq 'TaskHub.exe' }` → `Stop-Process -Force`；`Start-Sleep -Seconds 2` |
| 0.5 | 可选注入版本号 | `powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1 -Version 0.6.1`，覆写 `mio_taskhub/version.py` |
| 1 | 前端构建 | `Push-Location web` → `npm install`（仅当无 `node_modules`）→ `npm run build` |
| 2 | 清理旧产物 | `Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue` |
| 3 | 主程序打包 | `.venv\Scripts\python.exe -m PyInstaller mio-taskhub.spec --noconfirm --clean`；spec `datas=[(os.path.join(SPECPATH,"web","dist"), "web/dist")]` |
| 4 | 验证 5 项关键依赖 | `_internal\PIL`、`_internal\PIL\_imaging*.pyd`、`_internal\web\dist\index.html`、`_internal\web\dist\assets`、`TaskHub.exe`；任一缺失即 `throw` |
| 4.5 | 安装器 / 卸载器 | `-m PyInstaller mio-taskhub-installer.spec --distpath dist_installer --workpath build_installer`；只把 `uninstall.exe` 拷进 payload，`setup.exe` 不进 payload |
| 5 | 复制分发文件 | 见 §4.2 |
| 6 | zip + 单文件安装包 + 一致性校验 | `scripts/make_zip.py`（确定性 zip，不用 `Compress-Archive`）→ `scripts/make_setup.py --setup ... --payload ... --out ... --verify` → `scripts/check_installer_bundle.py` 返回 `[OK]`；`-Quick` 跳过第 6 步 |
| 收尾 | 启动 hub 并验证 | `Start-Process ...\TaskHub.exe -ArgumentList 'hub'` → `Start-Sleep -Seconds 4` → 读 `C:\Users\admin\.mio_taskhub\runtime.log` 尾部（**此处亦硬编码用户名，见 §4.4**） |

`packaging/run.py` 按 `sys.argv[1]` 分派 hub / mcp / widget 三角色；无黑框模式 stdout 为 None 需重定向；`payload_layout.py` 统一布局契约；排除 torch / pandas / scipy；目标 Windows x64。产物：`dist/mio-taskhub-v3/`、`dist/mio-taskhub-win64.zip`、`dist/mio-taskhub-installer/mio-taskhub-setup.exe`。

### 4.2 分发需补齐的文件与 `workbuddy/` 目录

`build.ps1:116-128` 的 `$distFiles` 实际列出 8 个文件，并复制 `packaging/workbuddy/` 整个目录：

| # | 文件（`packaging/` 下源） | 用途 |
|---|---|---|
| 1 | `setup-agent.bat` | 本机 Agent 接入（WorkBuddy） |
| 2 | `setup-agent.ps1` | 同上 PowerShell 版，内含 `workbuddy` 技能安装 |
| 3 | `setup-opencode.bat` | opencode 接入 |
| 4 | `setup-opencode.ps1` | 同上 PowerShell 版 |
| 5 | `使用说明.txt` | 用户说明 |
| 6 | `mio-taskhub-widget.bat` | widget 启动 |
| 7 | `agent_wrapper.py` | 无 MCP 时的 REST 降级包装器 |
| 8 | `idle_worker.py` | 空闲 worker |
| 目录 | `packaging/workbuddy/`（含 `taskhub-skill/SKILL.md`、`taskhub-skill/embed-bridge-template.html`） | WorkBuddy 技能与嵌入桥接模板 |

口径说明：`pyinstaller-rebuild-windows` skill §4 与 `docs/taskhub/build-release-checklist.md` 第 4 步使用「6 个分发文件」口径，指上表 1–6 项 + `workbuddy/` 目录；`build.ps1` 另加 `agent_wrapper.py` / `idle_worker.py` 两项。skill 记 zip 条目数 807 + 6 + 2 = **815**，与 `build.ps1` 的 8 项口径在总数上一致。**只跑 PyInstaller 不跑第 5 步会打出残缺 zip**：`dist/` 里只有 `_internal/` 与 `TaskHub.exe`，用户解压后找不到 `setup-agent.bat`（README 下载段却要求双击它）。该缺口在 skill 中记录为历史多轮 ad-hoc 重建共同问题。

可复制原文（等价 `build.ps1` 第 5 步的补拷）：

```bash
cd <项目根>
for f in setup-agent.bat setup-agent.ps1 setup-opencode.bat setup-opencode.ps1 "使用说明.txt" mio-taskhub-widget.bat; do cp -f "packaging/$f" dist/mio-taskhub-v3/; done
cp -f packaging/agent_wrapper.py packaging/idle_worker.py dist/mio-taskhub-v3/
cp -rf packaging/workbuddy dist/mio-taskhub-v3/
```

`docs/taskhub/build-release-checklist.md` 的干净重建顺序（与 build.ps1 并行存在，作为校验清单）：

```
1. rm -rf build build_installer dist_installer dist/mio-taskhub-v3
2. .venv/Scripts/python.exe -m PyInstaller mio-taskhub.spec --distpath dist/mio-taskhub-v3
3. Copy dist_installer/uninstall.exe → dist/mio-taskhub-v3/
4. payload 根须含 TaskHub.exe / uninstall.exe / _internal/ / workbuddy/ / agent_wrapper.py
5. .venv/Scripts/python.exe scripts/make_zip.py dist/mio-taskhub-v3 dist/mio-taskhub-win64.zip
6. .venv/Scripts/python.exe scripts/make_setup.py --setup dist_installer/mio-taskhub-setup.exe --payload dist/mio-taskhub-win64.zip --out dist/mio-taskhub-installer/mio-taskhub-setup.exe --verify
7. .venv/Scripts/python.exe scripts/check_installer_bundle.py  → 必须 [OK]
```

校验命令：`python -c "import zipfile; z=zipfile.ZipFile('dist/mio-taskhub-win64.zip'); print('TaskHub.exe:', 'TaskHub.exe' in z.namelist(), 'uninstall.exe:', 'uninstall.exe' in z.namelist())"`；静默装/卸端到端：`setup.exe --silent --dir <dir>` → `setup.exe --uninstall --silent --dir <dir>`，验证安装目录移除且无 `mio_embedded_*` 临时泄漏。

### 4.3 纯前端热更新路径（不重建 exe）

机制：`mio_taskhub/main.py:446-454`，frozen 时 `_web_dir()` 返回 `os.path.join(sys._MEIPASS, "web", "dist")`，`app.mount("/", StaticFiles(directory=WEB_DIR, html=True))` 由 Starlette 每次读盘；`no_cache_html` 中间件对 `text/html` 注入 `Cache-Control: no-store, max-age=0`。因此前端是 data 资源，替换磁盘文件即生效。

```bash
# 前提：web/ 下已 npm run build 生成 web/dist/
cd <项目根>
cp -rf web/dist/. dist/mio-taskhub-v3/_internal/web/dist/
```

判据：只需刷新浏览器（no-store 生效），不需重启 hub、不需 PyInstaller。**只有 `.py` 改动才需重建 exe**；`build.ps1 -Quick` 只跳 zip 仍在跑 PyInstaller，纯前端改动不应走它。历史部署记录 `docs/ops/DEPLOYMENT-20261101-ledger-ui.md` 与 `DEPLOYMENT-20261029.md` 用「旧前端目录备份覆盖」作回滚，与本路径同源。

### 4.4 构建期已知障碍（发布前置需处理）

| 障碍 | 证据 | 处理 |
|---|---|---|
| 绝对路径硬编码 | `build.ps1:66` 主程序解释器、`:180` 读 `C:\Users\admin\.mio_taskhub\runtime.log` | 换机/换用户即失败；列 §10 任务卡 T7-03 |
| 旧 exe 锁 `dist/` | `pyinstaller-rebuild-windows` skill §1：mcp 每会话一实例，实测 6 实例 = 1 hub + 5 mcp | 用 `tasklist.exe \| grep -i taskhub` 查实例（该环境 `Get-Process` / `Get-CimInstance` 漏检）；`MSYS_NO_PATHCONV=1 taskkill /pid <PID> /f` |
| safe-delete 批量删除保护 | skill §2：PyInstaller `--clean` 清 `build/` 或 COLLECT 删临时目录时，`SAFE_DELETE_FAIL_CLOSED` 会让构建 abort，且可能「Build complete」假成功而 exe mtime 未更新 | 旧 `dist/` / `build/` 用 `mv` 改名移走（rename 不触发钩子）；构建命令前 `export CODEBUDDY_SAFE_DELETE_ENABLED=0`（仅本次、不持久化）；构建后必查 `ls -la dist/mio-taskhub-v3/TaskHub.exe` 时间戳 = 本次构建时刻 |
| zip 不可复现 | `build.ps1:135-139` 注释：`Compress-Archive` 遇只读 `base_library.zip` 抛 `UnauthorizedAccessError`、布局不稳、成员顺序/时间戳不稳 | 必须用 `scripts/make_zip.py` |
| `.ps1` 编码 | 中文资产名 + BOM 要求 | 保存为 UTF-8 with BOM；中文名文件处理用 PowerShell 更稳 |

### 4.5 exe 与前端校验

**exe 校验（CArchiveReader / PYZ.pyz，反查本轮新增标识符）**：`noarchive=False` 时纯 Python 模块编译进 exe 内嵌 `PYZ.pyz`。判据是「本轮新增的常量名 / 函数名 / 环境变量名在包内可查到」，验证对象是打包内容而非路由注册结果，不受数据状态影响。

```python
import os, tempfile, types
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
c = CArchiveReader(r'dist\mio-taskhub-v3\TaskHub.exe')
tmp = tempfile.gettempdir() + r'\pyz_probe.pyz'
with open(tmp, 'wb') as f:
    f.write(c.extract('PYZ.pyz'))
z = ZlibArchiveReader(tmp)
code = z.extract('mio_taskhub.diff_gate')   # 点号分隔的模块名

def walk(o, out):
    if isinstance(o, str): out.append(o)
    elif isinstance(o, types.CodeType):
        for a in ('co_consts', 'co_names', 'co_varnames'):
            for x in getattr(o, a, ()) or (): walk(x, out)
    elif isinstance(o, (tuple, list, set, frozenset)):
        for x in o: walk(x, out)
    return out
names = walk(code, [])
print('MIO_DIFF_GATE' in names, 'empty_diff_warn' in names, 'MAX_REJECTIONS' in names)
```

必须同时扫 `co_consts` + `co_names` + `co_varnames` 并递归进 tuple；只看 `co_consts` 且只认 `str` 会假阴性（模块级 tuple 常量里的字符串在 `co_consts` 但类型是 tuple）。版本校验另走运行时 `GET /openapi.json` 的 `info.version`（PYZ 内嵌、plaintext grep 查不到）。

**前端 hash 校验**：只验 API 200 无法发现「exe 冻结的是旧前端」。构建前记下 `web/dist/assets/index-*.js` 的文件名，启动 exe 后比对：

```bash
ls web/dist/assets/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
curl -s --noproxy '*' http://127.0.0.1:48620/ | grep -o 'index-[A-Za-z0-9_-]*\.js'
```

两处字符串一致才证明新前端已生效；不一致说明 spec 未打包最新 `web/dist`，需重新 `npm run build` 后按 §4.3 覆盖（纯前端）或重建 exe。历史部署记录用 `index-D6N_DTyo.js` / `index-B4blXezh.js` / `index-cBAPnm5P.js` 同名口径留证。

**独立实例现场验证**（不污染 48620 真实库）：`MIO_TASKHUB_DB='E:/abs/path/.smoke_ws/smoke.db' MIO_TASKHUB_PORT=48621 ./dist/mio-taskhub-v3/TaskHub.exe hub`，环境变量必须 Windows 绝对路径（传 Git Bash 的 `/e/...` 会被当成盘根相对路径连到空库，看起来像验证通过）。空临时库不能证明过滤/聚合类修复，此类需复制真实库做新旧 exe A/B（`cp ~/.mio_taskhub/taskhub.db .smoke_ws/exe_smoke.db`，先停 hub 保证一致）。

---

## 5. 发布前置 checklist（每项给判据）

现状基线：`docs/roadmap/t43-release-checklist-report.md`（2026-10-14，commit `d677782`，CI run `38684036460`）结论为 6 项「是」+ 1 项「部分」，`all_green = false`，**暂不对外发布**；缺的是 ⑤ 外部 3 台干净机人工取证，不得由 Agent 代跑、不得宣称「3 台干净机验证通过」。

| # | 前置项 | 判据（可勾选的完成条件） | 现状 |
|---|---|---|---|
| P-1 | LICENSE | `git ls-files LICENSE` 输出 `LICENSE`，且文件含 `MIT License` 字面串（`scripts/release_checklist.py::check_license`） | 已核实：LICENSE 已跟踪、含 MIT；**通过** |
| P-2 | README 统计脚本与数字口径冻结 | ① `python scripts/gen_readme_stats.py --check` 返回 0；② 顶部统计表全部 7 个字段（version / REST / MCP / 用例 / 后端 LOC / 前端 LOC / 前端文件数）等于脚本派生值；③ 正文历史残留数字（`mcp_server.py # 40 个 MCP 工具`、`api/ # 26 个路由模块（119 个端点）`、`tests/ # 1305 个用例`）已清理或明确标注为历史口径；④ 该 `--check` 已进 CI（§3.2 C-3） | 部分：`--check` 脚本存在但未进 CI；正文残留未清理；顶部实测 `1609 个用例` / `33,436 行` / `12,934 行`，与事实包 §2 的 `1601 / 33,383 / 12,532` 不一致【待确认】① |
| P-3 | 干净机首跑 | ≥3 台无 Python / 无 Node 的 Windows x64 干净机，各自完成：解压绿色版 → 启动 → `GET /api/v1/status` 200 → `register → claim → submit_result` 闭环；逐台留 OS 版本、踩坑、耗时记录；`scripts/coldstart_timing.py` 输出留档 | **未通过**：t43 报告 ⑤ 为「部分」，本机冷启 n=5 PASS（3.9s / 0.13s），外部 3 台待人工取证 |
| P-4 | Quickstart 计时 | n≥5 名陌生用户，各自从下载到完成一次闭环 ≤5 分钟（`docs/roadmap/05` M4 / T3.5），逐人计时记录；不得用作者本机计时替代 | 待取证 |
| P-5 | 可复现构建 | 同 commit 两次干净构建，`python scripts/repro_build.py compare-norm <dir_a> <dir_b>` 差异为 0；原始差异逐条可解释（PE 时间戳 / zip mtime）；`Get-FileHash dist\mio-taskhub-win64.zip -Algorithm SHA256` 记录并写进 Release 说明 | t42 报告（v0.6.0 / `d916a20`）已验证 807/807；**当前版本需重跑** |
| P-6 | changelog | `CHANGELOG.md` 含与本次发布版本对应的版本段落（非停留在 `## Unreleased`）；`git tag -l` 存在对应 tag 且指向发布 commit；段落内容含本周期改动摘要 | 部分：`CHANGELOG.md` 顶部为 `## Unreleased`，注释写明「版本号在发版提交（`chore(release)`）统一抬升并打 tag —— 本节保持 Unreleased」；tag 已有 `v0.6.1`，但 changelog 未收口到版本段落【待确认】⑤ |
| P-7 | docs/ 是否入库 | 见 §5.1 专项结论 | **未通过**（本次交付文档未跟踪） |
| P-8 | 冻结域入口是否统一下线 | ① 冻结清单 8 项对外宣称已改为「冻结 / 停投入」，README §8 / §5.6 / §5.7 标注到位；② `MIO_OBSERVER_AUTOSTART` / `MIO_IDEA_REVIEW_ENABLED` 默认 `off`（`frozen-scope-20261021.md` §本轮实际关掉的两个调度）；③ `MioDigestJob` / `ContractJob` 默认仍开且不被误列入冻结；④ 若执行 `feature-prune-inventory-20261028.md` 阶段 1，则 `web/src/components/Rail.jsx` 导航项由 13 项收敛为 9 项（隐藏 想法 / 定时 / 观测台 / 记忆，路由与组件保留、`#/ideas` 仍可访问），并留一行可回滚的数组改动记录 | 部分：①②③ 成立；阶段 1 是「修订提案，需确认后才执行」，是否执行【待确认】⑨ |
| P-9 | 前端独立错误码收尾 | ① `web/src/api.js::req()` 对结构化 `detail` 优先取 `detail.message`，回退顺序覆盖 `detail.detail` / `detail.error` / `JSON.stringify(detail)`；② `ApiError` 能暴露 `detail.code`（后端 `code` 位于 `detail` 内部，不是响应顶层）与 `detail.hint`；③ `gated()` 中 `e.error_code === 'policy_risk_high'` 的 409 确认分支不回归；④ 4 类门控 422 detail（`{message,quality}` / `{message,ratchet,hint}` / `{message,required_reads,missing,stale,hint}` / `{code,message,hint,files_changed,rejections,max_rejections}`）逐类在 UI 上显示可执行文案而非整段 JSON；⑤ 该改动属纯前端，按 §4.3 热更新并做前端 hash 校验，不重建 exe | **未通过**：现状 `ApiError` 第 29 行只读 `detail.error` 填 `error_code`；`req()` 第 43 行取 `detail?.detail \|\| detail?.error \|\| JSON.stringify(detail)`，未优先取 `detail.message`【待确认】⑩ |
| P-10 | CI 全绿 + 无已知红 | 发布 commit 的 `CI` workflow `conclusion=success` 且 `Run tests` 实际执行；`scripts/release_checklist.py --ci` 7 项全为「是」；任何 `-k` 排除项已在发布说明中显式列出 | t43 时点 CI 绿（run `38684036460`），但 `-k` 排除项与 C-1~C-6 未收口 |
| P-11 | 发布资产与 SHA256 | `mio_taskhub/release_assets.py::all_release_assets()` 的两项资产均存在且可下载；`scripts/check_installer_bundle.py` 返回 `[OK]`；静默装/卸端到端通过（§4.2） | 待逐项核验 |
| P-12 | 回滚材料就绪 | 部署前运行目录副本、`taskhub.db` 备份（含 `integrity_check=ok`）、新旧 exe sha256 三者齐备，且写入部署记录（格式参照 `docs/ops/DEPLOYMENT-20261029.md` §2） | 历史部署有此惯例；本次发布需重新生成 |

### 5.1 `docs/` 入库冲突的核实结论（专项）

任务书要求核实「`.gitignore` 第 31 行忽略 `docs/`，但 `docs/taskhub/` 又被称为正式 git 跟踪目录」。实测结论如下，**两句话分别对应不同时间的状态，不矛盾**：

| 核实动作 | 输出 | 结论 |
|---|---|---|
| 读 `.gitignore` 第 25–41 行 | 第 31 行是注释 `# docs/ 不再整体忽略：白名单纳入结论支撑文档...`；**实际生效规则在第 33 行 `docs/*`**，第 34–41 行为 `!docs/roadmap/`、`!docs/taskhub/`、`!docs/ops/`、`!docs/quality-audit.md` 等反选白名单 | 「`.gitignore:31 docs/` 整体忽略」是 `docs/roadmap/02-current-state-audit.md` §2 / F6 与 `05-execution-plan.md` T0.4 记录的**2026-10-10 历史状态**；G0 / T0.4 已改为白名单模式，注释还说明了「必须用 `docs/*` 而非 `docs/`，否则父目录被整体排除时子路径 `!` 反选失效」 |
| `git ls-files "docs/*" \| wc -l` | `258` | docs 下已有 258 个跟踪文件 |
| `git ls-files "docs/taskhub/*.md" \| wc -l` / `docs/roadmap/*` | `122` / `10` | `docs/taskhub/` 与 `docs/roadmap/` **确为正式 git 跟踪目录** |
| `git ls-files --error-unmatch docs/taskhub/frozen-scope-20261021.md docs/taskhub/build-release-checklist.md docs/taskhub/feature-prune-inventory-20261028.md docs/taskhub/diff-gate-grayscale-log.md` | 四行全部列出，exit 0 | 本章取证用的 4 份任务文档已入库 |
| `git check-ignore -v --no-index docs/taskhub/delivery-plan/phase3-devops.md docs/taskhub/brand-new-note.md docs/ops/new-note.md` | **无输出，exit 1** | 白名单生效，`docs/taskhub/` 下新建文件**不被忽略** |
| `git status --porcelain docs/taskhub/delivery-plan/ docs/taskhub/_delivery-fact-pack.md` | `?? docs/taskhub/_delivery-fact-pack.md` / `?? docs/taskhub/delivery-plan/` | 本次交付文档与事实包**未被忽略，但尚未 `git add`** |
| `git ls-files docs/taskhub/delivery-plan/` | 空 | `delivery-plan/` 下 5 份底稿（`phase1-requirements.md` / `phase1-architecture.md` / `phase2-frontend.md` / `phase2-backend.md` / `phase2-data.md`）全部未跟踪 |

**对发布前置项 P-7 的判据**：本次交付方案要进版本库，不需要改 `.gitignore`（已放开），但**必须显式执行** `git add docs/taskhub/_delivery-fact-pack.md docs/taskhub/delivery-plan/`；判据写为 `git ls-files docs/taskhub/delivery-plan/phase3-devops.md` 非空。是否将交付方案与事实包纳入发布 tag 的可追溯范围，由作者决定，标【待确认】⑪。本章不执行 `git add`（任务约束：不提交 git）。

---

## 6. 运行、配置三态与回滚

### 6.1 SQLite 备份与恢复

| 项 | 实现（`mio_taskhub/ops/backup.py`，经 `phase2-data.md` §6.3 复核） |
|---|---|
| 机制 | `SQLiteBackup` 守护线程，启动后延迟 60s，`DEFAULT_INTERVAL=3600`（每小时），用 SQLite Online Backup API `src_conn.backup(dst_conn)` 做一致性快照 |
| 路径 | `~/.mio_taskhub/backups/taskhub_YYYYMMDD_HHMMSS.db`；`MIO_TASKHUB_DB` 指向其他路径时为 `$MIO_TASKHUB_DB/../backups/` |
| 保留 | `_prune()` 保留 `keep_hourly=24 + keep_daily=7 = 31` 份（`max_keep=31`），按文件名倒序保留最近 N 个 |
| 对外 | `get_backup_status()` 返回 `{backup_dir, count, latest, total_bytes}`，`/readyz` 回显 |

**恢复流程现状**：`backup.py` 只有备份 + 清理 + 状态查询，**没有 restore 命令、没有验收脚本**（`phase2-data.md` §6.3 / §9 待确认 9 明确）。以下是建议步骤，非已验证流程，标【待确认】⑫：

```
1. 停 hub：走 §1.3 优雅停机，等 ThreadRegistry.stop_all() 与 engine.dispose() 完成
2. 处理 WAL 伴生文件：连接全部关闭后对 taskhub.db 做 checkpoint，或整体替换 taskhub.db / taskhub.db-wal / taskhub.db-shm 三者，避免备份 DB 与残留 WAL 不一致
3. 从 ~/.mio_taskhub/backups/ 选一份（按文件名时间），先 PRAGMA integrity_check 确认备份自身为 ok
4. 用备份替换 taskhub.db（保留当前 taskhub.db 为 .bak，不删除）
5. 重启 hub，验证：GET /healthz 200；GET /readyz 200 且 backups.count 正常；PRAGMA integrity_check = ok
6. 校验事件水位：SELECT id, type FROM event ORDER BY id DESC LIMIT 10; 与备份时刻预期对齐
```

### 6.2 配置三态切换的灰度观察运行要求

| 项 | 口径 |
|---|---|
| 开关 | `MIO_DIFF_GATE=off\|warn\|block`，默认 `warn`；`block` 才产生 422，非法值按 `warn` 处理（`diff_gate.py::gate_mode()`） |
| 状态口径 | **代码已落地、灰度观察未成立**；`docs/taskhub/diff-gate-grayscale-log.md` 起止仍为 `____-__-__`，M1~M5 全「待观察」，2026-10-21 记录「尚未产生真实拦截」 |
| 只读统计 | `curl -s http://127.0.0.1:48620/api/v1/board/diff-gate-stats \| python -m json.tool`；纯统计，不参与任何流程判定 |
| 唯一成功判据 | M2：**能指着一例「它拦下了一次我本会放过的假完成」** |
| 切 `block` 三前置 | ① ≥3 个真实 run 完成；② M3 误拒 = 0；③ M2 ≥1 例确认。三条全勾才切 `block` |
| 运行要求 | 观察期内不改 `mio_taskhub/` 基线（`docs/roadmap/04` §5.3 口径）；每次档位切换记录时间、旧值、新值、切换原因；M3 疑似误拒信号看 `suspected_false_positive_count`（`rejections>0` 且 `exit_code==0`）；M5 看 `by_reason` 分布，不该大量是 `collect_failed` |
| 回退方向 | 频繁误伤合法完成 → **回退为「仅标注」，不加严**（决策 §1）；操作为启动前置 `MIO_DIFF_GATE=off` 或 `MIO_DIFF_GATE=warn`，不回滚代码、不回滚数据、不回滚迁移 |

需注意 `mode=warn` 时 `blocked_run_count=0` 属正常（warn 档不产生拦截记录），不得据此判定门禁失效或宣称已生效。

### 6.3 回滚预案

| 触发条件 | 判定方式 | 执行步骤 | 预计耗时 | 验证方式 |
|---|---|---|---|---|
| 窄门禁误伤合法完成（M3 > 0 或人工确认误拒） | `board/diff-gate-stats` 的 `suspected_false_positive_count` > 0，或人工回看拦截上下文 | 启动前置 `export MIO_DIFF_GATE=off`（或 `warn`）→ 重启 hub。不改代码、不动数据、不回滚 `ALTER TABLE` | ≤1 分钟（配置级） | `GET /api/v1/board/diff-gate-stats` 回显 `mode` 为新值；同类提交恢复 200 |
| 纯前端热更新后 UI 异常 / 前端 hash 不一致 | §4.5 前端 hash 比对不一致，或用户报页面白屏、`TypeError: Failed to fetch` | 用部署前备份的 `web/dist` 覆盖 `_internal/web/dist/`（§4.3 路径）→ 用户刷新页面（`Cache-Control: no-store`）→ 不重启 hub、不重建 exe | ≤5 分钟 | 前端 hash 重新比对一致；`GET /` 返回新 index.html 引用的资产名；`GET /api/v1/tasks` 200 |
| 新 exe worker 反复退出 / API 5xx / 端口无监听 | `docs/ops/DEPLOYMENT-20261029.md` §8 记录的症状：`runtime.log` 出现 `exit stuck beyond 0s grace` / `restart backoff code=1073807364`；`netstat -ano \| grep 48620.*LISTENING` 无输出 | ① 停 TaskHub 进程；② 用部署前 `old-dist` 覆盖运行目录（`~/.mio_taskhub/deploy-*/old-dist/`）；③ 启动 `TaskHub.exe hub --worker`；④ **数据库不回滚**（迁移只加列，旧 exe 忽略新列） | 5–15 分钟 | `/api/v1/tasks` 200、任务数与素材数对齐、`PRAGMA integrity_check=ok`、exe sha256 与备份记录一致 |
| 误回滚到过旧版本 | `DEPLOYMENT-20261029.md` §8：第一次回滚选中 `5c0e4acd`（10-28 版），界面无变化；纠正后恢复 `30f1dd48` | 回滚前先核对备份目录的 exe sha256 与版本清单，按时间戳选最近一份正确的部署前副本，不按目录名猜测 | — | 恢复后比对 sha256 与 UI bundle `index-*.js` 名称 |
| 确认新 exe 写坏数据 | 人工确认数据损坏；`PRAGMA integrity_check` 非 `ok` | 按 §6.1 恢复流程从 31 份备份选一份替换；**需单独批准**，是最后手段 | ≥30 分钟（含停机） | §6.1 第 5–6 步全部通过 |
| 发布后出现确证 P0（数据损坏 / 状态不可信 / 不可恢复） | `docs/roadmap/05` §8 全局熔断 | 停止发布，从 Release 撤回资产，全部资源转止血 | — | 由作者判定，不走自动化 |

**回滚前置（无回滚不发布）**：每次产物替换前必须落到部署记录——① 旧运行目录完整副本；② `taskhub.db` 备份 + `integrity_check=ok` + sha256；③ 新旧 exe sha256 与字节数。历史格式见 `docs/ops/DEPLOYMENT-20261029.md` §2。

**回滚演练安排**：每次正式发布前，在隔离端口 `MIO_TASKHUB_PORT=48621` + 临时库 `MIO_TASKHUB_DB=<Windows 绝对路径>` 上预演一次「替换运行目录 → 回退到 old-dist → 恢复 200」；演练不碰 48620 真实库。演练记录与发布清单一同留档。频率按发布次数，一次一练，不设日历周期。

### 6.4 数据库变更与发布顺序

`migrations.py::run_migrations` 在每次启动经 `db.init_db()` 全量执行，靠 `inspect` 列存在性 + `CREATE INDEX IF NOT EXISTS` 实现幂等，**只加列、不删列、不重建表**；`_migrate_task_diff_gate` / `_migrate_run_diff_gate` 加列后存量行回填，并只在首次加列时把存量任务 `diff_gate_exempt` / `doc_gate_exempt` 置 1。旧 exe 读不到新列不影响运行（`DEPLOYMENT-20261029.md` §4 实证）。

发布顺序固定为：

```
1. 停 hub（优雅停机）→ 2. 备份 DB + WAL checkpoint + integrity_check
→ 3. 备份运行目录（old-dist）→ 4. 替换产物（.py 改动重建 exe / 纯前端只覆盖 web/dist）
→ 5. 启动 hub（迁移自动执行，向前兼容）→ 6. 验证 /healthz /readyz / 核心端点 / 前端 hash
→ 7. 观察一个真实周期 → 8. 确认无问题后才清理 old-dist（禁止发布当天清理）
```

禁止先跑破坏性 DDL，禁止在同一发布里既改表结构又做不兼容的旧版回退。窄门禁的回退维度是环境变量而不是迁移：`MIO_DIFF_GATE=off` 后判据不生效，列留在库里不影响旧版。

### 6.5 发布策略选择

采用「**备份先行 + 单机整体替换 + 分层回滚**」。被否决的备选：

| 备选 | 否决理由 |
|---|---|
| 蓝绿发布 | 需要第二套运行目录 + 第二份状态；hub 用命名 mutex 保证单实例、单端口 48620、单 SQLite 库，没有第二实例可切；维护两份 `~/.mio_taskhub` 会引入数据分叉 |
| 滚动发布 / 金丝雀按流量或按用户放量 | 本地单用户、无租户、无网关、无流量切分维度；限流桶 key 仅 client IP（`middleware.py` F11），无法按用户/租户分组；Star / Trending / fork 数不得作为任何 Gate 判据（`docs/roadmap/05` §9） |
| 数据库与代码同一次原子切换 | `migrations.py` 已做幂等只加列，旧版可忽略新列；拆开顺序反而能保留「停 → 备份 → 迁移 → 验证」的可回退窗口 |

灰度维度在本系统里只有一个：`MIO_DIFF_GATE` 三档（§6.2），放量节奏由「一个真实使用周期 + 3 个真实 run + M2 一例」决定，不按百分比放量。

---

## 7. 可观测与应急（现状边界）

| 维度 | 现状 | 本期动作 |
|---|---|---|
| 日志 | `console.log`（`docs/roadmap/02` §6.3 记 36 MB / ERROR 1758 / WARNING 419）、`runtime.log`（supervisor/worker 起停）、`RequestIDMiddleware` 注入 `X-Request-ID`、5xx 记 `logger.error("request_error")` 与 `logger.exception`；`/api/v1/logs` 环形缓冲 1000 条，可按 level / logger / limit 过滤 | 保留现状；F3 结构化日志丢 `path` / `duration_ms` 已登记延后，不投入 |
| 指标 | `/metrics` Prometheus 文本端点、`/dashboard`、`/api/v1/alerts`；`board/diff-gate-stats` 提供 M1~M5 代理指标 | 保留现状；**该域已冻结**，不新增 SLO / tracing / 告警闭环 |
| 链路追踪 | 全链路 `X-Request-ID`；`Event.seq` 单调增量；`TaskEvent` 状态迁移留痕；F8 otel 未生效，属冻结 | 不投入 |
| 告警分级 | SLO 目标值（HTTP 99.9% / 任务 99.0% / MCP 99.5% / P50<100ms / P99<2000ms / 5xx<0.1%）为**目标非实测**，且该域已冻结；`packaging/alerts.yml` 24 条规则为冻结保留内容，不作现行验收指标 | 不设新告警分级；不作为发布判据 |
| 值班响应 | 本地单用户工具，无值班轮值、无 on-call、无升级链 | 不引入；故障响应由作者按 §6.3 分层回滚自行处置 |
| 故障复盘 | 已有 `docs/ops/DEPLOYMENT-20261029.md` §8 的「失败、误回滚与纠正」记录格式，以及 `docs/taskhub/diff-gate-grayscale-log.md` 的观察记录格式 | 每次发布/回滚按同格式留一条记录：时间线、触发条件、判定依据、执行步骤、验证结果、数据是否回滚 |

---

## 8. 安全与成本

| 项 | 现状 | 发布判据 / 处置 |
|---|---|---|
| 服务鉴权 | 默认 loopback `127.0.0.1:48620`；`MIO_TASKHUB_TOKEN` 单 Bearer（仅 `/api/*`）；`--auth` 未设 token 时用 `secrets.token_urlsafe(24)` 自动生成并打印；WebSocket 单独校验，失败 `close(4401)`；非 loopback 且无 token 直接 `SystemExit`，除非显式 `MIO_TASKHUB_ALLOW_INSECURE_NETWORK` | token 只经环境变量或启动参数注入，**不进代码库、不写进 README 明文**；单 token 无过期、无权限体系、无轮换，属已知限制（本地单用户定位），登记延后 |
| 发布密钥 | `packaging/release.ps1:55-68` 依次取 `GH_TOKEN` → `GITHUB_TOKEN` → `git credential fill`（不落盘）；`.github/workflows/branch-protection.yml` 用 `secrets.BRANCH_PROTECTION_TOKEN` | 全部放 GitHub Secrets 或环境变量；判据：仓库内 `git grep` 无 PAT / token 字面值；`-DryRun` 不需要 token，可先预演 |
| 证书 / TLS | 默认 loopback 无 TLS，不签发也不分发证书 | 本期不发证书；对外暴露须显式 `--host` + `--auth`，`net_guard` 拒绝非 loopback 无 token 启动 |
| 依赖完整性 | `uv.lock`（含每个包 sdist / wheel sha256）与 `web/package-lock.json` 已入库 | 见 §2.3 D-1/D-2；本期不引入独立漏洞扫描器（会新增工具链且与 8 项冻结「停投入」冲突） |
| 大模型 API Key | 门控链路不调用大模型，判定由确定性状态机与 sha256 指纹完成；`test` 只是文档 kind，系统不跑测试、不做静态分析（`phase1-architecture.md` §5、`phase2-backend.md` §6.3）。`api/idea_templates.py` 有用户显式触发的 LLM 调用，属冻结域且经 mio-intelligence 能力代理 | **本项目不自持 LLM API Key，轮换与配额告警方案本期不适用**；若将来解冻引入，须先补：Key 托管方式（Secret / 环境变量）、轮换周期、单次与单任务成本上限、配额阈值与告警渠道，阈值未定前不写数字、不进主链路 |
| 成本口径 | 零云依赖、单机、无外部服务计费、无按 token 计费；产物不入库（`dist*/` 被忽略） | 无云资源预算告警可设；人力成本假设与区间见 §10，不含外部评审等待、审批墙钟、分发运营 |

---

## 9. 编排与部署形态：明确「不做」清单

| 不做项 | 依据 | 触发条件（当前均不成立） |
|---|---|---|
| 不引入 Prometheus / SLO / tracing | 冻结清单第 3 项（`frozen-scope-20261021.md`），单用户本地工具无人看指标；`observability/` 代码保留、停投入 | 出现第二个真实用户 → 重估 |
| 不做容器化编排（Compose / K8s / 伸缩 / 资源配额） | 本地单用户、单 EXE 免安装、零外部依赖；`Dockerfile` 仅作为可选源码运行路径存在，README 部署栏列「Docker 多阶段」。引入编排需 Docker 运行时、无图形安装/卸载，覆盖不到目标用户 | 三条触发条件任一成立才重估整体形态 |
| 不做多租户部署 | 事实包 §1 明确非目标；无 RBAC / 无角色表 / 无权限体系 | 多租户成立 → 迁 PostgreSQL |
| 不做跨进程写 | 单进程模块化单体，WS 广播是进程内 `asyncio.run`（无 broker / 无 IPC / 无第二份状态） | 跨进程写成立 → 迁 PostgreSQL |
| 不迁移 PostgreSQL | `docs/roadmap/03` §6.1 N6、`phase2-data.md` §7、`phase1-architecture.md` §3 | **并发 >1000 / 跨进程写 / 多租户**任一成立；当前 1–5 agents 单进程，不成立 |

其他不做：不合并 `advance` / `move` 两个状态机入口（冻结清单第 8 项，本轮不合并）；不把冻结项写成待开发功能；不把 Star / Trending / fork / 转发数写进任何 Gate 判据；不新增前端测试方法体系（P-9 只要求错误码收尾与 hash 校验）。

`Dockerfile` 现状（`FROM node:20-alpine` → `python:3.12-slim`，`EXPOSE 48620`，`HEALTHCHECK curl /healthz`，`CMD uvicorn --host 0.0.0.0 --port 48620`）与本章「主打 Windows 单 EXE」不冲突，但第 5 行 `npm install --frozen-lockfile 2>/dev/null || npm install` 与第 21 行 `pip install --no-cache-dir -e ".[dev]" 2>/dev/null || pip install --no-cache-dir .` 都沿用静默降级写法；是否在本期修或把 Docker 路径从发布面移除，标【待确认】⑧。

---

## 10. 任务卡与依赖

**字段规范**沿用 `docs/roadmap/05-execution-plan.md` §11：`标题 / 前置依赖(depends_on) / 涉及模块(files) / 验收命令 / 完成定义(DoD) / 风险 / 工期 / 上游映射`。DoD 必须绑定一条可复现命令；条件性任务打 `labels += ["conditional"]`。

**人力假设**：单人全职（约 5 工作日/周）+ Agent 辅助、熟悉本仓库、含设计/实现/测试/取证；不含外部评审等待、审批墙钟、分发运营。本地单用户定位不变，冻结清单不解冻。工期只给区间与依赖，不给日历日期。

| ID | 标题 | depends_on | files | 验收命令 / DoD | 风险 | 工期区间 |
|---|---|---|---|---|---|---|
| T7-01 | 修 CI 安装步与测试隔离 | 无 | `.github/workflows/test.yml` | 安装步改为 §2.2 三行；`env` 改 `MIO_TEST_DB_PATH`；测试命令加 `--basetemp`。DoD：CI `Run tests` 实际执行且 `0 failed`，日志无 `D:\dev\null`，`python -m pytest -q --basetemp=...` 本地 0 failed | 首次暴露更多 flaky；`-k` 排除项需同步登记 | 0.5–1 天 |
| T7-02 | README 数字口径冻结与 CI 校验 | T7-01 | `scripts/gen_readme_stats.py`、`README.md`、`.github/workflows/test.yml` | `python scripts/gen_readme_stats.py --check` 返回 0 且该步进 CI；正文残留 `40 / 119 / 1305` 清理或标注历史口径；顶部 7 字段与脚本派生值一致 | 脚本只统计 `git ls-files`，提交新测试文件后数字会变；正文残留不在 `--check` 覆盖范围内（子串匹配只找顶部加粗格式） | 0.5–1 天 |
| T7-03 | 打包机路径去硬编码 | 无 | `packaging/build.ps1` | 第 66 行改用 `$root\.venv\Scripts\python.exe`；第 180 行日志路径改用 `$env:USERPROFILE`。DoD：在仓库根非 `E:\work\code\agent-dev\mio-taskhub` 的副本上跑通 `build.ps1 -Quick`，退出码 0 | 改构建脚本需按 `build-release-checklist.md` 重跑干净重建 7 步 | 0.5–1 天 |
| T7-04 | 依赖锁定进入安装路径 | T7-01 | `pyproject.toml`、`uv.lock`、`.github/workflows/test.yml`、`Dockerfile` | `uv lock --check`（或 `uv sync --locked --extra dev`）返回 0；安装命令带 hash 校验；D-1/D-2/D-6 三条有明确处置（修或书面移除） | 更换安装方式可能触发 Windows / py3.12 下 wheel 可用性问题；需全量 pytest 回归 | 1–2 天（含回归） |
| T7-05 | 版本号四处一致性校验 | T7-02 | `pyproject.toml`、`mio_taskhub/version.py`、`packaging/build.ps1`、`README.md` | 新增校验命令比较四处版本，不一致 exit 1；`build.ps1 -Version` 后跑该命令通过 | `build.ps1 -Version` 只覆写 `version.py`，不同步 `pyproject.toml` 会立刻漂移 | 0.5–1 天 |
| T7-06 | 发布前置 P-3/P-4 人工取证 | 无（外部依赖） | `scripts/coldstart_timing.py`、`docs/roadmap/t35-coldstart-report.md` | ≥3 台干净机完成闭环并留 OS / 耗时 / 踩坑记录；n≥5 陌生用户 Quickstart ≤5 分钟计时记录。DoD：`release_checklist.py --ci` 第 ⑤ 项由「部分」变「是」 | 外部机器不可控、不能由 Agent 代跑；沙箱/杀软/端口占用会导致失败率高 → 按 `docs/roadmap/05` G3 处置返工 | 1–2 周（墙钟依赖外部机器，非纯工时） |
| T7-07 | 可复现构建与 SHA256 重跑 | T7-03、T7-04 | `scripts/repro_build.py`、`dist/`、`CHANGELOG.md` | `compare-norm` 差异 0、原始差异逐条可解释；`Get-FileHash` 记录并写进 Release 说明；P-5/P-6 判据满足 | safe-delete 钩子可致「Build complete」假成功；构建后必须核对 exe mtime | 1–2 天 |
| T7-08 | 交付文档入库与发布判据收口 | 无 | `docs/taskhub/_delivery-fact-pack.md`、`docs/taskhub/delivery-plan/` | `git ls-files docs/taskhub/delivery-plan/phase3-devops.md` 非空；`git check-ignore` 对交付文档无输出。DoD：P-7 判据满足 | 白名单已生效，只需 `git add`；是否纳入发布 tag 由作者决定【待确认】⑪ | 0.5 天 |
| T7-09 | 冻结域入口统一下线核验 | 无 | `web/src/components/Rail.jsx`、`README.md`、`feature-prune-inventory-20261028.md` | P-8 四条判据逐条留证；若执行阶段 1，则导航 13→9 且留一行可回滚的数组改动 | 阶段 1 是修订提案，需先确认是否执行【待确认】⑨；纯前端改动走 §4.3 热更新 | 0.5–1 天 |
| T7-10 | 前端独立错误码收尾 | 无（需前端/后端确认字段映射） | `web/src/api.js` | P-9 五条判据逐条验证；4 类门控 422 逐类在 UI 显示 `message` / `hint`；`gated()` 的 409 分支不回归；按 §4.3 覆盖并做前端 hash 校验 | 后端 4 类 detail 结构不统一、`code` 在 detail 内部；统一字段映射需与前端/后端工程师确认【待确认】⑩ | 1–2 天 |
| T7-11 | SQLite 恢复流程脚本化（条件性） | T7-07（备份机制不需改） | `mio_taskhub/ops/backup.py` 或新增 `scripts/restore_backup.py`、运维手册 | §6.1 六步有一条可复现命令；在临时库上演练一次恢复并校验 `Event.seq` 水位 | `restore` 现无实现；WAL 伴生文件处理未验证；本期是否补脚本【待确认】⑫ | 1–3 天（`conditional`） |
| T7-12 | 回滚演练与发布门全绿 | T7-01 ~ T7-10 | 部署记录、`scripts/release_checklist.py` | 隔离端口 48621 + 临时库预演「替换 → 回退 → 200」；`release_checklist.py --ci` 7 项全为「是」；§11 上线检查单全勾 | 任一发布门项不达标即阻断发布，不可「带病发布」 | 1–2 天（核验） |

**关键路径**：T7-01 → T7-02 → T7-05；T7-03 → T7-04 → T7-07；T7-06 与 T7-12 依赖外部取证墙钟，与其余任务并行。T7-11 为条件性任务，前置结论未出不得进 `ready`。

---

## 11. 上线检查单

**上线前**

- [ ] `git ls-files LICENSE` 输出 `LICENSE`，内容含 `MIT License`（P-1）
- [ ] `python scripts/gen_readme_stats.py --check` 返回 0，且该步已进 CI（P-2 / T7-02）
- [ ] 发布 commit 的 CI `conclusion=success`，`Run tests` 步骤实际执行，0 failed；`-k` 排除项已书面列出（P-10）
- [ ] `MIO_TEST_DB_PATH` + `--basetemp` 隔离生效，本地全量 pytest 无 teardown 被杀（T7-01）
- [ ] 同 commit 两次干净构建 `compare-norm` 差异 0；原始差异逐条可解释（P-5）
- [ ] `Get-FileHash dist\mio-taskhub-win64.zip -Algorithm SHA256` 已记录（P-5）
- [ ] `scripts/check_installer_bundle.py` 返回 `[OK]`；静默装/卸端到端通过（P-11）
- [ ] `release_assets.py::all_release_assets()` 两项资产齐备（P-11）
- [ ] `CHANGELOG.md` 版本段落已收口，`git tag -l` 对应 tag 存在（P-6）
- [ ] `git ls-files docs/taskhub/delivery-plan/phase3-devops.md` 非空（P-7 / T7-08）
- [ ] P-8 冻结域入口四条判据留证（T7-09）
- [ ] P-9 前端错误码五条判据留证，前端 hash 比对一致（T7-10）
- [ ] ≥3 台干净机首跑记录 + n≥5 Quickstart ≤5 分钟计时记录齐备（P-3 / P-4）
- [ ] `scripts/release_checklist.py --ci` 7 项全为「是」（P-10）
- [ ] 部署前三件套齐备：旧运行目录副本、`taskhub.db` 备份（`integrity_check=ok`）、新旧 exe sha256（§6.3 / P-12）
- [ ] 隔离端口 48621 + 临时库完成一次回滚预演（T7-12）

**上线中**

- [ ] 按 §6.4 固定顺序执行：停 hub（优雅停机）→ 备份 → 备份运行目录 → 替换产物 → 启动 → 验证
- [ ] 纯前端改动只覆盖 `_internal/web/dist/`，不跑 PyInstaller（§4.3）
- [ ] `.py` 改动走完整 `build.ps1`，第 5 步分发文件与 `workbuddy/` 已复制（§4.2）
- [ ] 构建后核对 `TaskHub.exe` 的 mtime = 本次构建时刻，排除 safe-delete 假成功（§4.4）
- [ ] 启动后 `GET /healthz` 200、`GET /readyz` 200 且 `backups.count` 正常、`GET /api/v1/tasks` 200
- [ ] 前端 hash 比对一致（§4.5）
- [ ] 新增 `.py` 逻辑做 PYZ / `co_consts` + `co_names` + `co_varnames` 反查（§4.5）
- [ ] `MIO_DIFF_GATE` 档位与发布说明一致；默认 `warn`，切 `block` 前三条前置已勾（§6.2）
- [ ] 不清理 `old-dist`，不删除部署前备份

**上线后**

- [ ] 记录部署时间线、exe sha256、DB 备份 sha256、迁移是否执行、验证结果（格式同 `docs/ops/DEPLOYMENT-20261029.md`）
- [ ] 灰度观察：填 `docs/taskhub/diff-gate-grayscale-log.md` 起止与 M1~M5；M3 误拒必须为 0；唯一成功判据 M2 未成立前不得对外宣称门禁效应
- [ ] 观察期内不改 `mio_taskhub/` 基线；每次档位切换留时间 / 旧值 / 新值 / 原因
- [ ] 回滚触发条件命中时按 §6.3 对应行执行，并记录复盘（§7）
- [ ] 外部材料只引用 G2 结论允许的表述；禁用词「提升质量」「形成闭环」「生产级可靠」「首创/突破」在结论出来前不得出现

---

## 12. 【待确认】

1. **README 用例数口径**：实测顶部为 `1609 个用例` / 后端 `33,436 行` / 前端 `12,934 行`，事实包 §2（取证日 2026-11-06）为 `1601 / 33,383 / 12,532`，正文历史残留为 `119 个端点 / 40 个 MCP 工具 / 1305 个用例`。`gen_readme_stats.py` 只统计 `git ls-files` 内的文件且 `check()` 只匹配顶部加粗格式，正文残留不受 `--check` 覆盖。需确认以哪个快照为发布冻结值，以及正文残留是否清理。
2. **覆盖率 80.0% 是否设为 CI 门禁阈值**：该数值是 G0 本地冻结基线（`04-review-20261014.md` 的 `g0_manifest.json`），CI 当前不跑覆盖率。未定阈值前不得对外宣称 CI 覆盖率达标。
3. **`-k "not test_two_agents_race and not test_webhook_guard"` 排除项是否本期修复**：4 个 `webhook_guard` 用例引用已改名方法（`CronEngine._should_skip_webhook_generation`），与「发布门无已知红」冲突。修复用例还是书面列为「已知红例外」需确认。
4. **`uv.lock` 是否作为 CI / 打包机唯一依赖源**：`uv.lock` 与 `web/package-lock.json` 已入库但当前安装绕过；改用 `uv sync --locked` / `--require-hashes` 需 Windows py3.12 回归。
5. **CHANGELOG 收口方式**：`CHANGELOG.md` 顶部为 `## Unreleased`，注释规定「版本号在发版提交统一抬升并打 tag —— 本节保持 Unreleased」，而 `git tag -l` 已有 `v0.6.1`。需确认是新增 `## v0.6.1` 段落还是保持 Unreleased 并在 Release Notes 里写版本。
6. **`BRANCH_PROTECTION_TOKEN` secret 是否已配置、required check 名是否稳定**：`branch-protection.yml` 依赖该 secret 且 `--require "test (3.12)"` 绑定 matrix 命名；改 matrix 会让审计失效。
7. **GitHub Actions 是否加人工审批卡点 / 制品归档 / SHA256 上传**：当前无卡点，Release 由人工执行 `packaging/release.ps1`（支持 `-DryRun`）。本地单用户分发是否需要额外卡点需确认。
8. **Docker 路径是否保留在发布面**：`Dockerfile:5` 使用 `npm install --frozen-lockfile`（非 npm 参数）与 `2>/dev/null || ...` 静默降级；README 部署栏列「Docker 多阶段」。修 `npm ci` 还是从发布面移除该路径需确认。
9. **`feature-prune-inventory-20261028.md` 阶段 1 是否执行**：该清单是「修订提案，需确认后才执行」；若执行则 `Rail.jsx` 导航 13→9（隐藏 想法 / 定时 / 观测台 / 记忆，路由保留）。
10. **前端错误体字段映射**：`web/src/api.js` 的 `ApiError` 只读 `detail.error`、`req()` 未优先取 `detail.message`；后端 4 类门控 422 detail 结构不统一且 `code` 位于 `detail` 内部，`policy_risk_high` 409 的 detail 结构需与前端/后端工程师共同确认后再收口。是否收敛为单一错误 schema 亦在 `phase2-backend.md` §9 / §11 列为【待确认】。
11. **交付方案文档是否纳入发布 tag 的可追溯范围**：`docs/taskhub/delivery-plan/` 与 `_delivery-fact-pack.md` 当前不被 `.gitignore` 忽略但未 `git add`；是否随发布 commit 入库由作者决定。
12. **SQLite 恢复流程是否本期脚本化**：`ops/backup.py` 无 restore 实现，§6.1 六步为建议步骤、未验证；WAL 伴生文件处理与 `Event.seq` 水位校验命令未定义。补脚本属条件性任务 T7-11。

---

（本章交付完毕。未修改任何源码或配置，未执行构建，未执行 `git add` / 提交；`git` 命令仅用于只读核实 `.gitignore` 与 `git ls-files` / `git check-ignore` / `git tag -l` 状态。全部【待确认】项共 12 条，均给出可核对的取证位置，未编造取值。）
