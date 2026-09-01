"""M1 状态机测试（追加于 b4d0712 的依赖/终态工具之上）。

覆盖：
 - State/Stage/ActorType 枚举
 - LEGAL_COMBOS 与 is_legal_combo
 - is_fully_done（Q1 锁定：completed AND done）
 - 14 类转换 T1–T15 的 happy/reject 路径
 - composite_status + block_reason
 - export_mapping_json
 - 回归：原有 is_terminal / dependency_satisfied / normalize_depends 行为不变
"""
import pytest
from mio_taskhub.status import (
    # 原有（回归）
    TERMINAL_STATES, is_terminal, dependency_satisfied,
    normalize_depends, task_deps,
    # M1 新增
    State, Stage, ActorType,
    LEGAL_COMBOS, is_legal_combo, is_fully_done, initial_state,
    TRANSITIONS, find_transition,
    validate_transition, IllegalTransition,
    composite_status, export_mapping_json, COMPOSITE_LABEL,
)


# ---------- 回归：原有 API 行为不变 ----------
class TestLegacyAPI:
    def test_terminal_states_unchanged(self):
        assert TERMINAL_STATES == {"completed", "cancelled", "failed", "blocked_failed"}

    def test_is_terminal_scheduling_terminal(self):
        class T:
            state = "cancelled"
            stage = "implementing"
        assert is_terminal(T()) is True

    def test_is_terminal_done_stage(self):
        class T:
            state = "claimed"
            stage = "done"
        assert is_terminal(T()) is True

    def test_is_terminal_not_terminal(self):
        class T:
            state = "running"
            stage = "implementing"
        assert is_terminal(T()) is False

    def test_dependency_satisfied_completed(self):
        class T:
            state = "completed"
            stage = "implementing"
            depends_on = None
        assert dependency_satisfied(T()) is True

    def test_dependency_satisfied_done(self):
        class T:
            state = "claimed"
            stage = "done"
            depends_on = None
        assert dependency_satisfied(T()) is True

    def test_normalize_none(self):
        assert normalize_depends(None) == []

    def test_normalize_plain_string_legacy(self):
        assert normalize_depends("t1") == ["t1"]

    def test_normalize_json_array(self):
        assert normalize_depends('["a","b"]') == ["a", "b"]

    def test_normalize_invalid_json(self):
        assert normalize_depends("[bad") == []

    def test_normalize_list(self):
        assert normalize_depends(["a", "", "b"]) == ["a", "b"]

    def test_task_deps(self):
        class T:
            depends_on = '["x","y"]'
        assert task_deps(T()) == ["x", "y"]


# ---------- M1：枚举 ----------
class TestEnums:
    def test_state_values(self):
        assert {s.value for s in State} == {
            "queued", "claimed", "running", "retrying",
            "completed", "failed", "cancelled",
        }

    def test_stage_values(self):
        assert {s.value for s in Stage} == {
            "brainstorming", "design", "planning", "ready",
            "implementing", "review", "done",
        }

    def test_actor_type_values(self):
        assert {a.value for a in ActorType} == {"user", "agent", "system"}


# ---------- M1：合法组合 ----------
class TestLegalCombos:
    def test_queued_pre_impl_and_impl(self):
        for st in [Stage.BRAINSTORMING, Stage.DESIGN, Stage.PLANNING, Stage.READY, Stage.IMPLEMENTING]:
            assert is_legal_combo(State.QUEUED, st)

    def test_running_only_implementing(self):
        assert is_legal_combo(State.RUNNING, Stage.IMPLEMENTING)
        for st in Stage:
            if st != Stage.IMPLEMENTING:
                assert not is_legal_combo(State.RUNNING, st), f"running+{st.value} 应非法"

    def test_retrying_only_implementing(self):
        # M1: RETRYING 可在所有非终态 stage（T9 从任意 FAILED stage 退避）
        assert is_legal_combo(State.RETRYING, Stage.IMPLEMENTING)
        for st in Stage:
            if st.value not in ("done", "cancelled"):
                assert is_legal_combo(State.RETRYING, st)

    def test_completed_not_in_pre_impl(self):
        # Q2
        for st in [Stage.BRAINSTORMING, Stage.DESIGN, Stage.PLANNING, Stage.READY]:
            assert not is_legal_combo(State.COMPLETED, st)

    def test_completed_in_impl_review_done(self):
        assert is_legal_combo(State.COMPLETED, Stage.IMPLEMENTING)
        assert is_legal_combo(State.COMPLETED, Stage.REVIEW)
        assert is_legal_combo(State.COMPLETED, Stage.DONE)

    def test_failed_not_in_ready_or_done(self):
        assert not is_legal_combo(State.FAILED, Stage.READY)
        assert not is_legal_combo(State.FAILED, Stage.DONE)

    def test_cancelled_not_in_done(self):
        assert not is_legal_combo(State.CANCELLED, Stage.DONE)

    def test_queued_not_in_review(self):
        # M1: (queued, review) 是合法组合（T5 允许 queued,review → completed,review）
        assert is_legal_combo(State.QUEUED, Stage.REVIEW)


# ---------- M1：is_fully_done（Q1 锁定）----------
class TestFullyDone:
    def test_only_completed_done(self):
        assert is_fully_done(State.COMPLETED, Stage.DONE) is True

    def test_completed_impl_not_fully_done(self):
        # Q1 严格
        assert is_fully_done(State.COMPLETED, Stage.IMPLEMENTING) is False
        assert is_fully_done(State.COMPLETED, Stage.REVIEW) is False

    def test_other_states_never_fully_done(self):
        for s in State:
            if s == State.COMPLETED:
                continue
            for st in Stage:
                assert is_fully_done(s, st) is False, f"{s.value}+{st.value} 误判 fully_done"

    def test_distinct_from_is_terminal(self):
        # 重要：is_fully_done 与 is_terminal 语义不同
        # cancelled+implementing：is_terminal=True（调度），is_fully_done=False（未完成）
        class T:
            state = "cancelled"
            stage = "implementing"
        assert is_terminal(T()) is True
        assert is_fully_done(State.CANCELLED, Stage.IMPLEMENTING) is False

    def test_initial(self):
        s, st = initial_state()
        assert (s, st) == (State.QUEUED, Stage.BRAINSTORMING)
        assert is_fully_done(s, st) is False


# ---------- M1：转换 happy ----------
class TestTransitionsHappy:
    def test_t1_claim(self):
        t = validate_transition(State.QUEUED, Stage.BRAINSTORMING,
                                State.CLAIMED, Stage.BRAINSTORMING, ActorType.AGENT)
        assert t.id == "T1" and t.event_type == "claimed"

    def test_t1_claim_user(self):
        t = validate_transition(State.QUEUED, Stage.DESIGN,
                                State.CLAIMED, Stage.DESIGN, ActorType.USER)
        assert t.id == "T1"

    def test_t1_claim_impl_user_denied(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.QUEUED, Stage.IMPLEMENTING,
                                State.CLAIMED, Stage.IMPLEMENTING, ActorType.USER)

    def test_t2_start(self):
        t = validate_transition(State.CLAIMED, Stage.IMPLEMENTING,
                                State.RUNNING, Stage.IMPLEMENTING, ActorType.AGENT)
        assert t.id == "T2"

    def test_t3_submit(self):
        t = validate_transition(State.RUNNING, Stage.IMPLEMENTING,
                                State.COMPLETED, Stage.IMPLEMENTING, ActorType.AGENT)
        assert t.id == "T3" and t.event_type == "completed"

    def test_t4_send_to_review(self):
        t = validate_transition(State.COMPLETED, Stage.IMPLEMENTING,
                                State.COMPLETED, Stage.REVIEW, ActorType.SYSTEM)
        assert t.id == "T4"

    def test_t5_review_pass(self):
        t = validate_transition(State.CLAIMED, Stage.REVIEW,
                                State.COMPLETED, Stage.REVIEW, ActorType.USER)
        assert t.id == "T5"

    def test_t6_finalize_fully_done(self):
        t = validate_transition(State.COMPLETED, Stage.REVIEW,
                                State.COMPLETED, Stage.DONE, ActorType.SYSTEM)
        assert t.id == "T6" and t.terminal is True
        assert is_fully_done(State.COMPLETED, Stage.DONE)

    def test_t7_review_reject(self):
        t = validate_transition(State.CLAIMED, Stage.REVIEW,
                                State.FAILED, Stage.REVIEW, ActorType.USER)
        assert t.id == "T7" and t.requires_reason is True

    def test_t8_bounce(self):
        t = validate_transition(State.FAILED, Stage.REVIEW,
                                State.QUEUED, Stage.IMPLEMENTING, ActorType.SYSTEM)
        assert t.id == "T8" and t.increments_bounce is True

    def test_t9_retry(self):
        t = validate_transition(State.FAILED, Stage.IMPLEMENTING,
                                State.RETRYING, Stage.IMPLEMENTING, ActorType.SYSTEM)
        assert t.id == "T9"

    def test_t10_retry_resume(self):
        t = validate_transition(State.RETRYING, Stage.IMPLEMENTING,
                                State.RUNNING, Stage.IMPLEMENTING, ActorType.AGENT)
        assert t.id == "T10"

    def test_t11_advance(self):
        t = validate_transition(State.CLAIMED, Stage.BRAINSTORMING,
                                State.CLAIMED, Stage.DESIGN, ActorType.USER)
        assert t.id == "T11"

    def test_t11_agent_denied(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.CLAIMED, Stage.BRAINSTORMING,
                                State.CLAIMED, Stage.DESIGN, ActorType.AGENT)

    def test_t12_start_impl(self):
        t = validate_transition(State.CLAIMED, Stage.READY,
                                State.CLAIMED, Stage.IMPLEMENTING, ActorType.AGENT)
        assert t.id == "T12"

    def test_t13_fail(self):
        t = validate_transition(State.RUNNING, Stage.IMPLEMENTING,
                                State.FAILED, Stage.IMPLEMENTING, ActorType.AGENT)
        assert t.id == "T13" and t.requires_reason is True

    def test_t14_cancel(self):
        t = validate_transition(State.QUEUED, Stage.READY,
                                State.CANCELLED, Stage.READY, ActorType.USER)
        assert t.id == "T14" and t.event_type == "cancelled"

    def test_t14_cancel_running(self):
        t = validate_transition(State.RUNNING, Stage.IMPLEMENTING,
                                State.CANCELLED, Stage.IMPLEMENTING, ActorType.SYSTEM)
        assert t.id == "T14"

    def test_t15_reopen(self):
        t = validate_transition(State.CANCELLED, Stage.IMPLEMENTING,
                                State.QUEUED, Stage.IMPLEMENTING, ActorType.USER)
        assert t.id == "T15" and t.event_type == "reopened"

    def test_t16_manual_retry_from_failed(self):
        t = validate_transition(State.FAILED, Stage.IMPLEMENTING,
                                State.QUEUED, Stage.READY, ActorType.USER)
        assert t.id == "T16" and t.requires_reason is True and t.event_type == "reopened"

    def test_t16_manual_retry_from_retrying(self):
        t = validate_transition(State.RETRYING, Stage.IMPLEMENTING,
                                State.QUEUED, Stage.READY, ActorType.USER)
        assert t.id == "T16"

    def test_t16_only_user(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.FAILED, Stage.IMPLEMENTING,
                                State.QUEUED, Stage.READY, ActorType.SYSTEM)


# ---------- M1：转换 reject（锁定不允许的路径）----------
class TestTransitionsReject:
    def test_undefined(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.QUEUED, Stage.BRAINSTORMING,
                                State.RUNNING, Stage.IMPLEMENTING, ActorType.AGENT)

    def test_illegal_target_combo(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.CANCELLED, Stage.IMPLEMENTING,
                                State.CANCELLED, Stage.DONE, ActorType.USER)

    def test_cannot_skip_to_done(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.RUNNING, Stage.IMPLEMENTING,
                                State.COMPLETED, Stage.DONE, ActorType.SYSTEM)

    def test_cannot_complete_impl_directly_to_done(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.COMPLETED, Stage.IMPLEMENTING,
                                State.COMPLETED, Stage.DONE, ActorType.SYSTEM)

    def test_cannot_skip_review_pass(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.CLAIMED, Stage.REVIEW,
                                State.COMPLETED, Stage.DONE, ActorType.SYSTEM)

    def test_t3_submit_only_agent(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.RUNNING, Stage.IMPLEMENTING,
                                State.COMPLETED, Stage.IMPLEMENTING, ActorType.USER)

    def test_t7_only_user(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.CLAIMED, Stage.REVIEW,
                                State.FAILED, Stage.REVIEW, ActorType.SYSTEM)

    def test_cannot_cancel_done(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.COMPLETED, Stage.DONE,
                                State.CANCELLED, Stage.DONE, ActorType.USER)

    def test_cannot_reopen_to_review(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.CANCELLED, Stage.REVIEW,
                                State.QUEUED, Stage.REVIEW, ActorType.USER)

    def test_no_implicit_unclaim(self):
        with pytest.raises(IllegalTransition):
            validate_transition(State.RUNNING, Stage.IMPLEMENTING,
                                State.CLAIMED, Stage.IMPLEMENTING, ActorType.AGENT)


# ---------- M1：转换不变量 ----------
class TestTransitionsInvariants:
    def test_all_land_legal(self):
        for t in TRANSITIONS:
            assert is_legal_combo(t.to_state, t.to_stage), \
                f"{t.id} lands in illegal: ({t.to_state.value},{t.to_stage.value})"

    def test_all_start_legal(self):
        for t in TRANSITIONS:
            assert is_legal_combo(t.from_state, t.from_stage), \
                f"{t.id} starts in illegal: ({t.from_state.value},{t.from_stage.value})"

    def test_index_unique(self):
        keys = [(t.from_state, t.from_stage, t.to_state, t.to_stage) for t in TRANSITIONS]
        assert len(keys) == len(set(keys)), "TRANSITIONS 存在重复 (from,to)"

    def test_t7_not_overridden_by_t13(self):
        t = find_transition(State.CLAIMED, Stage.REVIEW,
                            State.FAILED, Stage.REVIEW)
        assert t.id == "T7" and t.requires_reason is True


# ---------- M1：composite_status ----------
class TestCompositeStatus:
    def test_fully_done(self):
        r = composite_status(State.COMPLETED, Stage.DONE)
        assert r["label"] == "已完成" and r["tone"] == "ok" and r["is_terminal"] is True

    def test_running_live(self):
        r = composite_status(State.RUNNING, Stage.IMPLEMENTING)
        assert r["tone"] == "live" and r["is_terminal"] is False

    def test_completed_impl_not_fully_done(self):
        r = composite_status(State.COMPLETED, Stage.IMPLEMENTING)
        assert r["is_terminal"] is False
        assert "待评审" in r["label"]

    def test_completed_review_not_fully_done(self):
        r = composite_status(State.COMPLETED, Stage.REVIEW)
        assert r["is_terminal"] is False

    def test_block_reason(self):
        r = composite_status(State.QUEUED, Stage.READY, block_reason="依赖任务未完成")
        assert "依赖" in r["label"] and r["tone"] == "warn"

    def test_block_reason_truncated(self):
        r = composite_status(State.QUEUED, Stage.READY, block_reason="x" * 100)
        assert "…" in r["label"]

    def test_cancelled_muted(self):
        for st in [Stage.BRAINSTORMING, Stage.IMPLEMENTING, Stage.REVIEW]:
            r = composite_status(State.CANCELLED, st)
            assert r["label"] == "已取消" and r["tone"] == "muted"

    def test_failed_danger(self):
        r = composite_status(State.FAILED, Stage.IMPLEMENTING)
        assert r["tone"] == "danger"

    def test_retry_warn(self):
        r = composite_status(State.RETRYING, Stage.IMPLEMENTING)
        assert r["tone"] == "warn" and r["label"] == "重试中"

    def test_unknown_combo_safe(self):
        r = composite_status(State.RUNNING, Stage.DONE)  # 非法组合
        assert r["label"] == "未知" and r["tone"] == "muted"


# ---------- M1：export ----------
class TestExport:
    def test_shape(self):
        j = export_mapping_json()
        assert set(j) >= {
            "states", "stages", "actor_types",
            "legal_combos", "composite",
            "is_fully_done_check", "is_terminal_check_legacy", "transition_count",
        }

    def test_states_match(self):
        j = export_mapping_json()
        assert set(j["states"]) == {s.value for s in State}

    def test_legal_combos_count(self):
        j = export_mapping_json()
        assert len(j["legal_combos"]) == len(LEGAL_COMBOS)

    def test_composite_count(self):
        j = export_mapping_json()
        assert len(j["composite"]) == len(COMPOSITE_LABEL)

    def test_transition_count(self):
        j = export_mapping_json()
        # T1×5 + T2 + T3 + T4 + T5 + T6 + T7 + T8 + T9 + T10 + T11×3 + T12 + T13×6
        #   = 5+1+1+1+1+1+1+1+1+1+3+1+6 = 24 显式
        # + T14 (从所有非-cancelled 的合法组合) + T15 (从 cancelled/failed 到 queued)
        assert j["transition_count"] >= 30
