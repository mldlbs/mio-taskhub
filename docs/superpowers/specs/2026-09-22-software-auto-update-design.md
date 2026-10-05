# mio-taskhub 软件自动更新 — 设计文档

- 日期：2026-09-22
- 状态：Draft（待评审）
- 范围：**只更新程序本体**（`mio-taskhub.exe` + `_internal/` + 内置 `web/dist`），即绿色版发布包整体替换；不含 `~/.mio_taskhub` 用户数据，也不含 agent-dev 下其它项目仓库。

## 1. 背景与目标

现状：分发物是 `mio-taskhub-绿色版.zip`（PyInstaller onedir），入口 `mio-taskhub.exe [hub|mcp|widget]`，托盘只有"打开面板/退出"，**无任何版本/更新机制**。远程仓库 `github.com/mldlbs/mio-taskhub`（public）。

目标：
- 启动后静默检查 GitHub Releases 是否有新版本；
- 有新版本时在托盘 + Web 提示，用户点一下即可完成下载、校验、替换、重启；
- 更新过程**可恢复、可回滚**，失败不把可用程序弄坏；
- 建立**发版契约**（tag + 资产 + manifest），使"可更新的东西"可被稳定产出。

## 2. 非目标

- 不做代码签名（成本高）；仅 https + sha256。
- 不更新用户数据 / 数据库 / 日志 / 偏好。
- 不更新 MCP server（venv 源码形态，由开发流程管理）。
- 不做增量差分更新（每次整包替换）。
- 不做跨代迁移（低于 `min_supported` 只提示手动更新）。

## 3. 关键约定（定死）

1. **不是"原子替换"**，而是**「同卷 rename + 可恢复更新事务」**：
   ```
   install → rename → install.bak-<from_version>
   staging → rename → install
   成功确认后 删除 install.bak-<from_version>
   失败： install.bak-<from_version> → rename → install
   ```
   两次 rename 之间存在窗口（install 短暂不存在）；靠**启动时残留恢复** + **版本化备份**兜底。
2. **`.bak` 不被覆盖**：备份目录命名为 `install.bak-<from_version>`（含来源版本）。创建前若同名已存在则先删除（仅覆盖"同一来源版本"的备份，绝不破坏其它版本的可回滚副本）；并轮换清理旧备份（保留最近 2 份）。
3. **成功判据 = 新 Hub 健康启动**，不是文件替换成功：
   新 Hub 成功启动后写 sentinel `~/.mio_taskhub/update/runtime.json`（`{version, pid, started_at, port}`）；updater 轮询确认 `version == 目标版本 且 started_at 晚于本次替换`，否则视为失败 → 回滚。
4. **不假设 PID 消失=文件锁释放**：updater 对 rename/replace 做退避重试（≤30s），并显式探测锁；不要只依赖"等 PID 退出"。

## 4. 架构总览

```
mio-taskhub.exe (run.py 统一入口)
   │
   ├─ hub | mcp | widget | apply-update        ← 新增 apply-update 模式
   │
   └─ hub 进程内
        UpdateService（单例 + 状态机）
          ├─ 启动后延迟 20s 首次检查
          └─ 此后每 6h 检查（可配）
                  │
                  ▼
        GitHub Releases latest.json
                  │ 比对 __version__
                  ▼
               有新版本
          ┌───────┴────────┐
          ▼                ▼
     tray 通知        Web 顶栏 banner
          │
          ├─ POST /update/download → ~/.mio_taskhub/updates/<ver>.zip
          │
          └─ POST /update/apply → 独立进程:
                mio-taskhub.exe --apply-update <zip> --sha256 <hex> --pid <hub_pid> --target <install_dir>
                  ├─ 等待 Hub PID 退出（≤60s）
                  ├─ staging 解压
                  ├─ 校验目录结构
                  ├─ SHA-256 校验
                  ├─ install → install.bak-<from_version>
                  ├─ staging → install
                  ├─ 启动新版本 Hub
                  ├─ 等 sentinel 健康确认
                  │      ├─ 成功 → 删除 install.bak-<from_version>
                  │      └─ 失败 → 杀新进程 → 回滚 → 重启旧版
                  └─ 任一步失败 → 回滚 install.bak-<from_version> → install
```

## 5. 组件边界

| 模块 | 职责 |
|------|------|
| `mio_taskhub/version.py` | 单一版本源 `__version__`；`install_dir()`（frozen→exe 目录，否则仓库根） |
| `update/manifest.py` | `UpdateManifest` 契约解析/校验 + 语义化版本比较 `is_newer()` |
| `update/source.py` | `GitHubReleaseSource`：拉 `latest.json`（固定 CDN URL，含缺失回退） |
| `update/service.py` | 单例状态机、定时检查、进度事件、忽略版本持久化 |
| `update/downloader.py` | 流式下载、边下边算 sha256、`.part`→校验→rename、重试 |
| `update/apply.py` | 独立更新事务：等 PID、锁重试、staging、校验、备份、替换、重启、健康确认、回滚 |
| `api/update.py` | `GET /update/status`、`POST /update/{check,download,apply,dismiss}` |
| `run_hub.py` | 托盘菜单"检查更新/立即更新" + 有新版通知（pystray notify） |
| `web/` | 顶栏更新 badge/banner + 一键更新（复用现有 WS 事件） |
| `packaging/release.ps1` | 构建、sha256、生成 `latest.json`、`gh release create` |
| `packaging/build.ps1` | 注入版本号；产出 `dist/mio-taskhub-win64.zip` |

## 6. Manifest 契约（`latest.json`，`schema:1`）

```json
{
  "schema": 1,
  "version": "0.4.0",
  "channel": "stable",
  "released_at": "2026-09-22T10:00:00Z",
  "min_supported": "0.3.0",
  "mandatory": false,
  "notes": "## 变更\n- …",
  "assets": [
    {
      "os": "windows",
      "arch": "x64",
      "url": "https://github.com/mldlbs/mio-taskhub/releases/download/v0.4.0/mio-taskhub-win64.zip",
      "sha256": "<64 hex>",
      "size": 12345678,
      "format": "zip"
    }
  ]
}
```

**获取**：固定 URL `https://github.com/mldlbs/mio-taskhub/releases/latest/download/latest.json`
（public，免 token，走 CDN，不消耗 API 限额）。
**回退**：该资产缺失时，请求 `releases/latest`（API）解析 `tag_name`（`vX.Y.Z`）并匹配
`mio-taskhub-win64.zip` 资产；此路径无 sha256，仅能弱校验（zip 可解压），需在 UI 标注"弱校验"。

**校验（客户端）**：`schema==1`；必填字段齐全；`sha256` 为 64 位 hex；`url` 为 https 且域名白名单；
任一不满足 → manifest 无效，不更新。

## 7. 版本比较规则

- 语义化 `MAJOR.MINOR.PATCH`（可选 `-rc.N`）。
- `is_newer(current, latest)`：解析为可比较 tuple；**预发布 < 同号正式**；**解析失败一律判为"不更新"（fail-safe）**。
- `channel`：默认只处理 `stable`；预发布通道由配置开关（默认关）。
- `min_supported`：`current < min_supported` → 状态置为 **`needs_manual`**（提示手动更新，不自动执行），避免跨代不兼容。

## 8. 安全边界

- 仅接受 `https`；最终域白名单：`github.com`、`*.githubusercontent.com`。
- **强制 sha256 校验**（资产级）；不匹配 → 丢弃并重下（≤3 次）。
- 不做代码签名（非目标）；不做 exe 内嵌签名校验。
- 不读取任何远端可执行脚本；只解压 zip 并替换程序目录。

## 9. 状态机（UpdateService）

```
idle ──check──▶ checking ──┬─▶ up_to_date
                           ├─▶ needs_manual   (current < min_supported)
                           ├─▶ available ──dismiss──▶ dismissed
                           └─▶ check_failed   (离线/超时/限流，静默)
available ──download──▶ downloading ──▶ ready ──apply──▶ applying ──▶ done | failed
```

## 10. 数据流

1. hub 启动 → 延迟 20s → `check()`；其后每 6h（`MIO_UPDATE_INTERVAL_H`）。
2. `source.fetch()` → manifest → `is_newer()`；有新版 → WS 广播 `update_available` + 托盘通知。
3. `download()`：流式写 `updates/<ver>.zip.part`，边下边算 sha256，进度走 WS；校验通过 → rename `.zip`。
4. `apply()`：spawn `mio-taskhub.exe --apply-update <zip> --sha256 <hex> --pid <hub_pid> --target <install_dir>`（detached），hub **优雅退出**。触发时机：**用户一键更新**；若存在 running 任务，UI 提示"将中断 N 个任务"，由用户确认后仍可执行。

## 11. 更新事务（`--apply-update` 详细步骤）

```
1  解析参数：zip / sha256 / hub_pid / target
2  等待 hub_pid 退出（≤60s）；超时 → 中止（不替换），写 apply.log
3  探测 install 目录文件锁（试 rename 一个探针文件）；锁定 → 退避重试 ≤30s
4  staging = <target>.staging-<ver>；解压 zip 到 staging
5  校验 staging 结构（含 mio-taskhub.exe、_internal/web/dist/index.html 等关键项）
6  重算 zip 的 sha256，与传入的 --sha256 比对（防下载后被篡改）；不符 → 中止回滚
7  from_version = 当前 install 里 version.py 解析值（或 runtime.json）
8  backup = <target>.bak-<from_version>；若存在先删（仅同来源版本）
9  rename install → backup
10 rename staging → install            （失败 → 回滚 backup→install）
11 启动新 Hub（mio-taskhub.exe，detached）
12 轮询 sentinel（≤90s）：version==目标 且 started_at 晚于本次替换？
      是 → 删除 backup，写 apply.log(success)
      否 → 杀新进程 → 回滚 backup→install → 重启旧版 → 写 apply.log(failure)
13 任一步异常 → 尽最大努力回滚；失败则保留 staging/backup 并写人工介入标记
```

**日志**：`~/.mio_taskhub/update/apply.log`（追加，含时间戳、各步结果、错误）。

## 12. 异常与恢复矩阵

| 场景 | 处理 |
|------|------|
| 检查失败（离线/超时/限流） | 静默记录，下次再试，不打扰 |
| manifest 无效 | 不更新 + 日志 |
| 磁盘空间不足 | 下载前预检 `size` → 明确提示 |
| 安装目录不可写 | 写探针预检 → 提示"需手动/提权" |
| 下载中断 / sha256 不符 | 删包重试 ≤3 → `failed` |
| 主进程退出超时 60s | **不替换**，报错退出，保持原样 |
| rename 被文件锁 | 退避重试 ≤30s；仍失败 → 回滚 |
| 新 Hub 90s 未写 sentinel | 杀新进程 → 回滚 → 重启旧版 |
| updater 自身被杀 | 下次 hub 启动做**残留事务恢复** |
| 回滚也失败 | 保留 `staging`+`backup`，写 `apply.log`，提示人工介入 |

## 13. 残留事务恢复（hub 启动时）

- 发现 `install.bak-*` 且 `install` 缺失/不完整 → 将最新 backup 恢复为 install；
- 发现 `*.staging-*` → 清理；
- 记录到 `apply.log`，并在 Web/tray 提示"上次更新异常已恢复"。

## 14. API 与 UI / Tray

**REST（`/api/v1`）**
- `GET /update/status` → `{current, latest, state, notes, mandatory, min_supported, asset_sha256, weak_verify}`
- `POST /update/check` → 立即检查
- `POST /update/download` → 触发下载（进度走 WS）
- `POST /update/apply` → 触发 updater 并让 hub 优雅退出
- `POST /update/dismiss` → 忽略此版本（持久化）

**WS 事件**：`update_available` / `update_progress` / `update_ready` / `update_failed`

**Tray**：菜单加"检查更新""立即更新（vX.Y.Z）"；有新版时 `icon.notify()`

**Web**：顶栏 badge/banner；`available` → "立即更新"；`downloading` → 进度条；`needs_manual` → 提示手动。

## 15. 发版契约（`packaging/release.ps1`）

1. 定版本（写 `version.py`，或用 `-Version` 覆盖）；
2. 调 `build.ps1` 产出 `dist/mio-taskhub-win64.zip`（含 exe + _internal + web/dist）；
3. 计算 `sha256` 与 `size`；
4. 生成 `latest.json`（§6 契约）；
5. `gh release create vX.Y.Z mio-taskhub-win64.zip latest.json --notes-file <notes.md>`。

`build.ps1` 的改动：打包前把版本写入 `mio_taskhub/version.py`；资产名统一为 `mio-taskhub-win64.zip`（替换现有"绿色版.zip"命名或同时产出）。

## 16. 配置开关

| 变量 | 默认 | 作用 |
|------|------|------|
| `MIO_UPDATE_DISABLED` | 关 | `1` 关闭自动检查 |
| `MIO_UPDATE_CHANNEL` | `stable` | `prerelease` 时也接受预发布 |
| `MIO_UPDATE_INTERVAL_H` | `6` | 检查间隔（小时） |
| `MIO_UPDATE_BASE_URL` | GitHub | 覆盖更新源 base（内网/测试用，可指向 `file://` 或本地 http） |

## 17. 测试策略

- **单元**：`is_newer`（预发布/非法/跨段）、manifest 校验（非法输入矩阵）、source 解析（mock GitHub JSON，含 `latest.json` 缺失回退）。
- **downloader**：本地 http 假源 + sha256 失败重试 + `.part` 语义。
- **事务**：`execute_replace(install, staging, backup) -> result` 抽成纯函数，临时目录覆盖：正常替换、`.bak` 不覆盖、rename 重试、回滚。
- **健康确认**：mock sentinel 轮询（成功 / 超时→回滚）。
- **集成**：本地假 release（`MIO_UPDATE_BASE_URL` 指向本地）跑 `check→download→apply`；**不测真实 GitHub**（fixture JSON）。

## 18. 未决 / 后续

- 代码签名（本轮非目标）。
- 增量差分更新（本轮非目标）。
- 更新时机策略（是否强制"无 running 任务才允许立即更新"）——实现时按需细化。

## 19. 实现状态（2026-09-22）

- 已实现：version / manifest / source / downloader / apply（含回滚+健康确认+残留恢复+备份轮换）/
  service / runner（生产 apply_runner，detached spawn + 优雅退出回调） / api / runtime sentinel /
  托盘入口 / 前端更新条 / build.ps1 版本注入 / release.ps1。
- 测试：`tests/test_update_*.py`（84 项）全绿；含 runner 与生产 seam 回归。广度回归
  （update + test_api + test_mcp_server + test_doc_gate）**169 passed**。
- 未做（非目标）：代码签名、增量差分、CI 自动发版；`gh` CLI 未安装（发版前置）。
- 发版命令：`powershell -File packaging/release.ps1 -Version 0.4.0 -Notes "..."`。
- 已知限制：
  - 托盘 `icon.update_menu()` 跨线程刷新存在极低概率竞态（pystray 无编组 API）；worker 线程里
    `icon.notify` 同理，已 try/except 降级。
  - 更新条依赖既有 5s 任务轮询刷新；`update_*` WS 事件**已接线**（`events.broadcast_json` +
    `service._on_update_event`，消息 `{"type":"update_status","kind":...,"status":...}`），前端
    `App.jsx` 收到即 `setLastSync` 即时刷新并 toast。生产实测：连 `/ws` 触发 `check` 收到
    `update_status/update_check_failed`。
  - `default_apply_runner` 返回 0 仅表示"已触发 updater"，真正成败以 updater 的 sentinel 健康确认 /
    `~/.mio_taskhub/update/apply.log` 为准（hub 此时正退出，HTTP 响应可能丢失）。
  - 已加 `collect_submodules("mio_taskhub.update")` + `mio_taskhub.version` 到 `mio-taskhub.spec`，
    避免打包漏模块（需重新 build 验证）。

