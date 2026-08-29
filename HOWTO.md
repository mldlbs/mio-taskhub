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
# 一键打包：构建前端 + PyInstaller + 复制分发文件 + 压缩 zip
powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1
# 产物: dist/mio-taskhub/ （绿色版目录）+ dist/mio-taskhub-绿色版.zip
```

产物包含：
- `mio-taskhub.exe`：单 EXE，按参数分派三种功能
  - 双击 → 任务中心 hub，自动开浏览器
  - `mio-taskhub.exe mcp` → MCP 服务端（agent 经 stdio 调用，人不用碰）
  - `mio-taskhub.exe widget` → 置顶浮动任务面板（可收进系统托盘）
- `mio-taskhub-widget.bat`：双击启动浮动面板的入口
- `setup-agent.bat`：一键识别并配置 opencode / claude code / codex / workbuddy（双击）
- `使用说明.txt`：给最终用户的图文说明

小白使用流程：① 双击 mio-taskhub.exe → ② 双击 setup-agent.bat →
③ 重启 agent 说"使用 mio-taskhub 领取任务"。全程免 Python、免联网。

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

mio-intelligence 的 `mio.idea.generate` 工具会把生成的创意想法存到 `~/.mio-intelligence/ideas.jsonl`。
这些想法可以通过两种方式同步到 mio-taskhub 的 Idea 系统：

### 方式一：Agent 实时同步（推荐）

Agent 在对话中生成想法后，立即调用 `taskhub_add_idea` 推送到 taskhub：

```
1. 调用 mio.idea.generate(goal="...", context="...", numIdeas=3)
2. 对返回的每个 idea，调用 taskhub_add_idea(title=..., description=..., labels=["mio-intelligence", "strategy:SCAMPER"])
3. 后续可在 taskhub 中发酵、讨论、拆解为任务
```

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
