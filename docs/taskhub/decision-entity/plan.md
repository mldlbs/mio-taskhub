> 软件项目文档链 · 7 件套  
> 上游：[模块 Spec](docs/taskhub/decision-entity/spec.md) | 下游：[测试验收](docs/taskhub/decision-entity/test.md)  
> 任务：89a90af6（Decision 实体）

# 实施计划（Implementation Plan）

## 文档信息

| 项 | 值 |
|---|---|
| 文档版本 | v1.1 |
| 最后更新 | 2026-11-04 13:39 |
| 状态 | approved |
| 负责人 | agent:workbuddy |
| 适用范围 | Decision 实体全部交付物（模型/REST/inbox/MCP/前端/测试/exe） |

## 1. 分批与顺序

- A 批（后端核心，一次提交内完成）：
  1. A-1 模型：`models.py` 尾部追加 `Decision`（15 字段 + 4 索引）
  2. A-2 REST：新建 `api/decisions.py`（create/list/get/resolve/supersede）
  3. A-3 注册：`main.py` 在 task_diff 之后注册 decisions.router
  4. A-4 inbox：`api/inbox.py` decide 队列并入 open 决策 + summary 分解键
- B 批（agent 面）：
  1. B-1 MCP 工具 `taskhub_add_decision` / `taskhub_resolve_decision`
  2. B-2 instructions 增补「登记不裁决」规范
- C 批（前端）：
  1. C-1 `api.js` 三方法
  2. C-2 `DecisionInbox.jsx` 决策行 + 裁决对话框
  3. C-3 `ledger.css` inbox-dlg 样式
- D 批（验证与交付）：
  1. D-1 `tests/test_decisions.py`（≥12 用例）
  2. D-2 全量回归
  3. D-3 vite build + 源码 hub 实弹六步链路
  4. D-4 PyInstaller 重建 exe + PYZ 校验 + 部署

## 2. 文件清单

| 动作 | 文件 |
|------|------|
| 新增 | mio_taskhub/api/decisions.py、tests/test_decisions.py、web/src/components/DecisionInbox.jsx（重写）、web/src/noise.js（已有） |
| 修改 | mio_taskhub/models.py（尾部追加 Decision）、mio_taskhub/main.py（注册路由）、mio_taskhub/api/inbox.py（decide 扩展）、mio_taskhub/mcp_server.py（两工具+instructions）、web/src/api.js（三方法）、web/src/ledger.css（弹窗样式） |

## 3. 风险与对策

- 路由抢占：/decisions 前缀与 /tasks/{task_id} 无交叉，注册位置任意；沿用 inbox 的顺序注释防回归
- 测试污染：decisions 表由 conftest drop_all/create_all 自动清理，无手动清理需求
- 旧消费方断言「所有条目须有 title」：决策条目双写 title=question 兜底
- f-string 转义语法坑：MCP 工具错误文案不用 f-string 内嵌引号（实测 SyntaxError，已改 % 格式）

## 4. 验收对照

- 见 test.md 用例清单（FR-01~FR-05 全覆盖）与 D-3/D-4 实弹/交付步骤
