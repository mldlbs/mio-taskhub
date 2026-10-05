# mio-taskhub SLO/SLI 定义

## 服务等级指标 (SLI)

### 可用性
| 指标 | 定义 | 计算方式 |
|------|------|----------|
| **服务可用性** | HTTP 请求成功率 | `(2xx + 3xx) / 总请求数` |
| **任务处理可用性** | 任务完成率 | `COMPLETED / (COMPLETED + FAILED)` |

### 延迟
| 指标 | 定义 | 计算方式 |
|------|------|----------|
| **HTTP P50 延迟** | 50% 请求的响应时间 | `histogram_quantile(0.5, http_duration)` |
| **HTTP P99 延迟** | 99% 请求的响应时间 | `histogram_quantile(0.99, http_duration)` |
| **任务完成 P50** | 50% 任务的完成时间 | `taskhub_task_completion_p50_seconds` |

### 错误率
| 指标 | 定义 | 计算方式 |
|------|------|----------|
| **HTTP 5xx 错误率** | 服务端错误比例 | `5xx 请求 / 总请求` |
| **任务失败率** | 任务失败比例 | `FAILED / (COMPLETED + FAILED)` |
| **依赖错误率** | 外部依赖失败比例 | `dep_errors / dep_total_calls` |

### 吞吐量
| 指标 | 定义 | 计算方式 |
|------|------|----------|
| **任务吞吐量** | 每日任务创建数 | `taskhub_task_throughput_24h` |
| **队列深度** | 排队任务数 | `taskhub_tasks_total{state="QUEUED"}` |

---

## 服务等级目标 (SLO)

### 可用性 SLO
| 服务 | 目标 | 窗口 | 错误预算 |
|------|------|------|----------|
| **HTTP API** | 99.9% | 30 天 | 43.2 分钟/月 |
| **任务处理** | 99.0% | 30 天 | 7.2 小时/月 |
| **MCP 工具** | 99.5% | 30 天 | 3.6 小时/月 |

### 延迟 SLO
| 服务 | P50 目标 | P99 目标 | 窗口 |
|------|----------|----------|------|
| **HTTP API** | < 100ms | < 2000ms | 30 天 |
| **任务创建** | < 500ms | < 3000ms | 30 天 |
| **仪表盘加载** | < 500ms | < 2000ms | 30 天 |

### 错误率 SLO
| 服务 | 目标 | 窗口 | 错误预算 |
|------|------|------|----------|
| **HTTP 5xx** | < 0.1% | 30 天 | 0.1% |
| **任务失败** | < 5% | 30 天 | 5% |
| **依赖错误** | < 1% | 30 天 | 1% |

---

## 告警规则 (基于 SLO)

### 可用性告警
```yaml
# 可用性低于 SLO 目标
- alert: AvailabilitySLOBreached
  expr: |
    sum(rate(taskhub_http_request_count_total{status!~"5.."}[5m]))
    /
    sum(rate(taskhub_http_request_count_total[5m])) < 0.999
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "HTTP availability SLO breached (< 99.9%)"

# 错误预算即将耗尽
- alert: ErrorBudgetLow
  expr: |
    (1 - (
      sum(rate(taskhub_http_request_count_total{status!~"5.."}[30d]))
      /
      sum(rate(taskhub_http_request_count_total[30d]))
    )) > 0.0008
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Error budget > 80% consumed"
```

### 延迟告警
```yaml
# P99 延迟超过 SLO
- alert: LatencyP99SLOBreached
  expr: |
    histogram_quantile(0.99, sum(rate(taskhub_http_request_duration_ms_bucket[5m])) by (le)) > 2000
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "P99 latency SLO breached (> 2s)"

# P50 延迟异常
- alert: LatencyP50Elevated
  expr: |
    histogram_quantile(0.5, sum(rate(taskhub_http_request_duration_ms_bucket[5m])) by (le)) > 500
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "P50 latency elevated (> 500ms)"
```

### 错误率告警
```yaml
# 任务失败率超过 SLO
- alert: TaskFailureSLOBreached
  expr: |
    sum(rate(taskhub_tasks_total{state="FAILED"}[15m]))
    /
    sum(rate(taskhub_tasks_total{state=~"COMPLETED|FAILED"}[15m])) > 0.05
  for: 15m
  labels:
    severity: warning
  annotations:
    summary: "Task failure rate SLO breached (> 5%)"

# 依赖错误率超过 SLO
- alert: DependencyErrorSLOBreached
  expr: |
    sum(taskhub_dep_latency_errors)
    /
    sum(taskhub_dep_latency_count) > 0.01
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Dependency error rate SLO breached (> 1%)"
```

---

## 错误预算管理

### 预算状态
| 服务 | 预算 | 已用 | 剩余 | 状态 |
|------|------|------|------|------|
| HTTP API | 43.2 分钟 | - | - | 待计算 |
| 任务处理 | 7.2 小时 | - | - | 待计算 |
| MCP 工具 | 3.6 小时 | - | - | 待计算 |

### 预算行动
| 剩余预算 | 行动 |
|----------|------|
| > 50% | 正常开发 |
| 20-50% | 增加代码审查 |
| 10-20% | 冻结非关键变更 |
| < 10% | 仅修复 SLO 相关问题 |
| 0% | 全面冻结，全力修复 |

---

## 监控仪表盘

### 关键面板
1. **SLO 状态面板**: 实时显示各 SLO 达标情况
2. **错误预算面板**: 显示预算消耗进度
3. **延迟趋势面板**: P50/P90/P99 延迟趋势
4. **依赖健康面板**: SQLite/Git/MCP 依赖状态
5. **任务流面板**: 任务创建→完成的全流程

### 链接
- 仪表盘: `http://127.0.0.1:48620/dashboard`
- 指标: `http://127.0.0.1:48620/metrics`
- 告警: `http://127.0.0.1:48620/api/v1/alerts`