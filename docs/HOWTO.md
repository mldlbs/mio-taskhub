# opencode 接入 mio-taskhub 使用指南

## 启动方式

```bash
# 1. 启动任务中心服务
cd mio-taskhub && python -m uvicorn mio_taskhub.main:app --port 48620

# 2. 服务运行在 http://127.0.0.1:48620
#    - Web UI: http://127.0.0.1:48620/
#    - API: http://127.0.0.1:48620/api/v1
```

## opencode 怎么用

### 方式一：命令行直接调用

```bash
# 注册 agent
python agent_wrapper.py opencode register

# 领取任务
python agent_wrapper.py opencode claim
# 输出: Claimed task: xxxxx (run=yyyyy)

# 执行中... 发心跳
python agent_wrapper.py opencode heartbeat yyyyy 50

# 提交结果
python agent_wrapper.py opencode result yyyyy success "完成描述"
python agent_wrapper.py opencode result yyyyy fail "失败原因"

# 查看看板
python agent_wrapper.py opencode list
```

### 方式二：opencode prompt 中调用

在 opencode 的 prompt 里写：

```
请使用 mio-taskhub 领取并执行任务：
1. 先注册: python mio-taskhub/agent_wrapper.py opencode register
2. 领取任务: python mio-taskhub/agent_wrapper.py opencode claim
3. 执行任务...
4. 过程中发心跳: python mio-taskhub/agent_wrapper.py opencode heartbeat <run_id> <进度百分比>
5. 完成后提交: python mio-taskhub/agent_wrapper.py opencode result <run_id> success "结果描述"
```

### 方式三：多 agent 协作

```bash
# 不同 agent 注册不同名称
python agent_wrapper.py codex register
python agent_wrapper.py claude-code register
python agent_wrapper.py hermes register

# 各自领取任务（FIFO + 优先级）
python agent_wrapper.py codex claim      # codex 领到高优先级任务
python agent_wrapper.py claude-code claim # claude-code 领下一个
```

## 核心 API

| 动作 | 方法 | 路径 |
|---|---|---|
| 注册 | POST | /api/v1/agents/register |
| 领取任务 | POST | /api/v1/tasks/claim?agent=xxx |
| 心跳 | POST | /api/v1/runs/{id}/heartbeat |
| 提交结果 | POST | /api/v1/runs/{id}/result |
| 查看任务 | GET | /api/v1/tasks |

## Web UI 看板

浏览器打开 http://localhost:48620 可以看禅道风格看板：
- 6 列状态：待处理 → 已领取 → 进行中 → 已完成 → 失败
- 拖拽任务卡片改变状态
- 实时 WebSocket 刷新
- 创建任务表单

## 打包绿色版分发给小白（免装 Python）

```bash
pip install pyinstaller
# 一键打包：构建前端 + PyInstaller + 复制分发文件 + 压缩 zip + 组装安装包
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1
# 产物:
#   dist/mio-taskhub-v3/              绿色版目录
#   dist/mio-taskhub-win64.zip        绿色版 zip（自动更新用）
#   dist/mio-taskhub-installer/       独立安装包（setup.exe + uninstall.exe + zip + 安装说明）
#   dist_installer/                   单独的安装器 / 卸载器 exe
#
# 手工只重建了单个产物时，必须重跑一致性校验：
python scripts/check_installer_bundle.py
```

产物包含：
- `mio-taskhub.exe`：单 EXE，按参数分派三种功能
  - 双击 → 任务中心 hub，自动开浏览器
  - `mio-taskhub.exe mcp` → MCP 服务端（agent 经 stdio 调用，人不用碰）
  - `mio-taskhub.exe widget` → 置顶浮动任务面板（可收进托盘）
- `mio-taskhub-widget.bat`：双击启动浮动面板的入口
- `setup-agent.bat`：一键识别并配置 opencode / claude code / codex / workbuddy（双击）
- `uninstall.exe`：卸载程序（图形向导 / `--silent` 静默卸载）
- `使用说明.txt`：给最终用户的图文说明

小白使用流程：① 双击 mio-taskhub.exe → ② 双击 setup-agent.bat →
③ 重启 agent 说"使用 mio-taskhub 领取任务"。全程免 Python、免联网。

### 安装器 / 卸载器（独立安装包）

不想让用户手动解压、或希望能在 Windows「应用和功能」里卸载时，用安装包：

| 资产 | 路径 | 说明 |
|------|------|------|
| 安装器 | `dist/mio-taskhub-installer/mio-taskhub-setup.exe` | 双击 → 图形安装向导；`--silent` 静默安装 |
| 卸载器 | 随 payload 分发到安装目录的 `uninstall.exe` | 图形卸载向导；`--silent` 静默卸载 |

实现落在 `mio_taskhub/installer/`：

| 模块 | 职责 |
|------|------|
| `paths.py` | 路径与常量单一事实源（安装目录/注册表键/关键项） |
| `install.py` | 安装与卸载的**核心动作**（与 UI 解耦，可单测） |
| `registry.py` | 「应用和功能」HKCU 注册表项（用户级，免管理员） |
| `shortcuts.py` | 开始菜单 / 桌面 .lnk 快捷方式（COM，不依赖 pywin32） |
| `processes.py` | 卸载前清理占用安装目录的进程 |
| `payload.py` | payload 定位 / 解压 / 校验（契约见 `mio_taskhub/payload_layout.py`） |
| `gui.py` | Tkinter 安装向导 + 卸载向导（自绘控件，深色主题） |
| `cli.py` | 无界面命令行入口（CI / 批量部署） |
| `entry_support.py` | 无黑框 exe 的 stdio 重绑（`sys.stdout` 为 None 的兜底） |

打包入口：`packaging/setup_entry.py`（→ `mio-taskhub-setup.exe`）与
`packaging/uninstall_entry.py`（→ `uninstall.exe`），spec 是 `mio-taskhub-installer.spec`。

**改安装器时务必注意的坑**：

- **无黑框 exe 的 `sys.stdout` 是 `None`** —— 直接 `print()` 会抛 AttributeError。
  所有输出走 `entry_support.ensure_stdio()` / `emit()`。
- **卸载器运行在待删目录里** → Windows 锁住自己的 exe 映像，目录删不掉。
  `_self_delete_dir()` 派发一个**带重试**的 .bat（`rmdir` 失败就等 2 秒再试）。
  两个细节：① 必须用 `Popen` 不能用 `run`（run 会等 cmd 结束 → 死锁）；
  ② 批处理里的 for 变量要写 `%%i`，且用 f-string 生成（`%` 格式化会把 `%%i` 变成 `%i`）。
- **派发的 cmd 必须设 `cwd` 到待删目录之外** —— cmd 继承当前工作目录，
  会把要删的目录自己锁住（实测：文件全删了，目录壳子留着）。
- **卸载器被改名成 `uninstall.exe` 时必须默认走卸载流程** —— 安装器会把自身
  复制进安装目录当卸载器用（`setup_entry.py` 的 `_invoked_as_uninstaller()`）。
- **进程路径匹配不能用 `realpath("")`** —— 裸 exe 名（无目录部分）经
  `realpath("")` 会变成**当前工作目录**，把 Windows 的 `Registry` 等系统进程
  误判成占用者 → 永远删不掉（`processes._is_under()` 已挡）。
- **自更新会整体替换安装目录** → 卸载器会被一起换掉。
  `update/apply.py` 的 `_preserve_uninstaller()` 在替换前把它拷进 staging。
- **payload 布局契约只有一份** —— `mio_taskhub/payload_layout.py`。
  历史发布的 zip 顶层带一层 `mio-taskhub-v3/`，而更新器按「根目录即内容」校验
  → **自动更新装不上**（2026-10-21 实测）。现在解压统一自动剥离顶层前缀。
- **payload 搜索路径必须含「exe 自身所在目录」** —— 真实分发形态是
  setup.exe 与 zip 同目录。原先没排这一项 → 打包后的静默安装报「未找到安装包」
  （2026-10-21 实测）。`payload._exe_dir()` 已排到最前。

**改 GUI 时务必注意的坑**（Tkinter 天生爱坑）：

- **不要用 ttk 默认控件** —— 原生 Checkbox / Entry 在深色底上露出浅色系统色，
  看着像拼贴。`gui.py` 里输入框 / 按钮 / 复选框都是 `tk.Canvas` 自绘。
- **`Canvas.delete("all")` 会把 `create_window` 嵌的 Entry 一起删掉** →
  输入框变 1×1 隐藏、路径框看着是空的。只删图形元素：`delete("rounded")` + 打 tag。
- **Canvas 里放子控件要用 `grid` 固定列宽** —— `pack(expand=True)` 会让输入框
  吃掉整行，把旁边的按钮压成 1×1（看不见）。
- **显示文本要用独立 StringVar** —— Entry 若直接绑真实路径变量，往里 insert
  文本会**回写污染真实路径**。`gui._Field` 用 `_display_var` 显示、
  `_var` 保存真值。
- **不要用 `messagebox`** —— 模态框在部分 Win10 上标题栏会闪「(未响应)」。
  改用界面内自绘的提示条 `_Notice`。
- **后台线程回 UI 线程用 `_ui_call()`** —— `root.after()` 在主线程不在 mainloop 时
  抛 `RuntimeError: main thread is not in main loop`，且该异常发生在工作线程里 →
  界面永远卡在「正在安装」。
- **工作线程绝对不能碰 Tk 对象**（更隐蔽的坑）—— 从后台线程调 `var.get()`
  同样抛 `RuntimeError: main thread is not in main loop`。
  实测症状：安装**根本没开始**，界面却卡在「正在安装…」，日志一片空白
  （异常处理器自己调 `root.after` 也抛同样的错 → 连错误都显示不出来）。
  正确做法：**主线程**把 Tk 变量读成纯 Python 值再传给工作线程；
  工作线程 → 主线程用**队列**交接（`_Base.post()` / `_Base._drain_queue()`），
  完全不碰 Tk 的线程模型。
- **窗口销毁要清 after 队列** —— Tk 的 after 队列在窗口销毁后仍持有已删除的
  Tcl 命令名，残留回调报 `invalid command name ... (after script)`。
  `_close_now()` 里 `after_cancel` 掉自己排程的回调。
- **「安装包」那行默认不显示** —— 真实分发时包就在 setup.exe 旁边，自动找到，
  用户什么都不用做。让人去「选安装包」是多余且让人困惑的一步。
  只有自动定位失败才显示该行作为兜底。
- **长路径要友好显示** —— 完整路径太长会截断成 `C:\Users\admin\AppData\L...`。
  `_Field._friendly_name()` 只保留末两段（`…\Programs\mio-taskhub`），
  完整路径走悬停提示。
- **验证 GUI 必须用真正的 `mainloop()`** —— 用 `root.update()` 手动循环会报
  `main thread is not in main loop`（测试脚本的锅，不是程序 bug）。
  但注意：**真 mainloop 下 `root.after` 是可用的** —— 所以「手动 update 循环
  报错」推不出「生产也有问题」，要分清是脚本的锅还是程序的锅。
- **兜底行不能放在 `place()` 布局的容器里** —— 卡片内容区用 `place()` 定位，
  在里面 `pack()` 的控件**不会显示**。兜底行要放在卡片**外面**的独立槽位。
- **不要用 `pack(before=...)` 定位** —— `winfo_children()[-1]` 可能是被
  `pack_forget()` 掉的控件（如日志面板），Tcl 报 `window "..." isn't packed`。
  靠 pack 顺序确定位置。
- **Entry 是不透明的，嵌在 Canvas 上会盖住占位符** —— 空值时把 Entry 的
  window item `state="hidden"`，否则占位符只剩尾巴（显示成「ip」）。

### 打包 / 分发产物的三个坑（2026-10-21 实战）

1. **payload zip 里的 `uninstall.exe` 必须是「当次构建」的那份** ——
   重建了 `dist_installer/uninstall.exe` 却没重打 `dist/mio-taskhub-win64.zip`，
   用户装到机器上就是**旧卸载器** → 静默卸载异常退出（rc=23、目录还在）。
   手工重建单个产物最容易踩这个。
   对策：`scripts/check_installer_bundle.py` 校验「安装器 exe ↔ payload zip ↔
   `dist/mio-taskhub-installer/`」三者同源；`build.ps1` 步骤 6 强制跑一次。

2. **无黑框 exe 的 traceback 会被吞** —— PyInstaller 的
   `disable_windowed_traceback=True` 让异常**完全静默**：用户只看到
   「进程秒退、目录还在、退出码 23」。
   对策：`cli.main()` 加顶层 try/except，异常落 `installer.log`（含 traceback），
   返回码 **23** 区分于业务失败的 1。

3. **`taskkill /F /T` 会误伤** —— `/T` 连带杀掉目标 pid 的整棵子孙树，
   顺着父链往上会波及**启动卸载器的人**（实测把 bash 一起杀掉，
   于是卸载器「秒退」且没留下任何日志）。
   对策：只杀目标 pid 本身（`/F` 不带 `/T`），子孙进程由
   `psutil.children(recursive=True)` 单独枚举、**子先父后**地杀。

4. **杀进程的顺序：父在前，子在后** —— hub 是 supervisor→worker 双进程，
   supervisor 会在 worker 意外退出时**重新 spawn** 它（退避重试）。
   先杀 worker 正好给了它重启的窗口 → 新 worker 又把安装目录钉住 →
   目录永远删不掉。对策：按**祖先链深度**排序，父进程先杀；
   且**不要连带杀子进程**（父先死就不会有重启）。

5. **杀之前重新核对进程身份** —— `psutil.Process(pid)` 只缓存 pid，
   目标若在两次调用之间退出，pid 可能被系统**复用**给别的进程 →
   `taskkill /PID` 就会杀掉无辜进程（实测把用户的 shell 一起杀掉）。
   对策：杀掉前重新读一次 `exe`，与目标路径比对，不符就跳过。
   这条守卫实测确实在触发（installer.log 里有 `skip pid=... pid 被复用`）。

6. **【最隐蔽】psutil 杀成功后不要再 taskkill 同一个 pid** ——
   `psutil.Process(pid).kill()` 紧接 `_taskkill_tree(pid)` 是错的：
   进程已死 → **pid 被系统回收** → 下一个进程可能立刻复用 →
   `taskkill /F /PID` 命中**被复用的新 pid** → 杀掉无辜进程。
   实测被杀的是**启动卸载器的 shell** → 连带把卸载器自己带走 →
   **卸载器在 `main()` 之前就死了**（连日志都没写）→ 「进程秒退、
   目录还在、退出码 23」，且**偶发**（取决于 pid 是否被复用）。

   对策：psutil 杀成功后**不再 taskkill**；杀完 `wait()` 确认退出；
   只有 psutil 失败时才 taskkill 兜底，且兜底前再核对一次身份。

   **症状识别**：日志里**连 `[entry]` 行都没有** = 死在 `main()` 之前
   （bootloader 阶段），不是业务代码的问题。

### 自动更新与发版

程序启动后会自动检查 GitHub Releases 是否有新版本；有新版本时托盘提示、面板顶部出现"立即更新"条，
点击后自动下载、校验（sha256）、替换并重启，失败自动回滚到上一版本。
更新日志见 `%USERPROFILE%\.mio_taskhub\update\apply.log`；设 `MIO_UPDATE_DISABLED=1` 可关闭自动检查。
其它开关：`MIO_UPDATE_CHANNEL`（`prerelease` 接收预发布）、`MIO_UPDATE_INTERVAL_H`（默认 6 小时）、
`MIO_UPDATE_BASE_URL`（覆盖更新源，内网/测试用）。用户数据（数据库/日志）不受更新影响。

发版（需已安装并登录 `gh` CLI）：

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/release.ps1 -Version 0.4.0 -Notes "本次更新说明"
```

脚本会：`build.ps1` 产出 `dist/mio-taskhub-win64.zip` → 计算 sha256/size →
生成 `dist/latest.json`（客户端更新契约）→ `gh release create vX.Y.Z`（附 zip + latest.json）。

维护要点：
- 唯一打包 spec 在根目录 `mio-taskhub.spec`（单 EXE：hub/mcp/widget 三合一 + excludes 瘦身），不要复制副本
- 统一入口在 `packaging/run.py`，按 `sys.argv[1]` 分派到 `run_hub.py`（hub）/ `mio_taskhub.mcp_server`（MCP）/ `run_widget.py`（面板）
- 一键打包脚本 `packaging/build.ps1`：先 `npm run build` 前端 → PyInstaller → 复制 setup 脚本/使用说明/workbuddy skill/widget 入口 → 压缩 zip
- 一键配置脚本 `packaging/setup-agent.ps1`：自动检测 opencode/claude code/codex/workbuddy 并写配置
  （MCP 启动命令统一为 `mio-taskhub.exe mcp`，配置脚本已带 `mcp` 参数）；
  注意 .ps1 必须存为 **UTF-8 with BOM**，否则 PowerShell 5.1 按 ANSI 读取中文会解析失败；
  hash 键 `mio-taskhub` 带连字符必须加引号；向已有 JSON 对象追加块时记得补逗号
- 无黑框模式（console=False）下 stdout 为 None，需在 run.py 里重定向到日志文件，否则 uvicorn 日志配置会崩
  （MCP 分支走 stdio 不做重定向，windowed 下管道句柄仍有效，已实测）
- 前端改动后由 build.ps1 自动先 `cd web && npm run build` 再打包（Web UI 从 `web/dist` 打包进 exe）
- 排除重型依赖（torch/pandas/scipy 等）见 spec 的 excludes，防止体积膨胀到 GB 级
- 打包要求 Windows 64 位；Mac/Linux 用户需在对应系统重打

## mio-intelligence 创意想法 → taskhub 同步

**工具沿革**：`mio.idea.generate` 只存在于早期本机副本（`~/.config/opencode/mcp-servers/`，非 LLM 策略展开器），已发布 runtime 0.13.3 **没有**该工具——当前生成统一走 `creativity generate`（≥2 个 sources）。
历史存量想法在 `~/.mio-intelligence/ideas.jsonl`（12 条），批量同步脚本仍可消费。

### 方式一：Agent 实时同步（推荐）

Agent 在对话中生成想法后，立即调用 `taskhub_add_idea` 推送到 taskhub：

```
1. 调用 mio-intelligence_mio_creativity_generate(sources=[{name, content}, ...])（≥2 sources）
2. 对返回的每个 idea（title/idea/strategy），调用 taskhub_add_idea(title=..., description=..., labels=["mio-intelligence", "strategy:<策略>"])
3. 后续可在 taskhub 中发酵、讨论、拆解为任务
```

前端「模板生成」（`POST /api/v1/ideas/templates/generate`）已同义映射到 `mio --json creativity generate`，`sync_to_hub=true` 时落库为 taskhub Idea；**MCP tools/call 对该长任务回空包，勿经 MCP 调**。
端点自动注入 observer 近期洞察（`mio --json observer insights`，top2，fail-open）作为额外 sources，把观察→趋势→洞察链的产出带进创意环节；输出强制简体中文（上游 system 为英文，语言门禁只能随 sources 下发）。

### 方式二：批量同步脚本

```bash
# 预览待同步的想法
python scripts/ideas_sync.py --dry-run

# 执行同步
python scripts/ideas_sync.py

# 指定项目
python scripts/ideas_sync.py --project my-project
```

脚本会：
- 扫描 `~/.mio-intelligence/ideas.jsonl`
- 按 title 去重（已存在的跳过）
- 调用 taskhub REST API `POST /ideas` 创建 Idea（status=new）
- 记录同步状态到 `~/.mio-intelligence/idea_sync_state.json` 避免重复

### 数据映射

| ideas.jsonl 字段 | taskhub Idea 字段 | 说明 |
|---|---|---|
| `title` | `title` | 直接映射 |
| `description` | `description` | 直接映射 |
| `provenance.strategy` | `labels` | 追加 `strategy:SCAMPER` 等标签 |
| `goal` | `description` 前缀 | 追加到描述开头 |
| — | `status` | 固定 `new` |
| — | `labels` | 追加 `mio-intelligence`, `auto-generated` |
