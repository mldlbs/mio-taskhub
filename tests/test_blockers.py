"""GET /tasks/{id}/blockers：阻塞源聚合的契约测试。

覆盖每类阻塞源的判定与 severity 分级——这里的断言就是前端「阻塞」区块的数据契约。
"""
from fastapi.testclient import TestClient
from sqlmodel import Session

from mio_taskhub.db import engine
from mio_taskhub.main import app
from mio_taskhub.models import Decision, Subtask, SubtaskStatus, Task, TaskStage, TaskState

client = TestClient(app)


def _mk(title="blocker-task", stage="planning", **kw):
    with Session(engine) as s:
        t = Task(title=title, stage=TaskStage(stage), **kw)
        s.add(t)
        s.commit()
        s.refresh(t)
        return t.id


def _kinds(r):
    return [i["kind"] for i in r.json()["items"]]


def test_no_blockers_returns_empty():
    tid = _mk()
    r = client.get(f"/api/v1/tasks/{tid}/blockers")
    assert r.status_code == 200
    d = r.json()
    assert d["blocked"] is False and d["items"] == []
    assert d["next_stage"] == "ready"


def test_missing_task_404():
    assert client.get("/api/v1/tasks/nope/blockers").status_code == 404


def test_dependency_open_is_queue_info_before_ready():
    """planning 阶段前置未完成 → info（等前置是正常排队，不是阻塞）。"""
    dep = _mk(title="dep-open")
    tid = _mk(title="blocked-by-open", depends_on=[dep])
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    items = [i for i in d["items"] if i["kind"] == "dependency_open"]
    assert len(items) == 1
    assert items[0]["severity"] == "info"
    assert items[0]["ref_id"] == dep
    assert d["blocked"] is False  # 排队不算阻塞


def test_dependency_open_downgraded_after_ready():
    """ready 之后后端不校验依赖，只算 warning，但 consequence 要写明越级风险。"""
    dep = _mk(title="dep-open2")
    tid = _mk(title="ready-with-open-dep", stage="ready", depends_on=[dep])
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    items = [i for i in d["items"] if i["kind"] == "dependency_open"]
    assert items and items[0]["severity"] == "warning"
    assert "不再检查" in items[0]["because"] or "不校验依赖" in items[0]["consequence"]
    assert d["blocked"] is False


def test_dependency_dead_when_prereq_failed():
    dep = _mk(title="dep-failed")
    with Session(engine) as s:
        t = s.get(Task, dep)
        t.state = TaskState.FAILED
        s.add(t)
        s.commit()
    tid = _mk(title="blocked-by-dead", depends_on=[dep])
    items = [i for i in client.get(f"/api/v1/tasks/{tid}/blockers").json()["items"]
             if i["kind"] == "dependency_dead"]
    assert items and items[0]["severity"] == "blocking"


def test_dependency_dead_when_prereq_missing():
    tid = _mk(title="blocked-by-missing", depends_on=["deadbeef"])
    items = [i for i in client.get(f"/api/v1/tasks/{tid}/blockers").json()["items"]
             if i["kind"] == "dependency_dead"]
    assert items and "依赖指向不存在的任务" == items[0]["title"]
    assert "deadbeef" in items[0]["because"]


def test_open_decision_is_blocking():
    tid = _mk(title="with-decision")
    r = client.post("/api/v1/decisions", json={
        "question": "这条要不要继续做？", "task_id": tid, "options": ["继续", "砍掉"],
    })
    assert r.status_code == 200, r.text
    items = [i for i in client.get(f"/api/v1/tasks/{tid}/blockers").json()["items"]
             if i["kind"] == "decision_open"]
    assert items and items[0]["severity"] == "blocking"
    assert "继续做" in items[0]["because"]


def test_subtask_open_is_queue_info():
    """子任务排队不是问题：severity=info，不进阻塞计数。"""
    tid = _mk(title="with-subtasks")
    with Session(engine) as s:
        s.add(Subtask(task_id=tid, title="子任务 A", status=SubtaskStatus.PENDING))
        s.add(Subtask(task_id=tid, title="子任务 B", status=SubtaskStatus.DONE))
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    items = [i for i in d["items"] if i["kind"] == "subtask_open"]
    assert items and items[0]["severity"] == "info"
    assert "1 条" in items[0]["title"]
    assert d["blocked"] is False      # 排队不算阻塞
    assert d["warning_count"] == 0    # 也不占「注意」


def test_failed_state_blocker_lists_three_ways_out():
    tid = _mk(title="failed-task", stage="review")
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.state = TaskState.FAILED
        s.add(t)
        s.commit()
    items = [i for i in client.get(f"/api/v1/tasks/{tid}/blockers").json()["items"]
             if i["kind"] == "state_failed"]
    assert items
    todo = items[0]["todo"]
    # 操作指引必须是界面上的按钮名，不能是 HTTP 命令（用户看不懂接口路径）
    assert "重试" in todo and "取消任务" in todo and "人工验收" in todo
    assert "POST " not in todo and "http" not in todo.lower()


def test_terminal_task_skips_stage_gates():
    tid = _mk(title="done-task", stage="done")
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.state = TaskState.COMPLETED
        s.add(t)
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    assert d["next_stage"] is None
    assert d["items"] == []
    assert d["blocked"] is False


def test_terminal_task_hides_residual_blockers():
    """已完成任务残留的 block_reason / 前置取消不该出现在阻塞面板。

    实测任务 4aca25a6（done）仍挂着 runtime_block + dependency_dead 两条 blocking，
    属于数据卫生问题；阻塞面板只回答「为什么动不了」，终结任务一律清空。
    """
    dep = _mk(title="dep-cancelled")
    with Session(engine) as s:
        d = s.get(Task, dep)
        d.state = TaskState.CANCELLED
        s.add(d)
        s.commit()
    tid = _mk(title="done-with-noise", stage="done",
              depends_on=[dep], block_reason="依赖未满足")
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.state = TaskState.COMPLETED
        s.add(t)
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    assert d["items"] == []
    assert d["blocked"] is False


def test_copy_uses_ui_words_not_api_paths():
    """文案守卫：todo 只说界面上的按钮名，because/consequence 不出现内部枚举。

    用户 2026-11-04 的反馈是「因为什么阻塞我看不懂」——根因就是文案里塞了
    `stage=implementing` 和 `POST /api/v1/...`。这条测试防止再退回去。
    """
    dep = _mk(title="dep-copy-guard")
    tid = _mk(title="copy-guard", stage="brainstorming", depends_on=[dep])
    client.post("/api/v1/decisions", json={"question": "要不要拆成两条？", "task_id": tid})
    with Session(engine) as s:
        s.add(Subtask(task_id=tid, title="子任务 A", status=SubtaskStatus.PENDING))
        s.commit()
    items = client.get(f"/api/v1/tasks/{tid}/blockers").json()["items"]
    assert len(items) >= 3
    for i in items:
        assert "POST " not in i["todo"], i
        assert "PATCH " not in i["todo"], i
        assert "stage=" not in i["because"] and "state=" not in i["because"], i
        assert "stage=" not in i["consequence"] and "state=" not in i["consequence"], i
        # 三段式必须齐全（前端按这三段渲染）
        assert i["title"] and i["because"] and i["consequence"] and i["todo"], i


# ---------- 子任务标题结构化（B 类阻塞原因 / N 系工作项） ----------

def test_parse_subtask_title_n_item():
    from mio_taskhub.api.blockers import _parse_subtask
    p = _parse_subtask("N2 文档链一致性修正 api/spec —— 1592b3bd（120min, dep N1）【候选项已出】")
    assert p["code"] == "N2"
    assert p["name"] == "文档链一致性修正 api/spec"
    assert p["mins"] == 120
    assert p["deps"] == ["N1"]
    assert p["note"] == "候选项已出"
    assert "1592b3bd" not in p["desc"] and "dep" not in p["desc"]


def test_parse_subtask_title_b_cause():
    from mio_taskhub.api.blockers import _parse_subtask
    p = _parse_subtask(
        "B1 阻塞：数据路线未定（D-0）—— 确认单已生成 docs/taskhub/976be80e/confirm.md；"
        "待业务勾选；阻塞 N1 定稿/N4b/N6")
    assert p["code"] == "B1" and p["is_cause"] is True
    assert p["name"] == "数据路线未定（D-0）"
    assert p["mins"] is None                      # B 类没有时长语义
    assert "976be80e" in p["desc"]                # 路径里的 hex 不是 hash，别误删


def test_parse_unstructured_title_falls_back():
    from mio_taskhub.api.blockers import _parse_subtask
    p = _parse_subtask("就是一句普通描述")
    assert p["code"] == "" and p["name"] == "就是一句普通描述"


def test_subtasks_split_into_cause_and_work(tmp_path):
    tid = _mk(title="b-n-split", stage="planning")
    with Session(engine) as s:
        s.add(Subtask(task_id=tid, status=SubtaskStatus.PENDING,
                      title="N1 四级阈值推导 —— 0b3175e2（90min）【定稿待 B1/B2】"))
        s.add(Subtask(task_id=tid, status=SubtaskStatus.PENDING,
                      title="N2 文档链修正 —— 1592b3bd（120min, dep N1）"))
        s.add(Subtask(task_id=tid, status=SubtaskStatus.BLOCKED,
                      title="B1 阻塞：数据路线未定 —— 待业务签字；阻塞 N1/N2"))
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    kinds = _kinds_from(d)
    assert "subtask_blocked" in kinds and "subtask_open" in kinds
    blocked = next(i for i in d["items"] if i["kind"] == "subtask_blocked")
    open_i = next(i for i in d["items"] if i["kind"] == "subtask_open")
    assert blocked["members"][0]["code"] == "B1"
    assert [m["code"] for m in open_i["members"]] == ["N1", "N2"]
    n2 = open_i["members"][1]
    assert n2["dep_open"] == ["N1"]               # N1 未完成 → 标「等 N1」
    assert "1 条" in open_i["because"]            # 只有 N1 能立即开工
    assert blocked["severity"] == "blocking"      # B 类是真阻塞
    assert open_i["severity"] == "info"           # N 系排队不是
    assert d["blocked"] is True


def _kinds_from(d):
    return [i["kind"] for i in d["items"]]


def test_deviation_description_surfaced():
    tid = _mk(title="with-deviation", stage="planning")
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.deviations = [{"id": "D-1", "severity": "high", "status": "open",
                         "title": "D-1",
                         "description": "原型是三态展示页，与规格的四级算法不一致：缺蓝/橙两级。"}]
        s.add(t)
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    dev = next(i for i in d["items"] if i["kind"] == "deviation_open")
    assert "四级算法不一致" in dev["because"]
    assert "D-1" in dev["title"]


def test_deviation_ranks_before_doc_gate(tmp_path):
    """因果顺序：实施计划的批准以设计问题关闭为前提——偏差（warning）
    也要排在文档门（blocking）前面，文档门条目点名上游前置。"""
    tid = _mk(title="dev-before-docgate", stage="design")
    (tmp_path / "plan.md").write_text("# 实施计划\n", encoding="utf-8")
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.workspace = str(tmp_path)
        t.doc_paths = {"plan": "plan.md"}
        t.doc_statuses = {"plan": {"state": "draft"}}
        t.deviations = [{"id": "D-1", "severity": "high", "status": "open",
                         "title": "D-1", "description": "原型与规格的四联页交互不一致。"}]
        s.add(t)
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    kinds = _kinds_from(d)
    assert "doc_gate" in kinds and "deviation_open" in kinds
    assert kinds.index("deviation_open") < kinds.index("doc_gate")
    gate = next(i for i in d["items"] if i["kind"] == "doc_gate")
    assert "未处理偏差" in gate["because"]
    assert "先处理上面列出的偏差与决策" in gate["steps"][0]
    dev = next(i for i in d["items"] if i["kind"] == "deviation_open")
    assert "已批准" in dev["consequence"]      # 偏差条目点明它是文档批准的前置
    assert "定稿" in dev["consequence"]


def test_doc_gate_without_upstream_keeps_plain_steps(tmp_path):
    """没有偏差/决策时，文档门条目不掺入上游提示（避免无中生有）。"""
    tid = _mk(title="docgate-no-upstream", stage="design")
    (tmp_path / "plan.md").write_text("# 实施计划\n", encoding="utf-8")
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.workspace = str(tmp_path)
        t.doc_paths = {"plan": "plan.md"}
        t.doc_statuses = {"plan": {"state": "draft"}}
        s.add(t)
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    gate = next(i for i in d["items"] if i["kind"] == "doc_gate")
    assert all("偏差" not in x for x in gate["steps"])
    assert "先后顺序" not in gate["because"]


def test_codes_expanded_inline_in_notes():
    """正文里出现的编号要就地补名字，读者不用去别处查「B1 是什么」。"""
    tid = _mk(title="code-expand", stage="planning")
    with Session(engine) as s:
        s.add(Subtask(task_id=tid, status=SubtaskStatus.PENDING,
                      title="N1 四级阈值推导 —— 0b3175e2（90min）【定稿待 B1/B2】"))
        s.add(Subtask(task_id=tid, status=SubtaskStatus.BLOCKED,
                      title="B1 阻塞：数据路线未定 —— 待业务签字"))
        s.add(Subtask(task_id=tid, status=SubtaskStatus.BLOCKED,
                      title="B2 阻塞：上游采样 2h/点 —— 需提频"))
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    open_i = next(i for i in d["items"] if i["kind"] == "subtask_open")
    n1 = open_i["members"][0]
    assert "B1「数据路线未定」" in n1["note"], n1["note"]
    assert "B2「上游采样 2h/点」" in n1["note"]
    # dep 展开成「编号「名字」」
    with Session(engine) as s:
        s.add(Subtask(task_id=tid, status=SubtaskStatus.PENDING,
                      title="N2 文档链修正 —— 1592b3bd（120min, dep N1）"))
        s.commit()
    d2 = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    open2 = next(i for i in d2["items"] if i["kind"] == "subtask_open")
    n2 = next(m for m in open2["members"] if m["code"] == "N2")
    assert n2["dep_open_txt"] == "N1「四级阈值推导」", n2["dep_open_txt"]


def test_code_expansion_does_not_touch_unknown_tokens():
    """V1、Q-2、D-0 这些查不到的 token 原样保留，不许误伤。"""
    from mio_taskhub.api.blockers import _expand_codes
    name_map = {"B1": "数据路线未定"}
    assert "客户 V1 口径" in _expand_codes("客户 V1 口径", name_map)
    assert "Q-2" in _expand_codes("未实测（Q-2）", name_map)
    assert "B1「数据路线未定」" in _expand_codes("待 B1 签字", name_map)


def test_deviation_and_decision_codes_expanded():
    """偏差/决策编号也能就地展开：D-1 → 偏差描述，[D-0] → 决策问题。"""
    tid = _mk(title="d-code-expand", stage="planning")
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.deviations = [{"id": "D-1", "severity": "high", "status": "open",
                         "title": "D-1", "description": "原型与规格的四级算法不一致。"}]
        s.add(t)
        s.add(Subtask(task_id=tid, status=SubtaskStatus.BLOCKED,
                      title="B1 阻塞：数据路线未定（D-0）—— 待签字"))
        s.add(Decision(task_id=tid, status="open",
                       question="[D-0] 数据路线用哪套：实时推 or 历史表回放",
                       options='["实时推", "历史表"]'))
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    blocked = next(i for i in d["items"] if i["kind"] == "subtask_blocked")
    b1 = blocked["members"][0]
    assert "D-0「决策：数据路线用哪套：实时推 or 历史表…」" in b1["name"], b1["name"]
    # 偏差自身一行照旧，正文里引用 D-1 的地方会带描述
    with Session(engine) as s:
        t2 = s.get(Task, tid)
        t2.deviations = [{"id": "D-1", "severity": "high", "status": "open",
                          "title": None,
                          "description": "待 B1 签字后复核：原型与规格四级算法不一致。"}]
        s.add(t2)
        s.commit()
    d2 = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    dev = next(i for i in d2["items"] if i["kind"] == "deviation_open")
    assert "四级算法不一致" in dev["because"]


def test_cause_fix_classification():
    """B 类阻塞要有解决路径：类型 + 具体动作，别只说「有问题」。"""
    from mio_taskhub.api.blockers import _cause_fix, _parse_subtask
    cases = [
        ("B1 阻塞：数据路线未定（D-0）—— 确认单已生成，待业务勾选签字",
         "confirm", "审批确认"),
        ("B2 阻塞：上游采样 2h/点 —— 需提频 15min", "coordinate", "协调上游"),
        ("B3 阻塞：水位指标名/量纲未实测（Q-2）", "data", "补资料"),
        ("B4 阻塞：本地历史表漏落 82% —— 纯工程可修", "engineering", "工程修复"),
        ("B5 阻塞：三项能力均无外部责任方", "assign", "指派责任方"),
    ]
    for title, ftype, label in cases:
        p = _parse_subtask(title)
        fix = _cause_fix(p)
        assert fix["fix_type"] == ftype, (title, fix)
        assert fix["fix_label"] == label
        assert len(fix["fix_action"]) >= 8
        assert fix["fix_owner"]
        # 工程口径：陈述句，不出现劝说/口语
        for word in ("别", "你", "催", "干等", "说死", "悬着", "挂着", "砍掉"):
            assert word not in fix["fix_action"], (title, fix["fix_action"])


def test_cause_fix_author_override():
    """作者写了「解决：…」就以作者为准，不做关键词猜测。"""
    from mio_taskhub.api.blockers import _cause_fix, _parse_subtask
    p = _parse_subtask("B9 阻塞：环境证书过期 —— 解决：找运维换证书")
    fix = _cause_fix(p)
    assert fix["fix_type"] == "custom"
    assert fix["fix_action"] == "找运维换证书"


def test_blocks_txt_split_from_desc():
    """「卡住谁」从现状里拆出来单独一行，desc 只留原因/现状，不重复。"""
    from mio_taskhub.api.blockers import _parse_subtask
    p = _parse_subtask("B4 阻塞：本地历史表漏落 82% —— 纯工程可修，阻塞 N6 验证")
    assert p["desc"] == "纯工程可修"
    assert "N6 验证" in p["blocks_txt"]
    p2 = _parse_subtask(
        "B1 阻塞：数据路线未定（D-0）—— 确认单已生成，待业务勾选签字；阻塞 N1 定稿/N4b")
    assert "待业务勾选签字" in p2["desc"]
    assert "N1 定稿" in p2["blocks_txt"] and "N4b" in p2["blocks_txt"]
    # 「仅阻塞三项新增能力」是范围说明，不是卡住对象，不拆
    p3 = _parse_subtask("B5 阻塞：三项能力无责任方 —— 仅阻塞三项新增能力")
    assert p3["blocks_txt"] == ""


def test_blocked_subtask_without_targets_is_warning_not_blocking():
    """条目写着「不阻塞 …」时不算硬阻塞：降为 warning，且不计入 blocking 数。

    实测背景：任务 976be80e 的 B5 明确写「仅阻塞三项新增能力，不阻塞 N1…N13」，
    却被当成「1 项外部条件未落实」报成 blocking，把阻塞计数报虚。
    """
    from mio_taskhub.models import Subtask, SubtaskStatus
    tid = _mk(title="soft-block", stage="design")
    with Session(engine) as s:
        s.add(Subtask(task_id=tid, status=SubtaskStatus.BLOCKED,
                      title="B5 阻塞：三项目能力均无外部责任方 —— 不阻塞 N1/N2"))
        s.commit()
    d = client.get(f"/api/v1/tasks/{tid}/blockers").json()
    soft = [i for i in d["items"] if i["kind"] == "subtask_blocked"]
    assert len(soft) == 1
    assert soft[0]["severity"] == "warning"
    assert "不阻塞本任务" in soft[0]["title"]
    assert d["blocked"] is False

    # 同一批条目里有真阻塞时，只有真阻塞进 blocking 那条
    tid2 = _mk(title="hard-and-soft", stage="design")
    with Session(engine) as s:
        s.add(Subtask(task_id=tid2, status=SubtaskStatus.BLOCKED,
                      title="B1 阻塞：数据路线未定 —— 阻塞 N1 定稿"))
        s.add(Subtask(task_id=tid2, status=SubtaskStatus.BLOCKED,
                      title="B5 阻塞：新增能力无责任方 —— 不阻塞 N1"))
        s.commit()
    d2 = client.get(f"/api/v1/tasks/{tid2}/blockers").json()
    rows = [i for i in d2["items"] if i["kind"] == "subtask_blocked"]
    assert {r["severity"] for r in rows} == {"blocking", "warning"}
    blocking = next(r for r in rows if r["severity"] == "blocking")
    assert [m["code"] for m in blocking["members"]] == ["B1"]


def test_discussion_gate_exemption_surfaced_in_readiness():
    """requirement 已批准 → 就绪度面板不再报「缺少讨论记录」；草稿/未登记时照常报。"""
    from mio_taskhub.models import TaskState
    tid = _mk(title="disc-exempt", stage="brainstorming",
              doc_paths={"requirement": "requirement.md"})
    with Session(engine) as s:
        t = s.get(Task, tid)
        t.doc_statuses = {"requirement": {"state": "approved"}}
        s.add(t)
        s.commit()
    items = client.get(f"/api/v1/tasks/{tid}/blockers").json()["items"]
    assert not [i for i in items if i["kind"] == "discussion_required"]

    # 对照组：草稿状态仍然要报
    tid2 = _mk(title="disc-no-exempt", stage="brainstorming",
               doc_paths={"requirement": "requirement.md"})
    with Session(engine) as s:
        t = s.get(Task, tid2)
        t.doc_statuses = {"requirement": {"state": "draft"}}
        s.add(t)
        s.commit()
    items2 = client.get(f"/api/v1/tasks/{tid2}/blockers").json()["items"]
    disc = [i for i in items2 if i["kind"] == "discussion_required"]
    assert disc and disc[0]["severity"] == "blocking"
    assert "方向确认" in disc[0]["title"]
    assert "已批准" in disc[0]["because"]      # 文案要讲清豁免条件
