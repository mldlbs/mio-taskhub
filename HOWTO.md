# opencode 接入 mio-taskhub 使用指南

## 启动方式

```bash
# 1. 启动任务中心服务
cd mio-taskhub && python -m uvicorn mio_taskhub.main:app --port 8080

# 2. 服务运行在 http://127.0.0.1:8080
#    - Web UI: http://127.0.0.1:8080/
#    - API: http://127.0.0.1:8080/api/v1
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

浏览器打开 http://localhost:8080 可以看禅道风格看板：
- 6 列状态：待处理 → 已领取 → 进行中 → 已完成 → 失败
- 拖拽任务卡片改变状态
- 实时 WebSocket 刷新
- 创建任务表单
