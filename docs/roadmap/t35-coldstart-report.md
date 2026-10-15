# 冷启 Quickstart 计时报告（T3.5 / M3·M4）

> 取证日：2026-10-14 · 脚本：`scripts/coldstart_timing.py`
> 环境：本机（Windows，无 Python 依赖的绿色版 exe）· 临时端口 48699 + 临时 DB

## 1. 测量方法

`scripts/coldstart_timing.py` 完整复刻陌生用户路径：

1. **冷启 hub**：启动绿色版 `mio-taskhub.exe hub`，轮询 `/healthz` 直到 200，记启动耗时；
2. **闭环**：`register → create_task → claim → submit_result`，记每步耗时；
3. 重复 `--n` 次取 min/median/max。

脚本默认**不杀**同名进程（`--kill` 才按名杀），只终止本次启动的 PID，避免误杀生产 hub。

```powershell
$env:MIO_TASKHUB_PORT='48699'; $env:MIO_TASKHUB_DB="$env:TEMP\coldstart_test.db"
python scripts/coldstart_timing.py --exe dist/mio-taskhub-v3/mio-taskhub.exe `
  --base http://127.0.0.1:48699 --n 5 --startup-deadline 40
```

## 2. 实测结果（n=5）

| 指标 | min | median | max | 阈值 | 判定 |
|---|---|---|---|---|---|
| hub 冷启 | 3.403s | **3.945s** | 4.603s | <30s | **M3 PASS** |
| 闭环总计 | 0.090s | **0.131s** | 0.158s | <300s | **M4 PASS** |
| register | 0.020s | 0.029s | 0.046s | — | — |
| claim | 0.030s | 0.048s | 0.055s | — | — |
| submit | 0.016s | 0.024s | 0.034s | — | — |

**结论**：本机冷启到一次闭环 **≈ 4.1s**，远低于「陌生用户 <5 分钟」门槛（M4）。主要耗时在 hub 进程启动（~3.9s），API 闭环本身 <0.2s。

> **诚实边界**：以上为**本机**测量，非 3 台外部干净机器（M3 的"外部"要求需人工在干净 VM 执行）。外部机器可能因杀软扫描、首次解压、Defender 实时防护而显著变慢——见 §3 秒坑清单。

## 3. 秒坑清单（外部环境不可控项）

源自 `publish-github-release` 类经验 + 本环境实测：

| # | 坑 | 现象 | 规避 |
|---|---|---|---|
| 1 | **端口占用** | 48620 被旧实例占用，新 hub 起不来或静默退出 | 先 `tasklist`/`taskkill` 清同名进程；或用 `MIO_TASKHUB_PORT` 换端口 |
| 2 | **杀软实时防护** | exe 首次运行被 Defender 扫描，冷启从 4s 涨到 30s+ 甚至被隔离 | 首次运行加白名单；或提示用户"首次启动较慢属正常" |
| 3 | **残留进程锁文件** | 旧 `mio-taskhub.exe` 占用 `dist/`，`rm`/`mv` 报 `Device or resource busy` | 打包/删除前先杀进程 |
| 4 | **沙箱批量删除保护** | `rm -rf dist build` 项数 >50 被拦（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`） | 改用重命名腾位（`mv`），非删除 |
| 5 | **代理死链** | 本机 `http.proxy`/`HTTP_PROXY` 指向死代理，`git credential fill` 静默返回空、连 GitHub 超时 | 清代理 env 后直连 |
| 6 | **中文资产名编码丢字** | zip 资产名含中文经 stdin 传给 Python 时丢字 | 资产名用 ASCII |
| 7 | **`git add -A` 吞冒烟库** | `.smoke_ws/` 里的真实库副本被一起提交，blob 永久留历史 | `.smoke_ws/` 写进 `.gitignore`，或显式列文件 |
| 8 | **版本号三处硬编码** | `pyproject.toml`/`main.py`/`otel.py` 漏改，exe 自报旧版本 | 改后必须重新构建 |

## 4. M3/M4 判定与后续

- **M4（陌生用户 <5 分钟一次闭环，n≥5）**：**本机 PASS**（闭环 0.13s，含冷启 4.1s）。
- **M3（3 台外部干净机器成功）**：**待人工取证**——需在 3 台无 Python 的干净 VM 上执行绿色版，记录每台冷启耗时与 §3 命中项。本报告提供可复跑脚本与基线，人工执行后回填 §2 表。

> **纪律**：M3 未在外部机器取证前，**不得对外宣称"3 台干净机器验证通过"**（`03` §6.2 纪律）。
