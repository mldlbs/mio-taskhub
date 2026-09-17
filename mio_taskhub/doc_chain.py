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
DEFAULT_PATH = {
    'requirement':  'docs/requirement.md',
    'architecture': 'docs/architecture.md',
    'spec':         'docs/spec.md',
    'data-model':   'docs/data-model.md',
    'api':          'docs/api.md',
    'test':         'docs/test.md',
    'runbook':      'docs/runbook.md',
}

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

## 1. 全局约定
- 鉴权：
- 错误码结构：
- 分页 / 过滤：
- 版本策略：

## 2. 接口清单
| Method | Path | 用途 | 权限 |
|--------|------|------|------|
|  |  |  |  |

## 3. 接口明细
<!--
- 每个接口一节（### Method Path）
- 请求 / 响应字段表：字段 / 类型 / 必填 / 说明
- 附请求与响应示例
- 列出该接口的错误码
-->

## 4. 兼容性
- 破坏性变更：
  - （如何通知、过渡期多长）
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


def chain_path_of(kind: str, registered: dict) -> str:
    """链内文档路径：已登记用登记值，否则默认 docs/<kind>.md。"""
    return (registered or {}).get(kind) or DEFAULT_PATH[kind]


def render_chain(kind: str, registered: dict = None) -> str:
    """渲染链内某文档的模板正文（含按链路计算的上下游链接）。"""
    if kind not in _TEMPLATE_BUILDERS:
        raise KeyError(f'kind {kind!r} 不在文档链内: {DOC_CHAIN}')
    reg = registered or {}
    paths = {k: chain_path_of(k, reg) for k in DOC_CHAIN}
    idx = DOC_CHAIN.index(kind)
    up = (paths[DOC_CHAIN[idx - 1]], f'{_KIND_LABEL[DOC_CHAIN[idx - 1]]}') if idx > 0 else None
    down = (paths[DOC_CHAIN[idx + 1]], f'{_KIND_LABEL[DOC_CHAIN[idx + 1]]}') if idx < len(DOC_CHAIN) - 1 else None
    return _TEMPLATE_BUILDERS[kind](up, down)
