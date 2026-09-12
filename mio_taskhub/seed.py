# mio_taskhub/seed.py
"""首次运行时播种常用任务模板，降低上手成本。

仅在模板表为空时插入，避免重复播种；每个模板同时写入一条 v1 版本记录，
与 create_template 的行为保持一致。
"""
import uuid
from datetime import datetime, timezone
from sqlmodel import Session, select

from mio_taskhub.models import TaskTemplate, TaskTemplateVersion, ScheduledJob, ScheduledJobActionType


def _utcnow():
    return datetime.now(timezone.utc)


# 常用模板定义（面向跨 agent 任务中心）。字段与 TaskTemplate 模型一致。
COMMON_TEMPLATES = [
    {
        "title": "Bug 修复",
        "description": "定位并修复已发现的缺陷，给出根因与复现说明。",
        "category": "工程",
        "priority": 1,
        "est_duration_min": 60,
        "est_cost_min": 120,
        "target_agent_type": None,
        "acceptance_criteria": "根因已定位并记录；修复代码已提交；新增或修复对应单测通过；缺陷不再复现。",
        "files_template": [],
        "deliverables_template": ["fix_summary.md"],
        "labels": ["bug", "fix"],
        "tags": ["bug"],
        "stages": ["design", "planning", "implementing", "review", "done"],
    },
    {
        "title": "功能开发",
        "description": "从需求到上线的完整功能实现。",
        "category": "工程",
        "priority": 0,
        "est_duration_min": 240,
        "est_cost_min": 480,
        "target_agent_type": None,
        "acceptance_criteria": "接口契约明确；核心逻辑有单测覆盖；联调通过；验收标准逐条满足。",
        "files_template": [],
        "deliverables_template": ["design.md", "changelog.md"],
        "labels": ["feature"],
        "tags": ["feature"],
        "stages": ["brainstorming", "design", "planning", "implementing", "review", "done"],
    },
    {
        "title": "代码评审",
        "description": "对指定改动进行评审，输出意见与结论。",
        "category": "质量",
        "priority": 0,
        "est_duration_min": 45,
        "est_cost_min": 90,
        "target_agent_type": "reviewer",
        "acceptance_criteria": "评审意见已记录；阻塞项已标注；结论（通过/打回）已归档。",
        "files_template": [],
        "deliverables_template": ["review_notes.md"],
        "labels": ["review"],
        "tags": ["review"],
        "stages": ["review", "done"],
    },
    {
        "title": "编写单元测试",
        "description": "为目标模块补齐或增强单元测试。",
        "category": "质量",
        "priority": 0,
        "est_duration_min": 120,
        "est_cost_min": 240,
        "target_agent_type": None,
        "acceptance_criteria": "关键路径覆盖；覆盖率达标（如 ≥80%）；CI 全绿。",
        "files_template": [],
        "deliverables_template": ["tests/"],
        "labels": ["test"],
        "tags": ["test"],
        "stages": ["planning", "implementing", "review", "done"],
    },
    {
        "title": "数据清洗脚本",
        "description": "编写脚本清洗脏数据并产出干净数据集。",
        "category": "数据",
        "priority": 0,
        "est_duration_min": 90,
        "est_cost_min": 180,
        "target_agent_type": "data",
        "acceptance_criteria": "输出干净数据集；字段校验通过；异常记录到日志；可重复运行。",
        "files_template": ["src/clean.py"],
        "deliverables_template": ["clean_dataset.csv", "clean_report.md"],
        "labels": ["data", "etl"],
        "tags": ["data"],
        "stages": ["design", "implementing", "review", "done"],
    },
    {
        "title": "技术文档编写",
        "description": "编写设计文档、接口文档或运维手册。",
        "category": "文档",
        "priority": 0,
        "est_duration_min": 90,
        "est_cost_min": 180,
        "target_agent_type": None,
        "acceptance_criteria": "文档结构清晰；术语一致；示例可运行；通过技术评审。",
        "files_template": [],
        "deliverables_template": ["doc.md"],
        "labels": ["doc"],
        "tags": ["doc"],
        "stages": ["brainstorming", "design", "review", "done"],
    },
    {
        "title": "性能优化",
        "description": "定位瓶颈并优化，给出前后基准对比。",
        "category": "工程",
        "priority": 0,
        "est_duration_min": 180,
        "est_cost_min": 360,
        "target_agent_type": None,
        "acceptance_criteria": "瓶颈已量化；优化后指标明确提升；无功能退化；基准报告已归档。",
        "files_template": [],
        "deliverables_template": ["benchmark.md"],
        "labels": ["perf"],
        "tags": ["perf"],
        "stages": ["design", "implementing", "review", "done"],
    },
    {
        "title": "接口联调",
        "description": "对前后端/微服务接口进行联调与冒烟验证。",
        "category": "工程",
        "priority": 0,
        "est_duration_min": 60,
        "est_cost_min": 120,
        "target_agent_type": None,
        "acceptance_criteria": "接口契约对齐；正常与异常路径验证通过；联调清单逐项勾选。",
        "files_template": [],
        "deliverables_template": ["integration_checklist.md"],
        "labels": ["integration"],
        "tags": ["integration"],
        "stages": ["planning", "implementing", "review", "done"],
    },
]


def _build_content(t: TaskTemplate) -> dict:
    return {
        "id": t.id, "title": t.title, "description": t.description,
        "author": t.author, "category": t.category,
        "priority": t.priority, "est_duration_min": t.est_duration_min,
        "est_cost_min": t.est_cost_min,
        "target_agent_type": t.target_agent_type,
        "acceptance_criteria": t.acceptance_criteria,
        "files_template": t.files_template,
        "deliverables_template": t.deliverables_template,
        "stages": t.stages, "dependencies": t.dependencies,
        "labels": t.labels, "tags": t.tags,
        "is_public": t.is_public, "version": t.version,
        "created_at": t.created_at.isoformat(),
        "updated_at": t.updated_at.isoformat(),
    }


def seed_common_templates(db: Session):
    """模板表为空时插入常用模板。幂等：已存在则跳过。"""
    existing = db.exec(select(TaskTemplate)).first()
    if existing:
        return
    now = _utcnow()
    for spec in COMMON_TEMPLATES:
        t = TaskTemplate(
            id=str(uuid.uuid4())[:8],
            title=spec["title"],
            description=spec.get("description", ""),
            author="system",
            category=spec.get("category", ""),
            priority=spec.get("priority", 0),
            est_duration_min=spec.get("est_duration_min", 30),
            est_cost_min=spec.get("est_cost_min", 60),
            target_agent_type=spec.get("target_agent_type"),
            acceptance_criteria=spec.get("acceptance_criteria", ""),
            files_template=spec.get("files_template", []),
            deliverables_template=spec.get("deliverables_template", []),
            stages=spec.get("stages", []),
            dependencies=spec.get("dependencies", []),
            labels=spec.get("labels", []),
            tags=spec.get("tags", []),
            is_public=True,
            created_at=now,
            updated_at=now,
            version=1,
        )
        db.add(t)
        ver = TaskTemplateVersion(
            id=str(uuid.uuid4())[:8],
            template_id=t.id,
            version=1,
            content=_build_content(t),
            created_by="system",
            description="seed common template",
        )
        db.add(ver)
    db.commit()


def seed_idea_generate_job(db: Session):
    """播种 idea 自动生成定时任务（幂等：已存在则更新 description）。"""
    from mio_taskhub.scheduling.cron_engine import compute_next_run, validate_cron

    NEW_DESCRIPTION = (
        "自动调用 agent-runtime 生成创意想法并同步到 taskhub。\n\n"
        "## 背景\n"
        "mio-agent-runtime 提供了 mio.idea.generate MCP 工具，能基于多策略生成创意。\n"
        "本任务定期调用该工具，将生成的创意同步到 taskhub 的想法库中。\n\n"
        "## 执行步骤\n\n"
        "### 1. 调用 idea.generate\n"
        "使用 bash 执行以下命令（MCP JSON-RPC over stdio）：\n"
        "```\n"
        "node D:\\node_global\\node_modules\\mio-agent-runtime\\server\\mio-intelligence-mcp\\index.js\n"
        "```\n"
        "发送 JSON：\n"
        "```json\n"
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"mio.idea.generate","arguments":{'
        '"goal":"如何提升 mio-taskhub 的自动化能力",'
        '"context":"定期自动生成创意，探索 taskhub 与 agent-runtime 的协同优化方向",'
        '"constraints":["不引入外部依赖","保持本机单用户","复用现有 MCP 工具","优先利用已有数据"],'
        '"numIdeas":3'
        "}}}\n"
        "```\n\n"
        "### 2. 解析返回结果\n"
        "响应中 result.content[0].text 是 JSON 字符串，解析后取 ideas 数组。\n"
        "每个 idea 包含：title, description, provenance.strategy。\n\n"
        "### 3. 同步到 taskhub\n"
        "对每个 idea，调用 taskhub_add_idea：\n"
        "- title: idea.title（截断到 200 字符）\n"
        "- description: idea.description\n"
        "- labels: [\"mio-intelligence\", \"auto-generated\", \"strategy:<provenance.strategy>\"]\n\n"
        "### 4. 去重\n"
        "先调用 taskhub_ideas 获取现有想法列表，跳过标题相同的想法。\n\n"
        "### 5. 提交结果\n"
        "调用 taskhub_submit_result 汇报：生成了几个想法、各自策略、同步了几个。\n"
    )

    existing = db.exec(select(ScheduledJob).where(ScheduledJob.name == "idea-generate")).first()
    if existing:
        # 更新 description（如果过时）
        cfg = existing.action_config or {}
        if cfg.get("description", "") != NEW_DESCRIPTION:
            cfg["description"] = NEW_DESCRIPTION
            existing.action_config = cfg
            existing.updated_at = _utcnow()
            db.add(existing)
            db.commit()
        return

    cron_expr = "0 */4 * * *"  # 每4小时
    if not validate_cron(cron_expr):
        return

    job = ScheduledJob(
        name="idea-generate",
        cron_expr=cron_expr,
        next_run_at=compute_next_run(cron_expr),
        action_type=ScheduledJobActionType.CREATE_TASK,
        action_config={
            "title": "[定时] 自动生成创意想法",
            "description": NEW_DESCRIPTION,
            "target_agent_type": "cli",
            "priority": 0,
            "stage": "ready",
            "labels": ["auto", "idea-generation"],
            "max_retries": 2,
        },
        enabled=True,
        max_retries=2,
        timeout_seconds=120,
    )
    db.add(job)
    db.commit()
