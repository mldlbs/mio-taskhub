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
python -m PyInstaller mio-taskhub.spec --noconfirm   # 产物在 dist/mio-taskhub/
Compress-Archive -Path dist/mio-taskhub/* -DestinationPath dist/mio-taskhub-绿色版.zip
```

产物包含：
- `mio-taskhub.exe`：任务中心 hub，双击启动 + 自动开浏览器
- `mio-taskhub-mcp.exe`：MCP 服务端（agent 自动调用，人不用碰）
- `setup-agent.bat`：一键识别并配置 opencode / claude code / codex（双击）
- `使用说明.txt`：给最终用户的图文说明

小白使用流程：① 双击 mio-taskhub.exe → ② 双击 setup-agent.bat →
③ 重启 agent 说"使用 mio-taskhub 领取任务"。全程免 Python、免联网。

维护要点：
- 打包入口脚本在 `packaging/run_hub.py`（启动 hub）和 `packaging/run_mcp.py`（MCP）
- 一键配置脚本 `packaging/setup-agent.ps1`：自动检测 opencode/claude code/codex/workbuddy 并写配置
  （workbuddy 写 `~/.workbuddy/mcp.json` 的 mcpServers）；
  注意 .ps1 必须存为 **UTF-8 with BOM**，否则 PowerShell 5.1 按 ANSI 读取中文会解析失败；
  hash 键 `mio-taskhub` 带连字符必须加引号；向已有 JSON 对象追加块时记得补逗号
- 无黑框模式（console=False）下 stdout 为 None，需在 run_hub.py 里重定向到日志文件，否则 uvicorn 日志配置会崩
- 前端改动后需先 `cd web && npm run build` 再打包（Web UI 从 `web/dist` 打包进 exe）
- 排除重型依赖（torch/pandas/scipy 等）见 spec 的 excludes，防止体积膨胀到 GB 级
- 打包要求 Windows 64 位；Mac/Linux 用户需在对应系统重打
