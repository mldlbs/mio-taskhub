# mio-taskhub MCP 接入指南

通过 MCP（Model Context Protocol）把 mio-taskhub 暴露成一组工具，任何支持 MCP 的 agent（opencode、claude code、codex、hermes、workbuddy 等）都能直接以"原生工具调用"的方式注册、领取任务、发心跳、交结果，无需在 prompt 里手写 curl / CLI 命令。

## 架构

```
+---------------+    MCP stdio     +------------------+    HTTP    +------------------+
|  Agent        | <--------------> | mio_taskhub_mcp  | -------->  | mio-taskhub hub  |
|  (opencode/   |  tools 调用      | (本地 MCP server) |   API/v1   | (FastAPI :48620)  |
|   claude/codex|                  |                  |  register/ |                  |
|   /hermes/...) |                 |                  |  claim/    |                  |
+---------------+                  +------------------+  heartbeat |                  |
                                                        /result    +------------------+
```

- **hub（:48620）** 必须先启动：`python -m uvicorn mio_taskhub.main:app --port 48620`
- **MCP server** 是本地 stdio 进程，由每个 agent 自己拉起；它把 hub 的 HTTP API 包装成 8 个工具
- 多个 agent 可同时连同一个 hub，各自注册不同名称、FIFO+优先级领取任务

## 暴露的工具

| 工具名 | 作用 |
|---|---|
| `taskhub_register` | 注册 agent（幂等，重复注册刷新在线状态） |
| `taskhub_claim` | 领取一个排队任务，返回 run_id + 任务详情 |
| `taskhub_heartbeat` | 心跳上报进度（0-100），避免超时 |
| `taskhub_submit_result` | 提交结果（成功/失败），驱动 completed/retrying/failed |
| `taskhub_list_tasks` | 看板：按状态 / agent 类型过滤列任务 |
| `taskhub_get_task` | 查看单个任务完整详情 |
| `taskhub_create_task` | 提交新任务供各 agent 领取 |
| `taskhub_cancel_task` | 取消排队中的任务 |

## 1. 启动 hub

```bash
cd mio-taskhub
python -m uvicorn mio_taskhub.main:app --port 48620
# Web UI: http://127.0.0.1:48620/   API: http://127.0.0.1:48620/api/v1
```

## 2. 各 agent 注册 MCP server

MCP server 启动命令（任选其一）：
```bash
# 已 pip install -e . 时：
mio-taskhub-mcp
# 或直接：
python -m mio_taskhub.mcp_server
```

可选环境变量 `MIO_TASKHUB_URL`，默认 `http://127.0.0.1:48620/api/v1`。

### opencode

编辑 `~/.config/opencode/opencode.jsonc`（或项目 `.opencode/` 配置）：

```jsonc
{
  "mcp": {
    "mio-taskhub": {
      "type": "local",
      "command": ["python", "-m", "mio_taskhub.mcp_server"],
      "enabled": true
    }
  }
}
```

### claude code

```bash
claude mcp add mio-taskhub -- python -m mio_taskhub.mcp_server
# 查看：claude mcp list
# 移除：claude mcp remove mio-taskhub
```

### codex

编辑 `~/.codex/config.toml`：

```toml
[mcp_servers.mio-taskhub]
command = "python"
args = ["-m", "mio_taskhub.mcp_server"]
```

### hermes / workbuddy 等支持 MCP 的 agent

按其官方配置方式注册一个 local stdio MCP server，command 指向：
```
python -m mio_taskhub.mcp_server
```

## 3. 在 agent 的提示词 / 系统指令中启用

建议把下面这段写进各 agent 的全局配置文件，让它自主执行任务：

| Agent | 配置文件 |
|---|---|
| opencode | 项目 `AGENTS.md` 或 `~/.config/opencode/AGENTS.md` |
| claude code | 项目 `CLAUDE.md` 或 `~/.claude/CLAUDE.md` |
| codex | `~/.codex/AGENTS.md` |

```markdown
## mio-taskhub 任务执行规范
使用 mio-taskhub MCP 工具领取并完成任务：
1. taskhub_register：注册为 <agent 名称>（先检查是否已注册）
2. taskhub_claim：领取任务，得到 run_id 和任务详情
3. 执行任务内容；如耗时较长，周期性调用 taskhub_heartbeat 上报进度
4. 完成调用 taskhub_submit_result(run_id, success=true, result="产出描述")
   失败则提交 success=false 并说明原因
```

## 4. 手动验证（不启动 hub 时）

```bash
# 启动 hub
python -m uvicorn mio_taskhub.main:app --port 48620

# 用 MCP Inspector 交互测试
npx @modelcontextprotocol/inspector python -m mio_taskhub.mcp_server

# 或直接调用（走 stdio JSON-RPC）：
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | python -m mio_taskhub.mcp_server
```
