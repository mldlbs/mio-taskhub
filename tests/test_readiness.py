"""GET /tasks/{id}/readiness：执行就绪度的契约测试。

四件事必须可断言：
  1. 分层正确（flow / input / risk）
  2. 排序正确（blocking 先，其次流程层，再次低成本项）
  3. plan 只给 blocking 的前三条，且每条带原因
  4. 输入检查的作用域——构思/设计阶段不查输入（否则全库 1500 条噪音）
"""
from fastapi.testclient import TestClient
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Task, TaskStage, TaskState

client = TestClient(app)


def _mk(title="readiness-task", stage="implementing", state=TaskState.QUEUED, **kw):
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage), state=state, **kw)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _kinds(d):
    return [i["kind"] for i in d["items"]]


def test_404():
    assert client.get("/api/v1/tasks/nope/readiness").status_code == 404


def test_every_item_has_nature_and_evidence():
    """面板顶部有「性质」列与「依据」行，数据契约必须逐条给齐。

    缺 nature → 总览表的分类列空白；缺 evidence → 读者无法核对判据从哪来。
    """
    from mio_taskhub.models import Subtask, SubtaskStatus
    tid = _mk(title="nature-and-evidence", stage="design", gate_tier="high",
              doc_paths={"plan": "plan.md"}, attempt=2)
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.deviations = [{"id": "D-1", "severity": "high", "status": "open",
                         "title": "D-1", "description": "原型与规格不一致。"}]
        t.gate_tier = "high"
        s.add(t)
        s.add(Subtask(task_id=tid, status=SubtaskStatus.BLOCKED,
                      title="B1 阻塞：数据路线未定 —— 待业务签字"))
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    assert d["items"]
    for i in d["items"]:
        assert (i.get("nature") or "").strip(), ("nature 缺失", i["kind"], i["title"])
        assert (i.get("evidence") or "").strip(), ("evidence 缺失", i["kind"], i["title"])
        assert "判定依据" not in i["because"], ("正文里不该再留判定依据", i["kind"])


def test_input_checked_only_for_exec_stages():
    """构思阶段不查执行输入——否则每条任务都报「缺验收标准」，等于没报。"""
    tid = _mk(title="early-stage", stage="brainstorming")
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    assert "acceptance_missing" not in _kinds(d)
    assert "scope_unknown" not in _kinds(d)
    assert d["counts"]["input"] == 0


def test_input_layer_reports_missing_inputs():
    """implementation 阶段 + queued：没工作区、没验收标准、没交付物，三项都要报。"""
    tid = _mk(title="no-inputs")
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    kinds = _kinds(d)
    assert "scope_unknown" in kinds
    assert "acceptance_missing" in kinds
    assert "deliverable_missing" in kinds
    assert d["counts"]["input"] >= 3
    # 都是 warning：不补也能跑，只是会返工
    for i in d["items"]:
        if i["kind"] in ("scope_unknown", "acceptance_missing", "deliverable_missing"):
            assert i["severity"] == "warning"


def test_input_layer_cleared_when_inputs_present():
    tid = _mk(title="has-inputs", workspace="E:/tmp/ws",
              acceptance_criteria="接口返回 200 且 nodeExtrema 非 null",
              deliverables=["docs/api.md"])
    kinds = _kinds(client.get(f"/api/v1/tasks/{tid}/readiness").json())
    assert "scope_unknown" not in kinds
    assert "acceptance_missing" not in kinds
    assert "deliverable_missing" not in kinds


def test_risk_layer_retry_and_bounce():
    tid = _mk(title="retried", attempt=3, bounce_count=4)
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    kinds = _kinds(d)
    assert "retry_history" in kinds and "bounce_history" in kinds
    risk = [i for i in d["items"] if i["layer"] == "risk"]
    assert all(i["severity"] == "warning" for i in risk)


def test_high_tier_unconfirmed_is_risk():
    tid = _mk(title="high-gate", gate_tier="high")
    kinds = _kinds(client.get(f"/api/v1/tasks/{tid}/readiness").json())
    assert "high_tier_unconfirmed" in kinds


def test_flow_blocking_comes_before_input_and_risk():
    """排序契约：blocking 永远排在 warning 前面；flow 层在 input/risk 之前。"""
    dep = _mk(title="dep-blocker", stage="planning")
    tid = _mk(title="ordering", stage="brainstorming", depends_on=[dep], attempt=3)
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    assert [i["order"] for i in d["items"]] == list(range(1, len(d["items"]) + 1))
    layers = [i["layer"] for i in d["items"] if i["severity"] == "blocking"]
    assert layers == sorted(layers, key=lambda x: {"flow": 0, "input": 1, "risk": 2}[x])
    first_warning = next((n for n, i in enumerate(d["items"]) if i["severity"] == "warning"), None)
    last_blocking = max((n for n, i in enumerate(d["items"]) if i["severity"] == "blocking"), default=-1)
    if first_warning is not None:
        assert last_blocking < first_warning


def test_plan_lists_top_three_blocking_with_reason():
    dep = _mk(title="dep-plan", stage="planning")
    tid = _mk(title="plan-order", stage="review", state=TaskState.FAILED, depends_on=[dep])
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    assert d["ready"] is False
    assert 1 <= len(d["plan"]) <= 3
    assert len(d["plan"]) == min(3, d["counts"]["blocking"])
    for p in d["plan"]:
        assert p["order"] and p["title"] and p["why"]
    # 第一条永远是 flow 层的失败态/依赖类
    assert d["plan"][0]["kind"] in ("state_failed", "dependency_dead", "dependency_open")


def test_terminal_task_is_ready_with_no_items():
    tid = _mk(title="done-task", stage="done", state=TaskState.COMPLETED)
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    assert d["items"] == [] and d["ready"] is True and d["plan"] == []


def test_context_package_docs_without_content_is_blocking():
    """拆解出来的子任务：上下文文档 chars=0 → 拿不到数据/规则，必须报 blocking。"""
    pkg = {
        "version": 1,
        "documents": [{"kind": "spec", "path": "docs/spec.md", "sections": ["输入"], "chars": 0}],
        "contracts": {},
    }
    tid = _mk(title="child-with-empty-ctx", role="child",
              context_package=__import__("json").dumps(pkg))
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    item = next(i for i in d["items"] if i["kind"] == "context_docs_missing")
    assert item["severity"] == "blocking"
    assert "docs/spec.md" in item["because"]


def test_consumer_without_contract_is_warning():
    parent = _mk(title="producer", stage="implementing")
    _mk(title="consumer", stage="planning", depends_on=[parent])
    d = client.get(f"/api/v1/tasks/{parent}/readiness").json()
    item = next((i for i in d["items"] if i["kind"] == "contract_missing"), None)
    assert item is not None and item["severity"] == "warning"


def test_design_stage_requires_scope_but_not_deliverable():
    """阶段递进：设计阶段要求「改哪里」，但还不要求交付物 / 验收标准。"""
    tid = _mk(title="design-stage", stage="design")
    kinds = _kinds(client.get(f"/api/v1/tasks/{tid}/readiness").json())
    assert "scope_unknown" in kinds
    assert "deliverable_missing" not in kinds
    assert "acceptance_missing" not in kinds


def test_doc_gap_reports_missing_sections(tmp_path):
    """文档写了但缺必需章节 → 报 doc_gap，因为执行时这部分只能靠猜。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "api.md").write_text("# 接口契约\n\n## 文档信息\n\n- 版本：v1\n", encoding="utf-8")
    tid = _mk(title="api-gap", stage="ready", workspace=str(ws),
              doc_paths={"api": "api.md"}, gate_tier="standard",
              acceptance_criteria="返回 200", deliverables=["api.md"], files=["api.md"])
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    item = next((i for i in d["items"] if i["kind"] == "doc_gap"), None)
    assert item is not None, _kinds(d)
    assert "缺关键章节" in item["title"]
    assert item["severity"] == "blocking"  # standard 档 + 已到 ready


def test_absolute_doc_path_not_reported_as_missing(tmp_path):
    """登记的是绝对路径、文件确实在，就不能报「文件不见了」。

    completion_gate 把登记路径当相对路径拼在 workspace 下判存在，
    没有 workspace 的存量任务会一律误报；readiness 用文档解析器复核后跳过。
    """
    doc = tmp_path / "requirement-abs.md"
    doc.write_text("# 需求\n\n## 背景\n\n测试\n", encoding="utf-8")
    tid = _mk(title="abs-doc", stage="ready",
              doc_paths={"requirement": str(doc).replace("\\", "/")},
              gate_tier="light", acceptance_criteria="看得到",
              deliverables=["x.md"], files=["x.md"])
    kinds = _kinds(client.get(f"/api/v1/tasks/{tid}/readiness").json())
    assert "doc_file_missing" not in kinds


def test_absolute_doc_path_missing_still_reported(tmp_path):
    """反过来：绝对路径指向的文件真没了，还得报。"""
    tid = _mk(title="abs-doc-gone", stage="ready",
              doc_paths={"requirement": str(tmp_path / "nope.md").replace("\\", "/")},
              gate_tier="light", acceptance_criteria="看得到",
              deliverables=["x.md"], files=["x.md"])
    kinds = _kinds(client.get(f"/api/v1/tasks/{tid}/readiness").json())
    assert "doc_file_missing" in kinds


def test_doc_gate_items_are_grouped(tmp_path):
    """多份文档停在同一个未达标状态 → 聚合成一条 + members，不再重复三遍。"""
    ws = tmp_path / "ws"
    ws.mkdir()
    for name in ("requirement", "spec", "api"):
        (ws / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    tid = _mk(title="doc-gate-group", stage="design",
              workspace=str(ws),
              doc_paths={k: f"{k}.md" for k in ("requirement", "spec", "api")})
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    gates = [i for i in d["items"] if i["kind"] == "doc_gate"]
    assert len(gates) == 1, [(i["kind"], i["title"]) for i in d["items"]]
    g = gates[0]
    assert "3 份文档" in g["title"]
    assert sorted(g["members"]) == ["技术方案", "接口契约", "需求文档"]
    assert g["severity"] == "blocking"
    # 单份文档不达标时保持原样，不强组
    (ws / "plan.md").write_text("# plan\n", encoding="utf-8")
    tid2 = _mk(title="doc-gate-single", stage="planning",
               workspace=str(ws), doc_paths={"plan": "plan.md"})
    d2 = client.get(f"/api/v1/tasks/{tid2}/readiness").json()
    gates2 = [i for i in d2["items"] if i["kind"] == "doc_gate"]
    assert len(gates2) == 1 and not gates2[0].get("members")


def test_response_carries_freshness_timestamps():
    """结论是现算的，响应要带 computed_at / inputs_updated_at，界面才能说「数据截至几点」。"""
    tid = _mk(title="freshness", stage="planning")
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    assert d["computed_at"]
    assert d["inputs_updated_at"]                      # 至少任务行有 updated_at
    assert d["computed_at"] >= d["inputs_updated_at"]   # 算的时刻不会早于输入


# 面板文案分两轮被用户打回：「太多口语化，主要事情说不清楚」。
# 这条用词表把口吻钉住——新增条目时写成「靠猜」「白烧一轮」这类话会直接失败。
_COLLOQUIAL = ("靠猜", "白烧", "打转", "干等", "说白了", "运气", "随便",
               "不用动", "别忘", "就行", "就行了", "点一下", "点儿", "搞定")


def test_panel_wording_stays_engineering(tmp_path):
    """就绪度面板所有用户可见文案：不允许口语化表达。"""
    from mio_taskhub.models import Subtask, SubtaskStatus
    ws = tmp_path / "ws"
    ws.mkdir()
    tid = _mk(title="wording-check", stage="implementing", gate_tier="high",
              workspace=str(ws), doc_paths={"requirement": "requirement.md"},
              attempt=3, bounce_count=4)
    with Session(engine) as s:
        s.add(Subtask(task_id=tid, status=SubtaskStatus.BLOCKED,
                      title="B1 阻塞：数据路线未定（D-0）—— 待业务签字；阻塞 N1 定稿/N4b"))
        s.add(Subtask(task_id=tid, title="N1 四级阈值推导 —— 0b3175e2（90min）【待定稿】"))
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    assert len(d["items"]) >= 5, _kinds(d)

    def _scan(text, where):
        for w in _COLLOQUIAL:
            assert w not in str(text), (w, where, text)

    for i in d["items"]:
        for key in ("title", "because", "consequence", "todo"):
            _scan(i.get(key) or "", f"{i['kind']}.{key}")
        for m in i.get("members") or []:
            if not isinstance(m, dict):
                continue
            for key in ("name", "note", "desc", "fix_label", "fix_action", "fix_owner"):
                _scan(m.get(key) or "", f"{i['kind']}.member.{key}")
            for bi in m.get("blocks_items") or []:
                _scan(bi.get("name") or "", f"{i['kind']}.blocks.name")


def test_blocking_items_explain_fully():
    """用户第五轮反馈「不能省文字，问题要说清楚」：blocking 条目必须四个要素齐。

    验收口径：
      because ≥ 40 字（要能说清是什么问题 + 机制，不是「acceptance_criteria 为空」）
      consequence ≥ 15 字
      steps ≥ 2 条（只有一句话的「怎么办」不合格）
      owner 非空（谁做这件事必须点名到角色）
    """
    from mio_taskhub.models import Subtask, SubtaskStatus
    tid = _mk(title="explain-fully", stage="design",
              doc_paths={"requirement": "requirement.md"}, gate_tier="high")
    with Session(engine) as s:
        s.add(Subtask(task_id=tid, status=SubtaskStatus.BLOCKED,
                      title="B1 阻塞：数据路线未定（D-0）—— 确认单已生成 docs/x.md，待业务签字；阻塞 N1 定稿"))
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/readiness").json()
    blocking = [i for i in d["items"] if i["severity"] == "blocking"]
    assert blocking
    for i in blocking:
        assert len(i["because"]) >= 40, (i["kind"], i["because"])
        assert len(i["consequence"]) >= 15, (i["kind"], i["consequence"])
        assert len(i.get("steps") or []) >= 2, (i["kind"], i.get("steps"))
        assert (i.get("owner") or "").strip(), i["kind"]
        # 性质分类与依据：面板上作为独立标签/独立行渲染，不能缺
        assert (i.get("nature") or "").strip(), i["kind"]
        assert (i.get("evidence") or "").strip(), i["kind"]
        assert "判定依据" not in i["because"], i["kind"]


def test_evidence_split_out_of_prose():
    """「判定依据：…」抽到 evidence 单独成行，正文不重复同一句。"""
    from mio_taskhub.api.blockers import split_evidence

    body, ev = split_evidence(
        "文档已经写出来了，但状态还停在草稿。判定依据：doc_statuses.plan 为 draft，目标状态 approved。")
    assert ev == "doc_statuses.plan 为 draft，目标状态 approved"
    assert "判定依据" not in body and "doc_statuses" not in body
    assert body.endswith("草稿。")

    body2, ev2 = split_evidence("没有判定依据的正文。")
    assert body2 == "没有判定依据的正文。" and ev2 == ""
