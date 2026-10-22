# Changelog

## Unreleased

> 独立安装与卸载程序：图形安装向导、正规卸载入口（Windows「应用和功能」）、快捷方式、确定性打包。
> 版本号在发版提交（`chore(release)`）统一抬升并打 tag —— 本节保持 Unreleased。

### Added — 安装 / 卸载程序
- **`mio_taskhub/installer/` 新增安装与卸载子系统**：安装目录管理、「应用和功能」注册表项（HKCU 用户级，免管理员）、开始菜单/桌面快捷方式、进程清理、payload 解压校验。
- **图形安装向导 / 卸载向导**（Tkinter）：`mio-taskhub-setup.exe` 双击即装，装完建快捷方式；`uninstall.exe` 双击即卸，可选「同时删除用户数据」（默认保留数据库/日志/备份）。
- **静默模式**：`mio-taskhub-setup.exe --silent` / `uninstall.exe --silent`（CI、批量部署）。
- **Windows「应用和功能」卸载入口**：`HKCU\...\Uninstall\mio-taskhub`，含 `UninstallString` 与 `QuietUninstallString`。
- **`mio-taskhub-installer.spec`**：安装器/卸载器独立 spec（onefile，刻意不打 fastapi/sqlmodel/uvicorn 瘦身）。
- **`scripts/make_zip.py`**：确定性 zip 打包（替代 `Compress-Archive`）。
- **`mio_taskhub/release_assets.py`**：发布资产清单单一事实源；`release.ps1` 同步上传安装包。
- **`packaging/安装说明.txt`**：面向终端用户的安装包说明。

### Changed — 安装包合并为单文件
- **分发目录只剩 `mio-taskhub-setup.exe` 一个 exe**：程序本体（payload）已合并进它尾部，
  payload 里又含 `uninstall.exe` → 卸载器也在里面。**装和卸用同一个文件**
  （`--uninstall` 即为卸载）。不再单独发一份 `uninstall.exe`（那是同一份内容的副本）。
- **payload 里不再放 `mio-taskhub-setup.exe`**：放进 payload 的那份是**未合并版**
  （尾部没有 payload），用户双击必然报「找不到安装包」—— 是个坏入口，不如没有。
- **安装器窗口检测到已安装版本时多一个「卸载」按钮**：单文件安装包下，
  重新双击这一个文件就能重装或卸载。

### Fixed — 卸载可能删掉用户的整个文件夹（严重）
- **`detect_install_dir()` 在冻结态会回退到「卸载器自身所在目录」**。用户把
  `mio-taskhub-setup.exe` 下到 `D:\Downloads` 后双击 `--uninstall` →
  程序把**整个 Downloads 文件夹 rmtree 掉**（用户的数据、别的下载文件全没了）。
- 对策两道闸门：① `looks_like_install_dir()` —— 目录里必须有 `mio-taskhub.exe`
  或 `install.json` 才认；② `uninstall()` 里再校验一次，不满足就**什么都不删**
  直接返回并说明原因。

### Fixed — 窗口先在左上角闪一下再跳到屏幕中心
- **根因**：Tk 创建 toplevel 时会**立刻把它映射出来**（位置由 Windows 决定，
  通常在左上角），之后才调 `geometry` 挪到中间 → 用户看到「先在左上角
  打开，再闪到屏幕中心」。
- **解法**：`root.withdraw()` → 建控件 + 算位置 → `deiconify()`，全程不显示。
- **顺带修正了两处坐标语义**（实测确认，原注释写反了）：
  `geometry` 的 `+x+y` 是**整个窗口**左上角（不是客户区）；`winfo_x/rootx`
  才是客户区左上角。居中偏差从 dy=-20 变成 **dx=0 dy=0**。
- **边框偏移改用 Win32 测**（`ClientToScreen` 减 `GetWindowRect`）——
  窗口隐藏时 Tk 的 `winfo_x` 返回 0，算出来的偏移是 320 这种垃圾值。

### Fixed — 自更新装不上（真实缺陷）
- **自动更新永远失败**：实测已发布的 v0.6.1 资产 zip 顶层带一层 `mio-taskhub-v3/`，
  而更新器 `verify_staging` 按「根目录即内容」校验 → 校验必失败 → 更新永远装不上。
  新增 `mio_taskhub/payload_layout.py` 统一契约（安装器与更新器共用），
  解压时自动剥离顶层前缀目录。
- **打包用 `Compress-Archive` 会失败**：打包产物的 `_internal/base_library.zip` 是只读的 →
  `CompressArchiveUnauthorizedAccessError`；且其输出布局与成员顺序不稳定 → 改用 `scripts/make_zip.py`。

### Fixed — 卸载器相关
- **自更新会带走卸载器**：更新整体替换安装目录 → 卸载入口消失。`update/apply.py` 的
  `_preserve_uninstaller()` 在替换前把 `uninstall.exe` 拷进 staging。
- **裸 exe 名被误判为占用者**：`os.path.realpath("")` 解析成当前工作目录，
  把 Windows 的 `Registry` 等系统进程算成「占用安装目录」→ 永远删不掉。
  `processes._is_under()` 与更新器同源实现均已修正。
- **卸载器删不掉自己所在的目录**：Windows 锁住运行中的 exe 映像。
  派发带重试的 .bat 自删除（`Popen` + `cwd` 指向目录外 + 批处理变量写 `%%i`）。

### Changed — 安装器界面重做（2026-10-21）
- **深色卡片化布局 + 荧光绿主色**（对齐 Web UI 的 `#cdfa3f`），替换原先的 ttk 默认控件拼贴。
- **输入框 / 按钮 / 复选框改为 `tk.Canvas` 自绘**：原生 Checkbox 与 Entry 在深色底上
  露出浅色系统色，看着像没做完。
- **「安装包」行默认不显示**：真实分发时 setup.exe 与 zip 同目录 → 自动找到，
  用户什么都不用做。只有自动定位失败才显示该行作为兜底。
- **路径友好显示**：只保留末两段（`…\Programs\mio-taskhub`），完整路径走悬停提示。
- **去掉中间那块空的黑色日志面板**：改为安装时才展开。
- **用界面内提示条替代 `messagebox`**：模态框在部分 Win10 上标题栏会闪「(未响应)」。
- 文案面向小白：「无需安装 Python」「在桌面和「开始」菜单中创建快捷方式（可选）」「保持默认即可」。

### Fixed — 打包后静默安装找不到安装包
- **payload 搜索路径不含「exe 自身所在目录」** —— 真实分发形态是 setup.exe 与 zip
  同目录，原先没排这一项 → 打包后的 `--silent` 安装报「未找到安装包」（实测）。
  `payload._exe_dir()` 已排到最前，并补两条回归用例。

### Added — 单文件安装包（安装器与程序本体合并）
用户反馈「打包能把安装程序和软件包合并吗？没必要分两个包」——
真实安装器（NSIS / Inno Setup）都是**单文件** `setup.exe`，用户只下一个
文件、双击、装完就走。原来的形态要下两个文件再放同一目录，明显反人类。

实现（self-extracting）：
- 新增 `mio_taskhub/installer/embedded.py` —— 把 payload zip **追加**到
  `mio-taskhub-setup.exe` 末尾，再写一个 **16 字节尾部标记**
  （magic `MIOSTUP1` + 偏移 + 长度）。读的时候 seek 到 `size-16` 就能定位，
  不用扫描整个文件（PE 头里没有「附加数据」的字段，只能靠自己的标记）。
- 新增 `scripts/make_setup.py` —— 合并 + 校验（解出来必须是合法 zip 且含
  `mio-taskhub.exe`）。
- `payload.find_payload()` 查找顺序改为：显式路径 → **内嵌** → 同级目录 →
  常见下载目录 → 临时目录。内嵌排最前，因为单文件才是主流分发形态。
- `install()` 拆成 `install()`（try/finally 负责清理）+ `_install_impl()`，
  保证**成功和失败都会清掉**解出的临时目录，不留垃圾。
- `check_installer_bundle.py` 增加单文件校验：内嵌标记存在、内嵌的
  `uninstall.exe` 与当前产物同哈希。
- `build.ps1` 步骤 6 改为：合并 payload → 单文件 setup.exe + 独立 uninstall.exe
  + 安装说明（**不再**拷一份 zip 进安装包目录）。

分发资产随之调整：安装包目录里**不再**放 payload zip（它已经合并进 exe），
但 payload zip 仍作为**顶层资产**发布 —— 客户端自动更新要用。

验证：**只放 setup.exe 一个文件**（无 zip、无卸载器）→ 静默安装 rc=0 →
hub 起得来（healthz 200）→ 卸载 rc=0、目录删干净、临时目录零残留。

### Fixed — GUI 健壮性
- `Canvas.delete("all")` 会把 `create_window` 嵌的 Entry 一起删掉 → 输入框变 1×1
  隐藏、路径框看着是空的。改为按 tag 只删图形元素。
- Canvas 里子控件用 `pack(expand=True)` 会把旁边按钮压成 1×1（看不见）→ 改用 grid 固定列宽。
- 显示文本改用独立 StringVar，避免回写污染真实路径变量。
- 后台线程回 UI 线程加 `_ui_call()` 兜底：主线程不在 mainloop 时 `root.after()` 抛
  `RuntimeError` 且发生在工作线程 → 界面会永远卡在「正在安装」。
- 窗口销毁时清理 after 队列，消掉 `invalid command name ... (after script)` 残留。
- **「找不到安装包」兜底路径崩溃**（打包版实测）：兜底行原本放在卡片内容区里，
  而该区用 `place()` 布局 → `pack()` 不生效；且 `pack(before=...)` 引用到被
  `pack_forget()` 的日志面板 → Tcl 报 `window "..." isn't packed`。
  改为卡片外的独立槽位 + 靠 pack 顺序定位。
- **占位符被 Entry 盖住只剩尾巴**（显示成「ip」）：Entry 不透明，嵌在 Canvas 上
  会遮住占位符文字。空值时把 Entry 的 window item 设为 hidden。

### Fixed — GUI 安装永远卡在「正在安装…」
**工作线程碰了 Tk 对象**。`_work()` 里直接 `self.var_payload.get()`，
从后台线程调 Tk 的变量存取会抛
`RuntimeError: main thread is not in main loop` → **安装根本没开始**，
界面却卡在「正在安装…」，日志一片空白（异常处理器自己调 `root.after`
也抛同样的错 → 连错误都显示不出来，用户只看到界面不动）。

修法：
1. **主线程**把 Tk 变量读成纯 Python 值，再传给工作线程（工作线程不碰任何 Tk 对象）
2. 工作线程 → 主线程的 UI 操作改用**队列交接**（`_Base.post()` /
   `_Base._drain_queue()`，主线程在 `_pulse` 里取出来执行），不再跨线程调
   `root.after`
3. `_ui_call` 保留为兼容入口，但新代码一律用 `post()`

### Fixed — 卸载偶发秒退（rc=23，目录还在）
**两个进程清理的时序缺陷**，2026-10-21 定位：

1. **先杀 worker → supervisor 立刻把它拉起来** —— hub 是 supervisor→worker 双进程，
   supervisor 在 worker 意外退出时会**重新 spawn**（退避重试）。先杀 worker
   正好给了它重启的窗口 → 新 worker 又把安装目录钉住 → 目录永远删不掉。
   修法：按**祖先链深度排序，父进程在前**，先杀 supervisor 再杀 worker；
   且**不再连带杀子进程**（父先死就不会有重启）。
2. **pid 复用导致误杀** —— `psutil.Process(pid)` 只缓存 pid，目标若在两次调用
   之间退出，pid 可能被系统**复用**给别的进程 → `taskkill /PID` 杀掉无辜进程
   （实测把用户的 shell 一起杀掉 → 卸载器「秒退」且没留下任何日志）。
   修法：杀掉前**重新读一次 `exe`** 与目标路径比对，不符就跳过。
   实测这条守卫确实在触发（installer.log 里有 `skip pid=... pid 被复用`）。
3. **【最隐蔽的一个】psutil 杀成功后又 taskkill 同一个 pid** —— 原代码：
   `psutil.Process(pid).kill()` 紧接 `_taskkill_tree(pid)`。进程已死 →
   **pid 被系统回收** → 下一个进程可能立刻复用 → `taskkill /F /PID` 命中
   **被复用的新 pid** → 杀掉无辜进程。实测被杀的是**启动卸载器的 shell** →
   连带把卸载器自己带走 → **卸载器在 `main()` 之前就死了**（连 `[entry]`
   日志都没写），表现为「进程秒退、目录还在、退出码 23」，且**偶发**
   （取决于 pid 是否被复用）。
   修法：psutil 杀成功后**不再 taskkill**；杀完 `wait()` 确认退出；
   只有 psutil 失败时才用 taskkill 兜底，且兜底前再核对一次身份。

验证：四种启动形态各压测一轮，**合计 49 轮全通过** ——
① bash 单脚本「装→起 hub→卸载」15 轮（复刻失败形态）
② bash 后台子 shell 起 hub 12 轮
③ Python spawn 起 hub 10 轮
④ 带并发负载（同时跑全量测试）12 轮
每轮都确认：自删除最终完成、注册表/快捷方式清空、用户数据保留、hub 零残留。

### Fixed — 静默卸载异常退出（rc=23，目录还在）
三个独立缺陷叠在一起，2026-10-21 逐个定位：

1. **payload zip 里的 uninstall.exe 是旧版本** —— 重建了 `dist_installer/uninstall.exe`
   却没重打 `dist/mio-taskhub-win64.zip`（14:08 打的 zip vs 16:19 的 exe）。
   用户装到机器上的是旧卸载器 → 静默卸载直接异常退出，**进程秒退、目录还在、
   退出码 23**，且因 `disable_windowed_traceback=True` 连 traceback 都被吞掉，
   完全无从排查。
   对策：新增 `scripts/check_installer_bundle.py` 校验「安装器 exe ↔ payload zip ↔
   独立安装包目录」三者同源；`build.ps1` 步骤 6 结束时强制跑一次，不一致直接 throw。
2. **`uninstall_entry.py` 没透传 `--dir`** —— 调 `cli_main(['uninstall','--yes'])`，
   把 `--dir` 丢了 → CLI 只能靠注册表推断安装目录。已加 `_passthrough`。
3. **`taskkill /F /T` 误伤** —— `/T` 会连带杀掉目标 pid 的整棵子孙树，
   顺着父链往上会波及**启动卸载器的人**（实测把 bash 一起杀掉）。
   改为：只杀目标 pid 本身（`/F` 不带 `/T`），子孙进程由
   `psutil.children(recursive=True)` 单独枚举、子先父后地杀。

另外给 CLI 加了顶层异常兜底：内部异常统一落 `installer.log`（含 traceback），
返回码 23 区分于业务失败的 1，不再静默退出。

### Added
- `scripts/check_installer_bundle.py` —— 校验安装器分发产物一致性
  （安装器 exe ↔ payload zip 里的 uninstall.exe ↔ `dist/mio-taskhub-installer/`）。
- 发布检查清单新增第 ⑧ 项「安装器产物一致」。

### Tests
- `tests/test_installer.py`（35 用例）：payload 布局契约、安装/卸载、注册表读写、
  快捷方式创建删除、只读文件删除、进程路径匹配回归、payload 自动定位回归、
  自更新保留卸载器、**payload 卸载器不得是旧版**、**--dir 必须透传**、
  **CLI 内部异常必须留痕**、**工作线程不得碰 Tk 变量**、
  **GUI 安装在真 mainloop 下要跑完**。全量 1504 passed / 0 failed。

## v0.6.1 (2026-10-20)

> 执行链可靠性收口（P1–P14）：幽灵 agent、提前回收、空转风暴、指标抖动、无界增长。

### Fixed — 执行链与生命周期
- **幽灵 agent 反复抢占**（P3）：`_mark_stale_agents` 处理 `last_heartbeat=None`；dispatcher 加心跳门槛；`claim_for` 拒绝非 ONLINE agent；`analyze_reaper` 加 `never_started` 独立桶（修正"链路断 70.7%"→真实 0.4%）。
- **OFFLINE agent run 被 120s 紧窗口提前回收**（P1）：`_mark_stale_agents` 跳过有活跃 run 的 agent。
- **run 心跳同步刷新 agent 心跳**（P4）：跑任务即保 ONLINE。
- **手动 retry 重置 bounce_count**（P5）：防幽灵洪泛致 retry 立即再 FAILED。
- **任务到终态关闭活跃 run**（P6）：修 run 悬挂（曾悬 23.9h）。
- **终态时间戳只在首次进入时设**（P10）：修 `COMPLETED→REVIEW→DONE` 覆盖 `completed_at`。
- **停滞检测排除有心跳的长任务**（P9）；`remediation.requeue_task` 关 run。

### Fixed — 可观测与后台任务
- **idea_review 空转风暴**（P11）：失败退避 `_recently_attempted`（此前 3h 内 1445 任务 / 4376 领取）。
- **低阈值洞察误判恢复致抖动**（P13）：`_is_recovered` 区分方向（此前 7585 条 success_rate-low）。
- **depmetricssnapshot 无界增长**（P14）：`_dep_persist_loop` 补 `cleanup()`。
- **stalled 指标把 QUEUED 误计**（P12）；**HighHttpErrorRate 把 4xx 计入**（P7/F10）；**night_runner 子进程 URL 缺 `/api/v1`**（P8/F9）。
- **`never_started` 观测细化**（P2）：reaper_decision 记录真实 `run_state_before` + `run_heartbeated`。

### Added — 运维脚本
- `scripts/archive_spinning_tasks.py`：识别 REVIEW 空转残留并归档。
- `scripts/coldstart_timing.py` / `scripts/repro_build.py` / `scripts/gen_readme_stats.py` / `scripts/release_checklist.py`（G2–G4 交付）。

## v0.6.0 (2026-10-14)

> roadmap-05 G2–G3 收口：执行生命周期加固 + 开源前置 + 能力收敛。

### Added — D1 生命周期化（T3.1，FR: 无 / roadmap-05）
- `heartbeat.py`：默认超时 300→360s（+20% headroom，覆盖实测心跳抖动 p50=302.7s）；新增 `GRACE_PERIOD_SECONDS=60` 宽限期，agent 在线且 progress>0 时 base+grace，避免链路断误杀。
- `api/tasks.py`：`_task_detail` 新增 `diagnostic_hint` 字段，FAILED/RETRYING 任务返回用户可读诊断（agent offline / heartbeat timeout 原因 + 建议）。

### Added — 开源前置（T3.4，FR: 无 / roadmap-05）
- `LICENSE`（MIT, Copyright 2026 mldlbs）入库，解除法律阻塞。
- `scripts/gen_readme_stats.py`：README 统计从代码派生（版本/REST 端点/MCP 工具/测试数/LOC/前端），`--write` 更新、`--check` 校验漂移（CI 用），消除人工维护漂移。

### Added — 冷启 Quickstart 计时（T3.5，FR: 无 / roadmap-05）
- `scripts/coldstart_timing.py`：冷启 hub + register→claim→submit 闭环计时 harness；实测 hub 冷启中位 3.9s、闭环 0.13s。
- `docs/roadmap/t35-coldstart-report.md`：计时报告 + 8 项秒坑清单。

### Changed — 能力地图定档 + 技术债取舍（T3.6，FR: 无 / roadmap-05）
- `docs/taskhub/capability-map-20261001.md` §9：逐项定档（保留 19 / 延后 3 / 删除 1），0 项待定。
- `docs/roadmap/02-current-state-audit.md` §9.1：技术债逐项取舍，P0/P1 全清零，P2/P3 登记延后。

### Docs — G2 实验结论回写（T2.5，FR: 无 / roadmap-05）
- `01-positioning.md` §4.2 / `03-technical-gap.md` §6.2·§7：V1 证据不足 / V2 观测支持 / V3 部分成立（81.9%）/ D1 命中「部分成立」档。

### Deferred — 条件性任务（T3.2 / T3.3，FR: 无 / roadmap-05）
- T3.2（D2-b 协议化）：前置 V1 成立未满足（证据不足）→ defer。
- T3.3（D3 回执）：前置外部审查者 0 → defer。

## v0.5.5 (2026-10-05)

### Changed — 记忆视图合并观测模块 + 旧路径 404 修复（任务 e4c3bc51，FR-1~FR-6）

- Rail 收掉「记忆观测」独立入口，「记忆」视图改为双 tab 容器：默认 tab「记忆观测」（`MemoryObservatoryView` 原样迁入、零功能改动），次 tab「记忆网关」（存储状态/查询/调用统计/实时事件）；网关 5s 轮询仅在该 tab 挂载时运行。
- 修复 Memory Gateway 前端三处旧路径 404：`api.js` health、查询面板、ErrorBoundary 上报由 `/api/memory/*` 改指 `/api/v1/memory/*`（实测旧前缀恒 404 且无 alias）；`api/memory.py` 模块 docstring 同步 v1 化（纯注释，函数体零 diff）。
- 修复一键打包产物路径失配：`packaging/build.ps1` 的 `$distDir` 改为 `dist\mio-taskhub-v3`，与 `mio-taskhub.spec` COLLECT name 对齐；第 4 步校验 5 项、第 5 步分发文件复制、zip 与 hub 重启全流程恢复无人工干预（`build.ps1 -Quick` 实跑通过）。
- 新增 `tests/test_memory_merge.py` 4 个静态回归守卫（FR-1 双 tab 单入口 / FR-2 无旧路径 / FR-3 docstring 一致 / FR-4 脚本与 spec 对齐）。

### Added — 记忆观测层自 mneme 迁入（任务 8a966293，FR-1~FR-10）

- 新增 `GET /api/v1/memory/observatory/data?logs=1`：`MIO_HOME`（`memory.jsonl` + `experience_reuse.jsonl`）只读投影为图谱 JSON（实体/关系/meta），mtime 缓存 + `threading.Lock` 双检，零写入、不走 MCP、无 Node 依赖；坏行计入 `meta.skipped` 不中断；错误体 `{"error": "..."}`。
- 新增 `mio_taskhub/memory_observatory.py` 投影模块与 Rail「记忆观测」视图（`MemoryObservatoryView` + `lib/observatoryAdjacency.js` 纯函数邻接表）：卡片网格、类型筛选、观察视图日志默认隐藏/一键展开、本地过滤（标注非权威检索）、复用热度徽标（阈值 ≥8 高 / ≥3 中，对齐 mneme 现值）、详情出入边 ego 跳转、提示词片段复制/导出（启发式标注）、项目/来源聚合面板。
- 新增配置 `MIO_OBSERVATORY_HIDE_LOGS`（非空强制隐藏日志实体，`logs=1` 失效）；测试 `tests/test_memory_observatory.py` 11 用例对标 mneme 投影语义；mneme 源仓库零改动。

## v0.5.3 (2026-10-05)

### Added — 想法生产闸门重写：INBOX 分级 + 价值加权 + 带宽感知

- 新增 **INBOX（收集箱）状态**：自动生成的想法先进收集箱、不占 NEW 槽位；自动初筛按分数/年龄晋升 NEW（≥60）或归档（<30 且 >14 天）。
- 闸门由硬编码计数改为**价值加权**（novelty×0.4 + feasibility×0.3 + impact×0.3，14 天半衰期）+ 7 天吞吐带宽，`ok/degraded/critical` 三级，**生产降级而非硬停**（daily→weekly→alert，永不硬停）。
- 新增 API `/gate/status`、`/gate/force-generate`、`/ideas/inbox` 与批量初筛；MCP +5 工具；前端 GateStatusCard（30s 轮询 / 三级配色 / 强制生成）与 InboxView 收集箱三标签页。

### Fixed — 每日想法生成修复三件套

- 同步生成的想法 `status` 由 `new` 改写 `inbox`（此前绕过收集箱，与闸门设计矛盾）；存量绕过行已迁移修正。
- 想法生成 job 素材上限 `sources_limit` 5→7，修复三策略素材组合探尽导致的 409（存量 job 已更新）。
- 观测守护随启动方 CWD 漂移：`mio observer serve` 固定素材库基目录（新增 `MIO_OBSERVER_BASE_DIR`），修复每分钟 `dag/ENOENT` 报错与洞察停更。

## v0.5.2 (2026-10-03)

### Changed — 更新入口收进设置面板

- 顶部更新条不再常显：仅在有新版可操作时出现（发现新版/下载中/待重启/需手动/失败），右侧可跳设置或忽略。
- 新增 **设置面板**（Rail 底部齿轮）：软件更新区（当前版本/状态/通道 + 检查更新/下载/重启并更新）、版本说明、运行信息（PID/运行时长/内存/线程）。

## v0.5.1 (2026-10-03)

### Added — 手动检查更新入口（更新横幅常显）

此前 `UpdateBanner` 只在 `available/downloading/ready/needs_manual/failed` 时渲染，`up_to_date`/`idle`/`check_failed` 时整条隐藏，且没有任何手动触发入口——用户看不到版本状态，也无从手动检查，观感上像「更新功能不存在」。现：

- 更新条**常显**，展示当前版本与状态（`已是最新版本 vX` / `正在检查更新…` / `检查更新失败：…` 等）。
- 新增 **「检查更新」按钮**（调用 `POST /update/check`，此前该 API 无 UI 消费）。
- `failed` 增加「重试」；`needs_manual`（跨代不兼容）明确提示需手动更新；样式区分活跃（蓝）/失败（红）。

### Fixed — 观测口径：切断 insight 自喂养慢环

- 主成功率口径排除监控自造任务（`insight-auto`/`insight-followup`），避免指标度量自己派出的失败任务而长期 critical（任务 e83cc9e2）。
- 新增 `taskhub_task_excluded_monitoring_total` / `taskhub_task_monitoring_success_rate`，暴露被排除样本量与监控子系统自身 SLO。
- `insight_remediator` 任一终态（COMPLETED/FAILED/CANCELLED）均 ack；派生前实时 `/metrics` 复核（`MIO_INSIGHT_AUTOTASK_PRECHECK`，默认开）。
- `insights.store()` dedup 时刷新 `metric_value` 并新增 `_is_recovered` 恢复检测。

### Added — reaper 决策证据 & 生命周期观测

- reaper 发出结构化 `reaper_decision` 事件（kind/agent_status/progress/hb_lag/effective_timeout/reason），仅观测、不改行为（任务 79b856ef）。
- 终态指标窗口化（`taskhub_task_terminal_window_days`）与 `success_rate_active` 口径。

### Security

- 统一 MCP 风险门控：默认拒绝破坏性工具调用（round3 P1-C）。
- 安全默认绑定 CLI + webhook SSRF 防护（round2）。

## v0.4.0 (2026-09-23)

### Added — 文档生命周期在 Web UI 可见（此前 8 类生命周期前端完全看不到）

`doc_statuses` 早就写进了库、`set_doc_status` 也有质量门控，但前端**一个像素都没渲染**——缺口其实有两半：

- **后端清单漏字段**（`api/task_documents.py`）：`GET /tasks/{id}/documents` 的条目由 `_rel_entry()` 构造，从未带 `status`；只有 `GET /doc` 与 `PUT /doc` 的响应里有。前端即使想渲染也拿不到数据。现补 `status`（= `doc_statuses[kind]`，形如 `{state, at, note}`）。
- **前端未消费**：新增 `web/src/docLifecycle.js`（状态 → 中文名 + 色调的纯映射，与 `doc_lifecycle.py` 同源），`DocPanel` 据此渲染 ① 列表条目的状态徽标 ② 选中文档 meta bar 的生命周期赛道（`草稿 → 待审 → 已批准`，当前节点描边高亮，已过节点正常色、未到节点淡化）。新增 `api.getDocStatuses()`。

两条设计约束（都由实测暴露）：

- **扫描发现的文档不挂状态**：`doc_statuses` 是「该 kind 登记的那一份」的状态，而 `discover_task_docs` 的 kind 只是文件名启发式归类——同一任务实测有 **13 份文件都被判成 `requirement`**，若统一套状态会显示成一排假「草稿」徽标。故只有 `source=field` 的登记条目携带 `status`，并有测试锁定该边界。
- **未落状态也显示赛道**：`spec`/`plan`/`api` 等登记了文档但尚未 `set_doc_status` 的 kind，此前什么都不显示；现在按全 `todo` 渲染赛道，让「这个 kind 有生命周期、还没开始推进」本身可见。

### Added — 阶段推进的文档生命周期门控（draft 空契约不能再进 design）

`advance_stage` / `move_to_stage` 此前只查 `doc_path_of`（文档路径是否注册），从不查 `doc_statuses`——于是 draft / 空契约仍能推进到 design，且 `api` 契约从未被任何阶段门控（生命周期可见性缺口的「后半段」）。

- 新增 `LIFECYCLE_GATE`（`api/task_stages.py`）：目标阶段 → `{kind: 所需最低状态}`。进入 `design` 需 `spec` + `api` 都已 `approved`；进入 `planning` 需 `plan` `approved`；进入 `brainstorming` 需 `requirement` `approved`。
- **`api` 首次被门控**：此前不在任何阶段 `document_kinds` 里，契约写完（自动落位 draft）就能直接推进；现在未 `approved` 不能进 design。
- **向后兼容**：仅当 `doc_statuses[kind]` 已显式设过状态才校验；从未设状态的旧任务 / 仅靠 `spec_path` 登记路径（未走 `PUT /doc`）的任务一律放行，不阻断历史数据。
- **force 绕过 + 留痕**：请求带 `force=true` 可跳过门控，并 emit `task_stage_gate_forced` 事件（与 `set_doc_status` 质量门控绕过同源）。MCP 工具 `taskhub_advance_stage` / `taskhub_move_to_stage` 新增 `force` 参数。
- `GET /tasks/stages/requirements` 新增返回 `lifecycle_gate`，供前端 / agent 提示「推进到某阶段需要哪些文档达何状态」。
- 新增 `doc_lifecycle.reached_state(kind, current, required)` 判定线性生命周期是否已达门槛。
- 测试：`tests/test_stage_lifecycle_gate.py`（10 例）；`test_task_doc_paths.py::test_write_then_advance_design_without_workspace_doc` 改为断言 draft 被门控 + force 绕过。

### Added — 自动更新模块（update/）

- 新增 `mio_taskhub/update/`：`UpdateService` 状态机（idle→checking→up_to_date|needs_manual|available|dismissed|check_failed；available→downloading→ready→applying→done|failed），覆盖版本自检、清单拉取、差量下载、校验（sha）、应用全流程。
- 新增 `api/update.py`（`/update/status|check|download|apply|dismiss`）与前端 `UpdateBanner`：在 Web UI 提示「更新可用 / 下载进度 / 需手动处理」（如跨大版本或校验不符）。
- 更新源可配置：环境变量 `MIO_UPDATE_CHANNEL`（默认 `stable`）；`install_dir()` 按 frozen / 开发态自动解析；偏好存 `~/.mio_taskhub/update/prefs.json`，下载暂存 `~/.mio_taskhub/updates`。
- 配套 `tests/test_update_*.py`（manifest / downloader / apply / runner / service / source / ws / e2e）共 10 套。

### Added — 开发前文档 Consult 强制（pre-push 门控 + ReadEvidence 留痕）

- 新增 `scripts/git-hooks/pre-push` + `pre_push_runner.py` + `install_hooks.py`：push 前校验任务 `spec/api/plan` 已 `approved`、diff 引用的 `FR-n` 真实存在于已批准需求文档；hub 不可达时放行并告警。
- 新增 `mio_taskhub/read_evidence.py` 与 `taskhub_read_document` 的 ReadEvidence：`claim` 返回 `required_reads` / `required_fr`，动手前必须对每个 required kind 落一条带内容指纹的读证，提交成功路径校验「缺失 / 指纹不符」→ 422。
- 新增 `tests/test_doc_gate.py` / `test_read_evidence.py`。

### Fixed — 看门狗超时误判（18 个 FAILED 里 12 个是误杀）

排查 2026-09-17 的会话可视化需求时发现：库里 18 个 `FAILED` 任务中有 **12 个是误判**——agent 仍在正常上报 `progress`、随后还成功提交了 `result`（`exit_code=0`），任务却在它奔跑途中被判死。抽样 `c5bb23fd` 事件流：`12:03:33` 判 FAILED，`12:06:26` 成功提交，**判死比成功提交早 2 分 53 秒**。三处根因：

- **`_sweep` 用 agent 级 OFFLINE 旁路了 run 级心跳新鲜度**（`heartbeat.py` / `background.py`）：判死条件是 `run.agent_offline or expired`，而 `agent_offline` 取 agent 表 `last_heartbeat`（`AGENT_TIMEOUT_SECONDS=180`），agent 又往往只在 claim 前心跳一次 —— 于是「agent 心跳过期」单独就能杀死一个正在正常上报 progress 的 run。新增 `HeartbeatSweep.effective_timeout()`：agent OFFLINE **只把有效超时收紧到基线**（`AGENT_OFFLINE_TIMEOUT_SECONDS=120`），绝不跳过 run 级新鲜度判定。既保住「agent 掉了要快速回收」的原意，又消除误杀。
- **系统侧状态迁移全部丢弃 TaskEvent**（`background.py` 9 处）：`apply_transition()` 会构造 `TaskEvent` 但不负责持久化，而 `background.py` 的 9 处调用**都丢掉了返回值**、从不 `db.add(event)`。后果是超时判死、重入队列、放行依赖等系统动作在 `taskevent` 里**完全不留痕**（实测 `taskevent` 只有 71 行，而 `event` 728 行）——事后根本无法归因。新增 `_apply_event()` / `_try_apply_event()` 统一 add，9 处全部替换。
- **FAILED 是终态，迟到的成功结果无法回写**：`_on_timeout` 记 `FAILED`，agent 之后提交成功结果时 `_safe_transition` 静默吞掉 `IllegalTransition`，库里留下 `task=FAILED + run=成功/exit0` 的矛盾终态且永不自动纠正。

### Fixed — 三处状态转换在状态机里根本不存在（静默失败）

排查过程中用 `validate_transition()` 实测发现，代码正在调用的三个转换**未在状态机定义**，全部抛 `IllegalTransition`：

- **`RUNNING → QUEUED`**（`_on_timeout` 重试重入分支）：原表只有 `CLAIMED→QUEUED` 的 T21/T20。更糟的是异常会**连带回滚同一事务里的 run 回收** —— 该 run 每轮扫描重复失败、永远回收不掉。新增 **T23** `(RUNNING,IMPLEMENTING)→(QUEUED,READY)`（SYSTEM）。
- **`RETRYING → QUEUED`**（`_requeue_retries` 退避到期重入）：命中的是 T16，而 T16 的 RETRYING 支 `allowed_actors={USER}`，scheduler 以 SYSTEM 调用直接抛「无权执行 T16」→ **指数退避重试从未真正重入过队列**。状态机索引按 `(from,from_stage,to,to_stage)` 唯一，同一 key 无法再挂第二个 T-id，故直接把 T16 的 RETRYING 支 actor 集合扩为 `{USER, SYSTEM}`（FAILED 各支保持 user-only，`test_t16_only_user` 锁定不变）。
- **`FAILED → COMPLETED`**（迟到成功纠正）：新增 **T22** `late_result_recovery`，允许 SYSTEM/AGENT，`requires_reason=True`。

`_on_timeout` 同时改为：先无条件落库 run 回收，任务迁移走 `_try_apply_event`（非法只记 warning 不中断），并对已终态任务短路。

### Fixed — 误判会被迟到成功结果悄悄翻案的反向风险

`runs.py` 新增 `_is_system_timeout_failure()` 守卫：只有当任务当前 `FAILED` 且**最近一次 TaskEvent 是系统超时判死**（`actor_type=system` 且 reason 前缀为 `agent_offline:` / `heartbeat_timeout:`）时，才允许 `_handle_success` 走 T22 改判。agent / user 主动报的失败**不接受**迟到的成功结果翻案，必须人工复核。

### Changed — 超时基线与单一事实源

- **`DEFAULT_TIMEOUT_SECONDS` 120 → 300**（可用 `MIO_TASKHUB_TIMEOUT_SECONDS` 覆盖）：实测 agent 心跳间隔 67s~199s，原值 120s 过紧，正常长任务也会被判死。agent 离线基线单独用 `MIO_TASKHUB_AGENT_OFFLINE_SECONDS`（默认 120）。
- **删除 `background.py` 里重复的 `HeartbeatSweep` / `RunInfo`**，统一从 `heartbeat.py` 导入（单一事实源）。此前两份几乎相同的判死逻辑各自演化，同一个缺陷得修两遍。线程心跳钩子改为可选回调 `on_tick` / `on_error`。
- **`api/board.py` 的超时告警口径改为复用同一常量**：原先自己硬编码 120s，会提示「将被重置重领」而 sweep 实际要等到 300s，告警与行为不一致。
- `RunInfo.timeout_seconds` 默认值改为 `None`（= 沿用 sweep 自身超时），否则 dataclass 默认值会覆盖调用方显式配置的 `timeout_seconds`。

### Tests

- 新增 `tests/test_timeout_misjudge.py`（9 例）：覆盖三条防线 —— agent OFFLINE 不旁路 run 新鲜度、系统迁移必须留痕、迟到成功可纠正系统超时判死；并反向验证 agent 自报失败不允许被翻案、正常成功路径不受影响。
- `tests/test_agent_heartbeat.py` 补 3 例：fresh run 在 agent OFFLINE 下不得判死、心跳落后 71s（实测生产值）仍在基线内不得判死、落后 130s 超出基线仍回收。
- `tests/test_task_doc_paths.py` 补 1 例：清单条目带 `status`（有生命周期的带 `{state}`、无生命周期的为 `None`）。
- `tests/test_documents_related.py` 补 1 例：扫描发现的文档不得携带生命周期状态（锁定 13 份同 kind 文档被误标的问题）。

### 数据修复

一次性脚本清洗 12 条历史误判终态：补记缺失的判死事件（`event_type=repair_note`，如实标注为「重建」而非伪造转换），再走 T22 改判回 `(completed, review)` —— 落到 review 队列，人工仍可驳回。改前备份 `~/.mio_taskhub/backups/taskhub.db.bak-*-pre-misjudge-repair`。

## v0.3.0 (2026-09-17)

### Added — 接口契约「事无巨细」标准（`api` kind）

用户要求：接口契约必须足够详细，记录每个细节。落地为全链最严的文档规格：

- **模板重写**（`doc_chain.py`）：`api` 骨架由 4 节扩到 **21 节**，含一份可直接整块复制的完整接口范例（请求参数/响应字段/错误码/示例四个子块 + curl 与 JSON 示例）。
- **10 个必需章节**（缺失即 error）：文档信息与范围 · 环境与基础地址 · 认证与鉴权 · 通用响应结构 · 统一错误码 · 字段命名与类型规范 · 幂等性与重试 · 接口清单 · 接口明细 · 变更记录。
- **11 个建议章节**（缺失 warn，只扣分）：版本策略 · 通用请求头 · 分页/排序/过滤 · 时间/数值/空值约定 · 枚举全集 · 限流与配额 · 超时与并发 · 文件上传与下载 · 安全与脱敏 · 兼容性与废弃策略 · 附录。
- **必需表格**：通用响应结构 ≥1 行、统一错误码 ≥1 行、接口清单 ≥1 行。
- **逐接口子块强制**（新增 `detail_rule` 机制）：`接口明细` 下每个 `### <Method> <Path>` 必须含 **请求参数 / 响应字段 / 错误码 / 示例** 四个 `#### ` 子块，且前三者各需 ≥1 行数据——缺任一项报 error 并附可执行修法。
- `SECTION_HINTS['api']` 扩到 21 条逐节写作指引，直接喂给 revision 指令（写作者照做即可）。

### Added — 质量规格两个新机制（`doc_quality.py`）

- **`recommended`（建议章节层）**：`QUALITY_SPEC[kind]['recommended']` 列出建议 H2 章节，缺失记 **warn**（不阻断门控，但计入评分）——让「该写但可以不写」和「必须写」分开。
- **`detail_rule`（明细单元完整性）**：`{'section', 'unit_label', 'min_units', 'unit_blocks'}`，把指定 H2 章节下的每个 `### ` 单元切开，逐个检查单元内必须存在的 `#### ` 子块及其表格数据行数；`unit_blocks` 值为 0 表示只要求子块存在（「示例」多为代码块）。
- 新增 `_split_by_heading(lines, prefix)` helper，供 H3/H4 分层解析复用。

### Fixed — 文档质量

- **`_table_data_rows` 数据行数始终多算 1 行**：旧实现仅在「表头下一行是分隔行」时置 `in_table=True`，导致**分隔行自身被计入数据行**——0 数据行的空表报告为 1 行，`min_table_rows: 1` 形同虚设，空表也能通过填充度校验。新实现先排除分隔行、再排除表头行，剩余才是数据行。该缺陷因接口契约的逐接口行数校验而暴露。

### Fixed — 可观测性两块裸 SQL 长期坏死（root cause 同类）

后台日志里 `Failed to evaluate stalled tasks` 每 2 分钟刷一次，且前端「任务链路」面板恒为空——根因是同一类「裸 `text()` SQL 绕过 ORM」的缺陷：

- **`observability/remediation.py`**：`state NOT IN ('completed','failed','cancelled')` 用了**小写字面量**，而枚举列存的是大写枚举名（`QUEUED`/`COMPLETED`/…），导致状态过滤完全失效、匹配全表；且原生 SQL 返回的 `created_at` 是字符串，直接取 `.tzinfo` 抛 `AttributeError`，函数**永远返回空、自动重排功能从未真正工作**。
- **`observability/task_trace.py`**：`get_task_trace` / `get_task_traces_summary` 三处叠加缺陷：①`Session.exec(stmt, params)` 不接受位置参数（**TypeError**，两函数直接全废）；②状态过滤同样小写字面量（`get_task_traces_summary` 永远匹配不到行）；③裸 SQL 返回的 `created_at/completed_at` 当 `datetime` 用（`.isoformat()` / `str - str` 必崩），且 `event_metadata` 查成 `event_metadata`（DB 列名实为 `metadata`）——导致 `GET /api/v1/traces/{id}` 恒 404、`GET /api/v1/traces` 恒 `[]`。
- **修复做法**：状态过滤改用大写枚举名；时间比较一律在 SQL 里用 `julianday()`（不再取回 Python）；裸 SQL 时间值经新增的 `utils.parse_utc()` 统一转 aware UTC datetime；`db.exec(...)` 改 `db.execute(...)`；`event_metadata` 用 `metadata AS event_metadata` 取列并 JSON 还原。
- **自动重排语义收窄（防误伤）**：旧逻辑「非终态 + 超 30 分钟」会把整块排队中的 `QUEUED` 任务当卡住反复重排（当前库有 93 条）。现判定改为**只有 CLAIMED/RUNNING/RETRYING 且超过阈值、且重试额度未耗尽**才算卡住；阈值可用 `MIO_TASKHUB_STALL_MINUTES` 覆盖。当前库活跃态为 0 条，**修复后零行为变化**。
- **测试补强**：旧 `test_remediation.py` 只断言「返回 list」，给 bug 背书；新增断言真实卡住任务被识别、QUEUED 不被误判、重试额度耗尽即停、阈值可配；新增 `test_task_trace.py` 断言 span 时长数值化、summary 含已完成任务且时长为数值、metadata 还原为 dict、未完成任务不出现。`_table_data_rows` 修复一并保留。

### Added — 接口契约纳入生命周期，质量门控真正生效

此前 `api` 没有生命周期，而质量门控挂在 `set_doc_status` 上 —— 接口契约的质量 error 只出现在 `PUT /doc` 响应与质量报告里，**不阻断任何推进**，等于「最严的规格 + 最软的约束」。现补齐：

- **`api` 加入 `DOC_LIFECYCLE`**，与 `spec` 同形：`draft → review → approved`（初始态 `draft`，首次写入自动落位）。文档生命周期由 7 类扩到 **8 类**（含 Task 自身共 9 条状态机）。
- 效果：`POST /tasks/{id}/doc/api/status` 推进到 `review`/`approved` 时要求 `errors == 0`，**空心的接口契约被 422 拒绝**；`force: true` 仍可绕过并落事件留痕。
- `tests/test_doc_lifecycle.py` 覆盖断言由 7 类扩到 8 类；`test_doc_quality.py` 新增门控端到端用例（空心契约 422 → force 通过；填满的契约 draft→review→approved 全通）。

### Tests — 文档体系

- `test_doc_quality.py` 新增 5 个用例：接口契约是全链最严规格（必需章节数最多 + detail_rule 齐备）、模板覆盖全部必需与建议章节且逐节报「未填写」、填满的参考契约 0 error 满分、`detail_rule` 三种失败形态（缺子块/子块空表/无接口小节）、`recommended` 缺失只 warn 不阻断而必需章节缺失仍 error。新增 `GOOD_API` 参考契约夹具（21 节齐全）。
- 另新增 api 生命周期质量门控用例（见上），合计 6 个新用例。

### Added — 任务文档体系（首轮，已提交于 6dc88a8）

- **22 类文档 kind**：新增 `doc_paths.py` 作为唯一事实源（`DOC_KINDS`），`Task.doc_paths` 以 kind→相对路径的 JSON 落库，覆盖七件套主链 + 工程记录 + 复用资产三类。
- **七件套文档链**（`doc_chain.py`）：`requirement → architecture → spec → data-model → api → test → runbook`。`POST /api/v1/tasks/{id}/docs/scaffold` 一键补骨架，**默认不覆盖已有文件**，支持 `kinds` 指定子集与 `overwrite`；骨架自带跨链导航头、`FR-n`/`TC-n`/`ADR-n` 追溯编号与 TODO 占位，正文按分点+换行书写。
- **文档生命周期状态机（首轮 7 类，后追加 api 至 8 类）**（`doc_lifecycle.py`）：requirement `draft→approved`、spec `draft→review→approved`、decision `proposed→accepted→superseded`、plan `draft→approved→done`、test `planned→passed/failed`、milestone `planned→released`、incident `open→resolved→closed`，加 Task 自身。规则为严格向前：不许回退、不许跳级、终态不可改判。新增 `Task.doc_statuses` JSON 列（含迁移），首次写入自动初始化初始态。
- **质量保证三层闭环**（`doc_quality.py`）：
  - 写时 lint —— 每次 `PUT /doc` 同步检查必备章节、表格有效数据行、TODO 残留、分点书写，按 `score = max(0, 100 - 20*errors - 5*warns)` 评分并随响应返回；
  - 状态门控 —— 推进到 `review`/`approved`/`done` 要求 `errors == 0`，`force=true` 可强推但落事件留痕；
  - 修订指令 —— `GET /doc/{kind}/revision` 返回逐条可执行的修改要求，附章节写作指引与模板片段，把评分反哺回写作阶段。
  - 追溯矩阵 —— `traceability()` 校验 `requirement` 的 `FR-n` 是否被 `test` 的 `TC-n` 覆盖。
- **4 个新 MCP 工具**：`taskhub_scaffold_docs` · `taskhub_set_doc_status` · `taskhub_doc_quality` · `taskhub_doc_revision`（总数 33 → **40**）。
- **5 个新 REST 端点**：`POST /docs/scaffold`、`POST /doc/{kind}/status`、`GET /doc/statuses`、`GET /doc/{kind}/revision`、`GET /doc/quality`（总数 93 → **119**）。
- **阶段产出物门控扩展**：`STAGE_ARTIFACT_REQUIREMENTS` 由单 `document_kind` 改为 `document_kinds` 列表，新增 `brainstorming → requirement`、`implementing → changelog` 两个此前缺失的门，`design` 明确要求讨论会话；`GET /stages/requirements` 同时返回 `document_kinds` 与兼容字段 `document_kind`。
- **前端文档面板**（`DocPanel.jsx`）：22 类 kind 分类展示、七件套一键起骨架、质量分与生命周期状态徽标；新增 `web/src/stageDocs.js` 作为阶段产出物要求的单一来源，阶段推进弹窗由硬编码改为查表（无要求的阶段不再弹空输入框）。

### Added — 结构化可观测性

- **`mio_taskhub/observability/report.py`**：新增 agent 可直接消费的结构化快照，避免解析 Prometheus 文本。
  - `GET /api/v1/observability/report` —— database / http / process / threads / tasks / agents / slo / alerts / insights 全量快照。
  - `GET /api/v1/observability/integrity` —— integrity_check 风格总报告，逐组件 PASS/WARN/FAIL + `overall_status` + 完整 snapshot。
- `GET /api/v1/observability/summary` 改从 `collect_observability()` 取值，不再正则解析指标文本。
- 前端新增**可观测性视图**（`ObservabilityView.jsx`）。

### Fixed — 可观测性

- **`middleware.py` 错误率统计错误**：原先把所有响应都计入 `_error_count`，导致 2xx 也被算作错误；改为仅 4xx/5xx 计数。此前 `HighHttpErrorRate` 告警与可用性 SLO 会基于虚高的错误率误报。
- **`slo_history.py` SQLModel 参数传递**：`db.exec(text, {...})` 改为 `params={...}`，否则查询与清理静默失效。
- **`insights.py` 重复洞察刷屏**：60s 一次的评估会把同一个未确认洞察反复插入；现按 `title + severity + acknowledged=0` 去重，已存在则直接返回原记录。
- `AlertManager` 的 `HighHttpErrorRate` 告警文案改为中文并带百分比与请求数。

### Changed

- `PUT /api/v1/tasks/{id}/doc` 响应新增 `quality` 与 `status` 字段。
- 任务创建/更新接口统一走 `doc_paths`，`spec_path` / `plan_path` 降级为兼容入口并保留双向同步（`merge_doc_paths` / `sync_legacy_fields`）。
- `/dashboard` 移除对 `web/dist/dashboard.html` 构建产物的依赖，改由 `main.py` 内置 `_DASHBOARD_HTML` 兜底（`web/dist/dashboard.html` 已删除，876 行）。

### Tests — 可观测性

- 新增 `tests/test_doc_chain.py`（骨架生成与不覆盖语义）、`tests/test_doc_lifecycle.py`（状态机合法性）、`tests/test_doc_quality.py`（质量评分、门控、修订指令）、`tests/test_task_doc_paths.py`（22 类 kind 与兼容字段）。
- 用例总数 509 → **603**（53 文件 / 7,585 行）。

### Docs

- 重写 `README.md`：修正全部统计数字（后端 76 模块 / 13,749 行、119 端点、40 MCP 工具、前端 40 文件 / 11,694 行、603 用例），新增「任务文档体系」独立章节（22 类 kind / 七件套链 / 9 类生命周期 / 质量三层闭环 / 端点表 / 典型闭环），更新阶段门控表、MCP 工具分组表与目录结构。

---

## v0.2.1 (2026-09-14)

### Fixed
- `/dashboard` 在 `web/dist/dashboard.html` 缺失时返回 404（且错误文案误写为 "Landing page not found"）。现改为回退到 `main.py` 中已有的内置仪表盘 HTML（`_DASHBOARD_HTML`），无需构建产物即可访问。

### Docs
- 重写 `README.md`：补充核心模型（状态机 / 7 段研发阶段 / 想法生命周期 / 实体表）、三种部署方式、MCP 33 工具分组、Web UI 视图表、可观测性与 SLO、完整环境变量表、7 个后台线程、打包踩坑与已知限制。
- 修正文档中的 API 文档地址：为 `/docs`（非 `/api/v1/docs`）。

---

## v0.2.0 (2026-09-11)

### Observability Overhaul (P0-P3)
- **OpenTelemetry**: Auto-instrumentation for FastAPI, SQLAlchemy, httpx
- **Built-in alerting**: `AlertManager` with thread/HTTP health rules
- **Alerts API**: `GET /api/v1/alerts` returns active alerts
- **Dashboard**: Self-contained HTML dashboard at `/dashboard` with Chart.js
- **USE metrics**: CPU, memory, threads, DB connection pool utilization
- **Business metrics**: Success/failure/cancel rates, throughput, P50 latency, retry stats
- **Dependency latency**: SQLite/Git/MCP call tracking (avg/P50/P90/P99/errors)
- **Thread health**: Heartbeat age, consecutive failures, alive status per thread
- **SLO/SLI**: Service level objectives with compliance metrics
- **Structured logging**: Trace context correlation (trace_id/span_id)
- **Log query API**: `GET /api/v1/logs` with level/logger filtering

### Prometheus Alert Rules
- `packaging/alerts.yml`: 24 alerting rules (critical/warning/info)
- `packaging/alertmanager.yml`: Alertmanager production config
- `packaging/prometheus.yml`: Prometheus scrape config

### New Modules
- `otel.py`: OpenTelemetry initialization and instrumentation
- `alerts.py`: Built-in AlertManager (no external dependencies)
- `dep_metrics.py`: Dependency latency collector with track() context manager

### Metrics Added
```
# System
taskhub_process_cpu_percent
taskhub_process_memory_rss_bytes / vms_bytes / percent
taskhub_process_threads
taskhub_db_pool_size / checked_out / checked_in / overflow / utilization
taskhub_thread_pool_alive / total / utilization

# Business
taskhub_task_success_rate / failure_rate / cancel_rate
taskhub_task_terminal_total
taskhub_task_throughput_24h / 7d
taskhub_task_retries_total / avg / max
taskhub_task_avg_completion_seconds / p50_seconds
taskhub_agent_utilization

# Dependencies
taskhub_dep_latency_count / errors / avg_ms / max_ms / p50_ms / p90_ms / p99_ms
taskhub_dep_error_rate

# SLO
taskhub_slo_availability_30d / target / breach
taskhub_slo_error_budget_remaining
taskhub_slo_latency_avg_ms_1d / breach
```

### API Endpoints Added
- `GET /dashboard` - Self-contained monitoring dashboard
- `GET /api/v1/alerts` - Active alert status
- `GET /api/v1/logs` - Log query with filtering

---

## v0.1.0 (2026-09-09)

### Initial Release
- FastAPI + SQLite task coordination hub
- Multi-agent dispatch with state machine
- MCP server for agent integration
- WebSocket real-time notifications
- ADR projection to Git
- Background thread management with heartbeat
- SQLite auto-backup (hourly snapshots)
- HTTP rate limiting (120 req/min/IP)
- PyInstaller EXE build
- Web SPA dashboard