"""Idea templates for structured idea generation and quick capture."""
from dataclasses import dataclass
from typing import List, Optional, Dict, Any
import json


@dataclass
class IdeaTemplate:
    """Template for structured idea creation."""
    id: str
    name: str
    description: str
    category: str
    icon: str
    fields: List[Dict[str, Any]]
    prompt: str
    tags: List[str]


DEFAULT_TEMPLATES = [
    IdeaTemplate(
        id="feature-request",
        name="功能需求",
        description="新功能或功能改进建议",
        category="product",
        icon="✨",
        fields=[
            {"key": "title", "label": "功能标题", "type": "text", "required": True, "placeholder": "一句话描述核心价值"},
            {"key": "problem", "label": "解决什么问题", "type": "textarea", "required": True, "placeholder": "用户痛点、现状不足"},
            {"key": "solution", "label": "期望方案", "type": "textarea", "required": True, "placeholder": "理想状态、核心交互"},
            {"key": "acceptance", "label": "验收标准", "type": "textarea", "required": False, "placeholder": "如何判断完成"},
            {"key": "impact", "label": "影响范围", "type": "select", "options": ["单模块", "跨模块", "架构级", "用户可见"], "required": False},
        ],
        prompt="生成功能需求想法：目标是 {title}，解决 {problem}，方案是 {solution}",
        tags=["feature", "product"]
    ),
    IdeaTemplate(
        id="tech-debt",
        name="技术债/重构",
        description="代码质量、架构优化、技术债务偿还",
        category="engineering",
        icon="🔧",
        fields=[
            {"key": "title", "label": "重构标题", "type": "text", "required": True, "placeholder": "模块/组件名 + 问题类型"},
            {"key": "current_state", "label": "现状描述", "type": "textarea", "required": True, "placeholder": "代码位置、复杂度、痛点"},
            {"key": "target_state", "label": "目标状态", "type": "textarea", "required": True, "placeholder": "重构后的架构/代码结构"},
            {"key": "risk", "label": "风险评估", "type": "textarea", "required": False, "placeholder": "破坏性变更、测试覆盖、回滚方案"},
            {"key": "effort", "label": "预估工时", "type": "select", "options": ["<1天", "1-3天", "3-5天", "1-2周", "2周+"], "required": False},
        ],
        prompt="生成技术债想法：重构 {title}，现状 {current_state}，目标 {target_state}",
        tags=["tech-debt", "refactor", "engineering"]
    ),
    IdeaTemplate(
        id="bug-fix",
        name="缺陷修复",
        description="Bug 报告、异常处理、边界情况修复",
        category="quality",
        icon="🐛",
        fields=[
            {"key": "title", "label": "Bug 标题", "type": "text", "required": True, "placeholder": "[模块] 简述现象"},
            {"key": "repro", "label": "复现步骤", "type": "textarea", "required": True, "placeholder": "1. ... 2. ... 3. 观察到..."},
            {"key": "expected", "label": "期望行为", "type": "textarea", "required": True, "placeholder": "正确的行为是什么"},
            {"key": "actual", "label": "实际行为", "type": "textarea", "required": True, "placeholder": "错误信息、堆栈、截图"},
            {"key": "severity", "label": "严重程度", "type": "select", "options": ["P0-阻塞", "P1-严重", "P2-一般", "P3-低"], "required": True},
        ],
        prompt="生成Bug修复想法：修复 {title}，复现步骤 {repro}，期望 {expected}",
        tags=["bug", "fix", "quality"]
    ),
    IdeaTemplate(
        id="exploration",
        name="技术探索/Spike",
        description="新技术调研、可行性验证、POC",
        category="research",
        icon="🔬",
        fields=[
            {"key": "title", "label": "探索主题", "type": "text", "required": True, "placeholder": "技术名 + 探索目标"},
            {"key": "question", "label": "核心问题", "type": "textarea", "required": True, "placeholder": "要验证的假设、技术选型对比"},
            {"key": "criteria", "label": "成功标准", "type": "textarea", "required": True, "placeholder": "什么指标算通过"},
            {"key": "timebox", "label": "时间盒", "type": "select", "options": ["0.5天", "1天", "2天", "3天", "1周"], "required": False},
            {"key": "deliverable", "label": "交付物", "type": "textarea", "required": False, "placeholder": "文档/代码/演示/决策记录"},
        ],
        prompt="生成技术探索想法：探索 {title}，核心问题 {question}，成功标准 {criteria}",
        tags=["spike", "research", "exploration"]
    ),
    IdeaTemplate(
        id="process-improvement",
        name="流程/工具改进",
        description="开发流程、CI/CD、工具链、团队协作优化",
        category="process",
        icon="⚙️",
        fields=[
            {"key": "title", "label": "改进标题", "type": "text", "required": True, "placeholder": "流程/工具名 + 改进方向"},
            {"key": "pain_point", "label": "痛点", "type": "textarea", "required": True, "placeholder": "耗时、易错、手动、不可见"},
            {"key": "proposal", "label": "改进方案", "type": "textarea", "required": True, "placeholder": "自动化、标准化、可视化、并行化"},
            {"key": "roi", "label": "投入产出比", "type": "textarea", "required": False, "placeholder": "预估节省时间/减少错误/提升体验"},
        ],
        prompt="生成流程改进想法：优化 {title}，解决 {pain_point}，方案 {proposal}",
        tags=["process", "tooling", "ci-cd", "automation"]
    ),
    IdeaTemplate(
        id="ux-improvement",
        name="体验优化",
        description="UI/UX、交互、性能、可访问性优化",
        category="ux",
        icon="🎨",
        fields=[
            {"key": "title", "label": "优化标题", "type": "text", "required": True, "placeholder": "页面/组件 + 优化点"},
            {"key": "current_ux", "label": "当前体验问题", "type": "textarea", "required": True, "placeholder": "用户反馈、数据指标、观察到的困难"},
            {"key": "target_ux", "label": "目标体验", "type": "textarea", "required": True, "placeholder": "更流畅、更直观、更快、更无障碍"},
            {"key": "metrics", "label": "度量指标", "type": "textarea", "required": False, "placeholder": "任务完成率、时间、错误率、满意度"},
        ],
        prompt="生成体验优化想法：优化 {title}，当前问题 {current_ux}，目标 {target_ux}",
        tags=["ux", "ui", "accessibility", "performance"]
    ),
    IdeaTemplate(
        id="quick-capture",
        name="快速记录",
        description="灵感闪现、随手记、碎片化想法",
        category="quick",
        icon="💡",
        fields=[
            {"key": "title", "label": "想法标题", "type": "text", "required": True, "placeholder": "一句核心"},
            {"key": "note", "label": "补充说明", "type": "textarea", "required": False, "placeholder": "背景、灵感来源、相关链接..."},
        ],
        prompt="快速记录想法：{title}",
        tags=["quick", "inspiration"]
    ),
]


def get_template_by_id(template_id: str) -> Optional[IdeaTemplate]:
    """Get template by ID."""
    for t in DEFAULT_TEMPLATES:
        if t.id == template_id:
            return t
    return None


def get_templates_by_category(category: str) -> List[IdeaTemplate]:
    """Get templates filtered by category."""
    return [t for t in DEFAULT_TEMPLATES if t.category == category]


def get_all_templates() -> List[IdeaTemplate]:
    """Get all templates."""
    return DEFAULT_TEMPLATES


def render_template_prompt(template: IdeaTemplate, values: Dict[str, str]) -> str:
    """Render template prompt with provided values."""
    prompt = template.prompt
    for field in template.fields:
        key = field["key"]
        if key in values:
            prompt = prompt.replace(f"{{{key}}}", values[key])
    return prompt


def template_to_api_payload(template: IdeaTemplate, values: Dict[str, str]) -> Dict[str, Any]:
    """Convert template values to API payload for idea creation."""
    desc_parts = []
    for field in template.fields:
        key = field["key"]
        if key in values and values[key]:
            desc_parts.append(f"{field['label']}：\n{values[key]}")
    
    return {
        "title": values.get("title", template.name),
        "description": "\n\n".join(desc_parts) if desc_parts else "",
        "labels": template.tags,
        "project": "",
    }


if __name__ == "__main__":
    # Demo
    for t in DEFAULT_TEMPLATES:
        print(f"{t.icon} {t.name} ({t.category})")
        for f in t.fields:
            print(f"  - {f['label']} ({f['key']})")
        print()