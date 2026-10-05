# -*- coding: utf-8 -*-
"""软件项目开发文档链（七件套）。

用户视角的一条完整开发文档链：需求规格 → 架构设计 → 模块 Spec → 状态模型
→ 接口契约 → 测试验收 → 部署运维。每份文档带内容骨架（章节 + 填写指引）
与上下游追溯链接；POST /tasks/{id}/docs/scaffold 按链一次性生成，已有文件
不覆盖。

kind 映射（复用 21+1 类文档体系，不新增 kind）：
    需求规格  -> requirement     （brainstorming 门槛）
    架构设计  -> architecture    （design 阶段产出）
    模块 Spec -> spec            （design 门槛）
    状态模型  -> data-model      （planning 阶段产出，含状态机）
    接口契约  -> api             （planning 阶段产出）
    测试验收  -> test            （implementing 阶段产出）
    部署运维  -> runbook         （review/done 阶段产出）

书写约定（用户要求，2026-09-17）：文档正文**尽可能分点和换行**——
每点独立一行（`-` 列表），避免大段连续文字；表格优先于文字罗列。
"""

# 默认存放路径（workspace 相对）；若任务已登记该 kind 的路径则沿用登记值
import re

DEFAULT_PATH = {
    'requirement':  'docs/requirement.md',
    'architecture': 'docs/architecture.md',
    'spec':         'docs/spec.md',
    'data-model':   'docs/data-model.md',
    'api':          'docs/api.md',
    'test':         'docs/test.md',
    'runbook':      'docs/runbook.md',
}

# 任务级默认目录：每任务一目录，避免通名文件（docs/spec.md）与其它任务/主题混放
# （2026-09-23 实测：多任务共用 docs/ 导致清单混乱、同名多份无法判断权威）
TASK_DOC_DIR = 'docs/taskhub'


def task_doc_path(task_id: str, kind: str) -> str:
    """任务级默认文档路径：docs/taskhub/<task_id>/<kind>.md"""
    return f'{TASK_DOC_DIR}/{task_id}/{kind}.md'

# 链路顺序（上下游即按此相邻追溯）
DOC_CHAIN = ('requirement', 'architecture', 'spec', 'data-model', 'api', 'test', 'runbook')

CHAIN_TITLE = '软件项目文档链'


def _header(kind, up=None, down=None):
    up_s = f"上游：[{up[1]}]({up[0]})" if up else '上游：—'
    down_s = f"下游：[{down[1]}]({down[0]})" if down else '下游：—'
    return (f"> {CHAIN_TITLE} · {len(DOC_CHAIN)} 件套  \n"
            f"> {up_s} | {down_s}  \n"
            f"> 约定：正文**分点 + 换行**（每点一行，`-` 列表）；"
            f"指引注释处填内容，未用章节删除。\n")


def _tpl_requirement(up, down):
    return _header('requirement', up, down) + """
# 需求规格（Requirement Specification）

## 1. 背景与问题
<!--
- 现状是什么
- 痛点在哪（谁受影响）
- 为什么现在做
-->

## 2. 目标与非目标
- 目标：
  - （可衡量，有指标或明确状态）
- 非目标（本次明确不做）：
  - 

## 3. 用户故事 / 使用场景
<!--
- 作为 <角色>，我想 <做什么>，以便 <得到什么>
- 一个场景一条，高频场景排前面
-->

## 4. 功能需求
| 编号 | 需求 | 优先级 | 验收标准 |
|------|------|--------|---------|
| FR-1 |  | P0 |  |
| FR-2 |  | P1 |  |

<!--
- 一条需求一行，编号供测试追溯
- 验收标准必须可验收（能判 Pass/Fail）
- 禁用「支持xx等」模糊表述
-->

## 5. 非功能需求
- 性能：
  - （QPS / 延迟 / 容量，给具体数值）
- 安全：
  - （鉴权、数据边界）
- 兼容 / 依赖：
  - 

## 6. 边界与约束
<!--
- 时间 / 资源
- 合规要求
- 既有系统约束
-->
"""


def _tpl_architecture(up, down):
    return _header('architecture', up, down) + """
# 架构设计（Architecture Design）

## 1. 系统上下文
<!--
- 谁调用本系统、本系统调用谁
- 数据从哪来、到哪去
- 复杂时给 mermaid C4 图
-->

## 2. 总体架构
- 模块划分：
  - 模块A：一句话职责
  - 模块B：一句话职责
- 每个模块的详细设计 → 链接对应模块 Spec

## 3. 关键技术决策（ADR 摘要）
| 决策 | 备选 | 结论与理由 |
|------|------|-----------|
|  |  |  |

<!--
- 一个决策一行
- 理由要能被挑战（写清权衡）
-->

## 4. 关键数据流
<!--
- 核心链路从请求走到返回
- 标注存储点、转换点、外部调用点
-->

## 5. 技术选型
- 语言 / 框架：
- 存储：
- 第三方依赖：

## 6. 风险与对策
- 风险：
  - （高风险项单独建 risk 文档链接过来）
- 对策：
  - 
"""


def _tpl_spec(up, down):
    return _header('spec', up, down) + """
# 模块 Spec（Module Specification）

## 1. 模块职责与边界
- 做什么：
  - 
- 不做什么：
  - 

## 2. 输入 / 输出
- 输入：
  - （类型 / 格式 / 来源）
- 输出：
  - （类型 / 格式 / 去向）

## 3. 依赖
- 上游模块：
- 外部库：

## 4. 详细设计
<!--
- 类 / 函数 / 算法，一个主题一小节
- 复杂分支给 mermaid 流程图
- 预期新增 / 修改的文件列清单
-->

## 5. 异常与边界情况
| 场景 | 处理方式 |
|------|---------|
|  |  |

<!--
- 枚举失败场景：抛错 / 降级 / 重试，逐行写清
-->
"""


def _tpl_data_model(up, down):
    return _header('data-model', up, down) + """
# 状态模型 / 数据模型（Data & State Model）

## 1. 实体与字段
| 实体.字段 | 类型 | 约束 | 说明 |
|-----------|------|------|------|
|  |  |  |  |

<!--
- 一字段一行
- 约束写全：唯一键 / 非空 / 枚举值
-->

## 2. 状态机
| 当前状态 | 事件 | 下一状态 | 副作用 |
|---------|------|---------|--------|
|  |  |  |  |

- 非法转移处理：
  - 抛错 / 忽略 / 落日志（选一个写明）

## 3. 完整性约束
- 唯一键：
- 外键：
- 业务不变量（invariant）：
  - 

## 4. 迁移策略
- 存量数据迁移：
- 回滚方式：
"""


def _tpl_api(up, down):
    return _header('api', up, down) + """
# 接口契约（API Contract）

> **本文件要求事无巨细。** 每个接口的每个参数、每个响应字段、每个错误码都必须有
> 类型、必填性、约束、示例；宁可长，不可省。任何「详见代码」「同上」「略」都是缺陷。
> 读到本文件的人应当**无需翻源码**就能正确调用并处理全部异常。

## 1. 文档信息与范围
| 项 | 值 |
|----|----|
| 契约版本 | v1 |
| 适用范围 |  |
| 明确不含 |  |

<!--
- 元信息（文档版本/最后更新/状态/负责人）见文档顶部「文档信息」表，此处只写范围
- 契约版本变更时本表必须同步修改（配合 §20 变更记录）
- 「适用范围」写清哪些调用方/模块受此契约约束
- 「明确不含」写清边界（如不含内部 RPC、不含数据库直连），防止被误当全量接口清单
-->

## 2. 环境与基础地址
| 环境 | Base URL | 说明 |
|------|----------|------|
| dev |  |  |
| staging |  |  |
| prod |  |  |

- 路径前缀：
- 协议与端口：
- 网关 / 反向代理差异：
- 字符编码：UTF-8
- 内容类型：`application/json; charset=utf-8`

<!--
- 每个环境一行，禁止写「同上」
- 若经网关导致路径变化（前缀被剥掉/重写），必须写明
-->

## 3. 版本策略
- 版本位置：（URL 路径 / 请求头 / 查询参数——写明选了哪一种）
- 当前版本：
- 版本号规则：
- 多版本并存策略：
- 旧版本弃用流程：

## 4. 认证与鉴权
| 项 | 约定 |
|----|------|
| 认证方式 | Bearer / API-Key / 签名 / 会话 |
| 凭证来源 |  |
| 传递位置 | `Authorization: Bearer <token>` |
| 有效期 |  |
| 刷新方式 |  |
| 权限模型 | 角色 / 权限点 / 资源级 |
| 未认证响应 | HTTP 401 + 业务码 |
| 越权响应 | HTTP 403 + 业务码 |

<!--
- 认证失败与越权必须区分：401（不知道你是谁）≠ 403（知道你是谁但不许）
- 每个接口需要哪个权限点 → 见 §17 接口清单「权限」列，两处必须一致
-->

## 5. 通用请求头
| Header | 必填 | 类型 | 默认 | 说明 |
|--------|------|------|------|------|
| `Authorization` | 是 | string | — | Bearer token |
| `Content-Type` | 写请求必填 | string | `application/json` |  |
| `X-Request-Id` | 否 | string | 服务端生成 | 链路追踪，原样透传回响应 |

## 6. 通用响应结构
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `code` | integer | 是 | 业务码，0 表示成功 |
| `message` | string | 是 | 人类可读信息，仅供展示，调用方不得用于分支判断 |
| `data` | object \\| null | 否 | 业务数据；失败时为 null |
| `request_id` | string | 是 | 与请求头一致，排障时提供 |

<!--
- 成功与失败共用同一外壳，不允许出现第二套结构
- `data` 为 null 与 `data` 为 {} 的语义差异必须写明
- 若个别接口返回裸数组/二进制，必须在此显式列出例外
-->

## 7. 统一错误码
| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 400 |  |  |  |  |
| 401 |  |  |  |  |
| 403 |  |  |  |  |
| 404 |  |  |  |  |
| 409 |  |  |  |  |
| 422 |  |  |  |  |
| 429 |  |  |  |  |
| 500 |  |  |  |  |

<!--
- 每行写全：HTTP 状态 + 业务码 + 含义 + 什么情况触发 + 调用方该怎么办
- 禁止只写「参数错误」——要写清是哪个参数、违反了什么约束
- 接口级特有错误码登记在 §18 各接口的「错误码」子块，并在本表汇总
-->

## 8. 分页 / 排序 / 过滤
| 项 | 参数 | 类型 | 默认 | 上限 | 说明 |
|----|------|------|------|------|------|
| 页码 |  |  |  |  |  |
| 每页条数 |  |  |  |  |  |
| 排序字段 |  |  |  |  |  |
| 排序方向 |  |  |  |  |  |
| 过滤条件 |  |  |  |  |  |

- 分页响应字段：total / page / size / has_next 各自含义
- 游标 vs 偏移的选择理由与限制：
- 超限行为：（截断 还是 报错，报哪个业务码）
- 多字段排序的优先级规则：

## 9. 字段命名与类型规范
| 项 | 规范 |
|----|------|
| 命名风格 | snake_case / camelCase，禁混用 |
| ID 类型 | |
| 布尔 | |
| 金额 | 单位与精度 |
| 数组 | 空数组与 null 的语义 |
| 可选字段 | 字段缺失 与 显式 null 的区别 |

<!--
- 命名风格要给出反例（什么写法算违规）
- 「字段缺失 vs 显式 null」是调用方最容易踩的坑，必须写明
-->

## 10. 时间 / 数值 / 空值约定
- 时间格式：
- 时区：
- 时间精度：
- 数值范围与溢出处理：
- 空值语义：

## 11. 枚举全集
| 枚举 | 取值 | 含义 | 出现在哪些接口 |
|------|------|------|---------------|
|  |  |  |  |

<!--
- 全项目枚举集中登记在此，接口明细只引用不再重复定义
- 新增枚举值 = 兼容变更；删除取值或改变含义 = 破坏性变更（见 §19）
- 调用方必须能处理未知取值（前向兼容要求），要写明
-->

## 12. 幂等性与重试
| 接口 / 场景 | 幂等 | 幂等键 | 重试建议 |
|------------|------|--------|---------|
|  |  |  |  |

<!--
- 安全重试的状态码： （如 429 / 503）
- 绝不可重试的状态码： （如 409 冲突、非幂等写）
- 重试间隔与退避策略：
- 幂等键的生成规则、有效期、重复提交的响应
-->

## 13. 限流与配额
| 范围 | 阈值 | 窗口 | 超限响应 |
|------|------|------|---------|
|  |  |  | HTTP 429 + `Retry-After` |

## 14. 超时与并发
- 服务端处理超时：
- 建议客户端超时：
- 乐观锁 / 版本号字段：
- 并发冲突响应：
- 长任务的处理方式：（同步阻塞 / 异步返回任务 ID + 轮询）

## 15. 文件上传与下载
| 项 | 约定 |
|----|------|
| 上传方式 | multipart / 分片 / 预签名 URL |
| 单文件上限 | |
| 允许类型 | |
| 下载方式 | |
| 鉴权方式 | |

<!--
- 完全不涉及文件的接口，整节删除（不是留空）
-->

## 16. 安全与脱敏
- 敏感字段清单及脱敏规则：
- 传输要求：
- 日志禁止记录的字段：
- 越权与注入防护：

## 17. 接口清单
| Method | Path | 用途 | 权限 | 幂等 | 限流 | 关联状态 |
|--------|------|------|------|------|------|---------|
|  |  |  |  |  |  |  |

<!--
- 一行一个接口，覆盖全部对外接口（含内部管理接口）
- 「关联状态」对应 data-model 的状态机迁移，便于交叉核对
- 清单必须与 §18 明细一一对应：不允许只在清单里出现而无明细
-->

## 18. 接口明细

> 每个接口一个小节：`### <Method> <Path>`。
> 小节内**必须**包含四个子块（`#### `）：**请求参数 / 响应字段 / 错误码 / 示例**。
> 前三个子块都必须有表格且**至少 1 行数据**——缺任一项质量门会直接拦下。

<!--
（下面是一份完整范例。复制整块作为每个接口的骨架，再把内容换成真实的。范例本身不要保留。）

### GET /api/v1/tasks/{id}

- 用途：按 ID 查询任务详情
- 权限：`task:read`（或「任务归属者」）
- 幂等：是（GET 天然幂等）
- 副作用：无
- 关联状态：任意状态均可查询

#### 请求参数

| 参数 | 位置 | 类型 | 必填 | 默认 | 约束 | 说明 | 示例 |
|------|------|------|------|------|------|------|------|
| id | path | string | 是 | — | 8 位 hex | 任务 ID | a1b2c3d4 |
| verbose | query | boolean | 否 | false | — | 是否返回扩展字段 | true |

#### 响应字段

| 字段 | 类型 | 必填 | 说明 | 示例 |
|------|------|------|------|------|
| `data.id` | string | 是 | 任务 ID | a1b2c3d4 |
| `data.state` | string | 是 | 执行状态，取值见 §11 | queued |

#### 错误码

| HTTP | 业务码 | 含义 | 触发条件 | 处理建议 |
|------|--------|------|---------|---------|
| 404 | TASK_NOT_FOUND | 任务不存在 | id 查无记录 | 核对 id 是否正确 |
| 403 | FORBIDDEN | 无该任务权限 | 非归属者访问 | 检查权限点配置 |

#### 示例

请求：

```bash
curl -s "http://127.0.0.1:8000/api/v1/tasks/a1b2c3d4" \\
  -H "Authorization: Bearer <token>"
```

响应（200）：

```json
{"code": 0, "message": "ok", "data": {"id": "a1b2c3d4"}, "request_id": "req-1"}
```

（范例结束。以下开始写真实接口。）

-->

## 19. 兼容性与废弃策略
- 什么算破坏性变更：（字段删除/改名、类型收窄、枚举删值、必填变严、状态码改义）
- 什么算兼容变更：（新增可选字段、新增枚举值、新增接口）
- 变更通知方式与提前期：
- 废弃流程：标记 → 双写/双读 → 下线，各阶段时长
- 契约校验方式：（快照测试 / schema 校验 / 每日 diff）

## 20. 变更记录
| 日期 | 契约版本 | 变更内容 | 类型 | 影响方 |
|------|---------|---------|------|--------|
|  |  |  | 兼容 / 破坏 |  |

<!--
- 每次改契约都要在此追加一行，并与 §1 文档版本联动
- 破坏性变更必须标出影响方并链接通知记录
-->

## 21. 附录
- HTTP 状态码全集：
- 业务错误码全集：（与 §7 一致）
- 枚举全集：（与 §11 一致）
- 数据字典 / 术语表：
- 相关文档：状态模型 `docs/data-model.md`、测试验收 `docs/test.md`
"""


def _tpl_test(up, down):
    return _header('test', up, down) + """
# 测试与验收（Test & Acceptance）

## 1. 验收标准
| FR 编号 | 验收标准 | 验证方式 |
|---------|---------|---------|
| FR-1 |  |  |

<!--
- 逐条对应需求规格的 FR 编号
- 全部 FR 被覆盖 = 验收通过的必要条件
-->

## 2. 测试范围
- 单元测试：
  - （测什么）
- 集成测试：
  - 
- E2E / 冒烟：
  - 
- 明确不测的：
  - 

## 3. 用例清单
| 用例编号 | 覆盖需求 | 前置 | 步骤 | 预期 |
|---------|---------|------|------|------|
| TC-1 | FR-1 |  |  |  |

<!--
- TC-n 必须能与 FR-n 对上（追溯矩阵）
- 步骤逐行编号，不写成一段
-->

## 4. 验收流程
- 谁验收：
- 何时验收：
- 通过条件：
- 不通过的回退路径：
"""


def _tpl_runbook(up, down):
    return _header('runbook', up, down) + """
# 部署运维（Runbook）

## 1. 环境与依赖
- 运行环境：
- 版本要求：
- 外部服务依赖：
  - （缺一项部署就会卡，逐行列全）

## 2. 部署步骤
1. 
2. 
3. 

<!--
- 每步一条可复制执行的命令
- 从构建到上线按顺序编号
-->

## 3. 配置项
| 配置 | 默认值 | 说明 |
|------|--------|------|
|  |  |  |

## 4. 回滚
- 触发条件：
- 回滚步骤：
  1. 
  2. 

## 5. 监控与告警
- 关键指标：
- 健康检查端点：
- 告警阈值：

## 6. 常见故障排查
- 现象 → 处理（一行一条）
- 详表 → troubleshooting 文档
"""


# kind -> 模板构建器
_TEMPLATE_BUILDERS = {
    'requirement':  _tpl_requirement,
    'architecture': _tpl_architecture,
    'spec':         _tpl_spec,
    'data-model':   _tpl_data_model,
    'api':          _tpl_api,
    'test':         _tpl_test,
    'runbook':      _tpl_runbook,
}

_KIND_LABEL = {
    'requirement': '需求规格', 'architecture': '架构设计', 'spec': '模块 Spec',
    'data-model': '状态模型', 'api': '接口契约', 'test': '测试验收', 'runbook': '部署运维',
}


def chain_path_of(kind: str, registered: dict, task_id: str = None) -> str:
    """链内文档路径：已登记用登记值，否则任务级默认 docs/taskhub/<id>/<kind>.md，
    无 task_id 时退化为 docs/<kind>.md。"""
    stored = (registered or {}).get(kind)
    if stored:
        return stored
    if task_id:
        return task_doc_path(task_id, kind)
    return DEFAULT_PATH[kind]


INFO_TITLE = '文档信息'
INFO_DATETIME_PLACEHOLDER = 'YYYY-MM-DD HH:MM'
INFO_DATE_PLACEHOLDER = INFO_DATETIME_PLACEHOLDER   # 兼容旧引用


def _now_stamp() -> str:
    """文档「最后更新」时间戳：本地时间，精确到分钟。"""
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M')


def info_block() -> str:
    """统一的「文档信息」元信息表（文档版本 / 最后更新 / 状态 / 负责人 / 适用范围）。"""
    return (
        "\n## " + INFO_TITLE + "\n\n"
        "| 项 | 值 |\n"
        "|---|---|\n"
        "| 文档版本 | v0.1 |\n"
        "| 最后更新 | " + INFO_DATETIME_PLACEHOLDER + " |\n"
        "| 状态 | draft |\n"
        "| 负责人 | TBD |\n"
        "| 适用范围 | <!-- 本次覆盖的模块 / 接口；明确不含什么 --> |\n"
    )


def _inject_info_block(body: str) -> str:
    """把「文档信息」块插到首个 H1 之后（已存在则不重复插入）。"""
    if f'## {INFO_TITLE}' in body:
        return body
    lines = body.splitlines(keepends=True)
    for i, ln in enumerate(lines):
        if ln.startswith('# '):
            return ''.join(lines[:i + 1]) + info_block() + ''.join(lines[i + 1:])
    return info_block() + body


def _set_info_row(section: str, label: str, value: str) -> str:
    """把 section 内「| label | ... |」的第一个值替换为 value（仅首处，规范化空格）。"""
    pat = re.compile(r'\|\s*' + re.escape(label) + r'\s*\|\s*[^|\n]*?\s*\|')
    return pat.sub(lambda m: '| %s | %s |' % (label, value), section, count=1)


def update_info_section(text: str, state: str = None, bump_version: bool = False,
                        now: str = None) -> str:
    """就地更新「文档信息」表的 最后更新（日期+时间）/ 状态（可选递增 文档版本）。

    无该节 → 原样返回。供 set_doc_status 推进状态时自动维护元信息，避免人肉漏填。
    `now` 可传固定时间戳（测试用）；默认取本地当前时间 `YYYY-MM-DD HH:MM`。
    """
    marker = '## ' + INFO_TITLE
    i = text.find(marker)
    if i < 0:
        return text
    j = text.find('\n## ', i + len(marker))
    seg = text[i:j] if j != -1 else text[i:]
    stamp = now or _now_stamp()
    seg = _set_info_row(seg, '最后更新', stamp)
    if state:
        seg = _set_info_row(seg, '状态', state)
    if bump_version:
        m = re.search(r'\|\s*文档版本\s*\|\s*v(\d+)\.(\d+)\s*\|', seg)
        if m:
            seg = _set_info_row(seg, '文档版本',
                                'v%s.%d' % (m.group(1), int(m.group(2)) + 1))
    return text[:i] + seg + (text[j:] if j != -1 else '')


def render_chain(kind: str, registered: dict = None, task_id: str = None) -> str:
    """渲染链内某文档的模板正文（含按链路计算的上下游链接）。"""
    if kind not in _TEMPLATE_BUILDERS:
        raise KeyError(f'kind {kind!r} 不在文档链内: {DOC_CHAIN}')
    reg = registered or {}
    paths = {k: chain_path_of(k, reg, task_id) for k in DOC_CHAIN}
    idx = DOC_CHAIN.index(kind)
    up = (paths[DOC_CHAIN[idx - 1]], f'{_KIND_LABEL[DOC_CHAIN[idx - 1]]}') if idx > 0 else None
    down = (paths[DOC_CHAIN[idx + 1]], f'{_KIND_LABEL[DOC_CHAIN[idx + 1]]}') if idx < len(DOC_CHAIN) - 1 else None
    return _inject_info_block(_TEMPLATE_BUILDERS[kind](up, down))
