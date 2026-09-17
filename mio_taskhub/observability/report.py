"""结构化、agent 可消费的可观测性采集器 + integrity_check。

取代旧版 /observability/summary 用正则解析 Prometheus 文本取数的脆弱做法。
一次调用返回完整结构化快照，agent 或脚本可直接消费，无需拼 5+ 端点。
"""
import os
import time
import logging
from sqlmodel import Session, text

from mio_taskhub.db import engine, check_connection
from mio_taskhub.middleware import get_http_metrics
from mio_taskhub.background import get_thread_health
from mio_taskhub.observability.slo_history import get_slo_history
from mio_taskhub.observability.metrics import _start_time

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# 组件状态优先级（用于计算 overall_status）
_STATUS_RANK = {"FAIL": 0, "WARN": 1, "PASS": 2}
_STATUS_NAME = {0: "FAIL", 1: "WARN", 2: "PASS"}


def _safe(fn, default=None):
    """单个采集段失败不影响整份报告，返回 default。"""
    try:
        return fn()
    except Exception:
        logger.exception("observability collector subsection failed")
        return default


def _collect_database() -> dict:
    conn = check_connection()
    data = {"reachable": bool(conn.get("ok"))}
    try:
        pool = engine.pool
        size = pool.size()
        max_overflow = getattr(pool, "_max_overflow", 0) or 0
        capacity = size + max_overflow
        checked_out = pool.checkedout()
        data.update({
            "pool_size": size,
            "pool_checked_out": checked_out,
            "pool_checked_in": pool.checkedin(),
            "pool_overflow": pool.overflow(),
            "pool_utilization": round(checked_out / capacity, 4) if capacity > 0 else None,
        })
    except Exception:
        data["pool_utilization"] = None
    return data


def _collect_http() -> dict:
    hm = get_http_metrics()
    total = hm.get("request_count_total", 0)
    errors = sum(hm.get("error_count", {}).values())
    rate = (errors / total) if total else 0.0
    return {
        "request_count_total": total,
        "active_requests": hm.get("active_requests", 0),
        "errors_by_class": hm.get("error_count", {}),
        "error_count_total": errors,
        "error_rate": round(rate, 4),
        "total_duration_ms": round(sum(hm.get("total_duration_ms", {}).values()), 1),
    }


def _collect_process() -> dict:
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        return {
            "pid": os.getpid(),
            "uptime_seconds": round(time.time() - _start_time, 1),
            "cpu_percent": round(proc.cpu_percent(interval=0.1), 2),
            "memory_rss_mb": round(proc.memory_info().rss / 1048576, 1),
            "memory_percent": round(proc.memory_percent(), 2),
            "threads": proc.num_threads(),
        }
    except Exception:
        return {"pid": os.getpid(), "error": "process metrics unavailable"}


def _collect_threads() -> dict:
    th = get_thread_health()
    total = len(th)
    alive = sum(1 for d in th.values() if d.get("alive"))
    return {
        "total": total,
        "alive": alive,
        "utilization": round(alive / total, 4) if total else 0.0,
        "detail": {
            name: {
                "alive": d.get("alive"),
                "heartbeat_age_seconds": d.get("age_seconds"),
                "consecutive_failures": d.get("consecutive_failures", 0),
            }
            for name, d in th.items()
        },
    }


def _collect_tasks() -> dict:
    with Session(engine) as s:
        rate = s.exec(text(
            "SELECT "
            "SUM(CASE WHEN state='COMPLETED' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN state='FAILED' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN state='CANCELLED' THEN 1 ELSE 0 END), "
            "COUNT(*) FROM task WHERE state IN ('COMPLETED','FAILED','CANCELLED')"
        )).first()
        completed, failed, cancelled, total = rate if rate else (0, 0, 0, 0)
        success_rate = round(completed / total, 4) if total else None
        failure_rate = round(failed / total, 4) if total else None
        cancel_rate = round(cancelled / total, 4) if total else None

        by_state = {
            st: c for st, c in s.exec(
                text("SELECT state, COUNT(*) FROM task GROUP BY state")
            ).all()
        }
        t24 = s.exec(text(
            "SELECT COUNT(*) FROM task WHERE created_at IS NOT NULL "
            "AND julianday('now')-julianday(created_at) <= 1.0"
        )).first()
        t7 = s.exec(text(
            "SELECT COUNT(*) FROM task WHERE created_at IS NOT NULL "
            "AND julianday('now')-julianday(created_at) <= 7.0"
        )).first()
        retries = s.exec(text(
            "SELECT SUM(retry_count), AVG(retry_count), MAX(retry_count), "
            "SUM(CASE WHEN retry_count>0 THEN 1 ELSE 0 END) "
            "FROM task WHERE state IN ('COMPLETED','FAILED','CANCELLED')"
        )).first() or (0, 0, 0, 0)
        avg_c = s.exec(text(
            "SELECT AVG(julianday(completed_at)-julianday(created_at))*86400.0 "
            "FROM task WHERE state='COMPLETED' AND created_at IS NOT NULL AND completed_at IS NOT NULL"
        )).first()

    return {
        "total_terminal": total,
        "success_rate": success_rate,
        "failure_rate": failure_rate,
        "cancel_rate": cancel_rate,
        "by_state": by_state,
        "throughput_24h": t24[0] if t24 else 0,
        "throughput_7d": t7[0] if t7 else 0,
        "retries_total": retries[0] or 0,
        "retries_avg": round(retries[1], 2) if retries[1] is not None else None,
        "retries_max": retries[2] or 0,
        "retried_count": retries[3] or 0,
        "avg_completion_seconds": round(avg_c[0], 1) if avg_c and avg_c[0] is not None else None,
    }


def _collect_agents() -> dict:
    with Session(engine) as s:
        by_status = {
            st: c for st, c in s.exec(
                text("SELECT status, COUNT(*) FROM agent GROUP BY status")
            ).all()
        }
        online = s.exec(text("SELECT COUNT(*) FROM agent WHERE status='online'")).first()
    online = online[0] if online else 0
    return {
        "by_status": by_status,
        "online": online,
        "active": None,  # task 表无 agent 关联列，无法统计运行中的 agent 数
        "utilization": None,
    }


def _collect_slo() -> dict:
    with Session(engine) as s:
        avail = s.exec(text(
            "SELECT SUM(CASE WHEN state='COMPLETED' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN state='FAILED' THEN 1 ELSE 0 END) "
            "FROM task WHERE state IN ('COMPLETED','FAILED') "
            "AND created_at IS NOT NULL "
            "AND julianday('now')-julianday(created_at) <= 30.0"
        )).first()
    if avail and (avail[0] or 0) + (avail[1] or 0) > 0:
        a = (avail[0] or 0) / ((avail[0] or 0) + (avail[1] or 0))
        budget_remaining = max(0.0, 0.01 - (1 - a))
        return {
            "availability_30d": round(a, 6),
            "target": 0.99,
            "breach": a < 0.99,
            "error_budget_remaining": round(budget_remaining, 6),
        }
    return {"availability_30d": None, "target": 0.99, "breach": None, "error_budget_remaining": None}


def _collect_alerts() -> dict:
    from mio_taskhub.observability.alerts import get_alert_manager
    mgr = get_alert_manager()
    if not mgr:
        return {"active": [], "active_count": 0, "total": 0}
    mgr.evaluate()
    all_a = mgr.get_all()
    active = [a for a in all_a if a.get("active")]
    return {"active": active, "active_count": len(active), "total": len(all_a)}


def _collect_insights() -> dict:
    from mio_taskhub.observability.insights import InsightsEngine
    eng = InsightsEngine()
    recent = eng.recent(limit=100)
    unack = [i for i in recent if not i.get("acknowledged")]
    return {
        "unacknowledged": len(unack),
        "recent_total": len(recent),
        "items": recent[:20],
    }


def _collect_ideas() -> dict:
    """灵感漏斗 + 同步管线 + 评估覆盖。

    字段均来自 idea / task 表真实列，无虚构：
    - by_status / generated_* / mio_intelligence_synced / adr_count / reviewed_count 直查
    - broken_down_count: 经 Task.idea_id 关联统计（拆解=把灵感拆成任务）
    - stuck_new_7d: 卡在 new 状态超 7 天未推进，反映灵感堆积健康
    - scoring.persisted=False: 评分是 idea_scoring 按需计算、未落库，故不报评分覆盖率
    """
    with Session(engine) as s:
        total = s.exec(text("SELECT COUNT(*) FROM idea")).first()
        by_status = {
            st: c for st, c in s.exec(
                text("SELECT status, COUNT(*) FROM idea GROUP BY status")
            ).all()
        }
        g24 = s.exec(text(
            "SELECT COUNT(*) FROM idea WHERE created_at IS NOT NULL "
            "AND julianday('now')-julianday(created_at) <= 1.0"
        )).first()
        g7 = s.exec(text(
            "SELECT COUNT(*) FROM idea WHERE created_at IS NOT NULL "
            "AND julianday('now')-julianday(created_at) <= 7.0"
        )).first()
        mi = s.exec(text(
            "SELECT COUNT(*) FROM idea WHERE labels LIKE '%mio-intelligence%'"
        )).first()
        adr = s.exec(text(
            "SELECT COUNT(*) FROM idea WHERE idea_type='adr' OR adr_status IS NOT NULL"
        )).first()
        reviewed = s.exec(text(
            "SELECT COUNT(*) FROM idea WHERE review_count > 0"
        )).first()
        broken = s.exec(text(
            "SELECT COUNT(DISTINCT idea_id) FROM task "
            "WHERE idea_id IS NOT NULL AND idea_id <> ''"
        )).first()
        stuck = s.exec(text(
            "SELECT COUNT(*) FROM idea WHERE status='new' AND created_at IS NOT NULL "
            "AND julianday('now')-julianday(created_at) > 7.0"
        )).first()

    total = total[0] if total else 0
    g24 = g24[0] if g24 else 0
    g7 = g7[0] if g7 else 0
    mi = mi[0] if mi else 0
    adr = adr[0] if adr else 0
    reviewed = reviewed[0] if reviewed else 0
    broken = broken[0] if broken else 0
    stuck = stuck[0] if stuck else 0

    archived = by_status.get("archived", 0)
    cancelled = by_status.get("cancelled", 0)
    active = total - archived - cancelled
    review_coverage = round(reviewed / total, 4) if total else None
    breakdown_coverage = round(broken / active, 4) if active else None
    stuck_ratio = round(stuck / active, 4) if active else None

    return {
        "total": total,
        "by_status": by_status,
        "generated_24h": g24,
        "generated_7d": g7,
        "mio_intelligence_synced": mi,
        "adr_count": adr,
        "reviewed_count": reviewed,
        "review_coverage": review_coverage,
        "broken_down_count": broken,
        "breakdown_coverage": breakdown_coverage,
        "stuck_new_7d": stuck,
        "stuck_new_ratio": stuck_ratio,
        "scoring": {
            "persisted": False,
            "note": "评分由 idea_scoring 按需计算、未落库；review_coverage 可作评估覆盖代理",
        },
    }


def collect_observability() -> dict:
    """返回完整结构化可观测性快照（不解析 Prometheus 文本）。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "service": "mio-taskhub",
        "database": _safe(_collect_database, {"reachable": None, "error": "collector failure"}),
        "http": _safe(_collect_http, {}),
        "process": _safe(_collect_process, {"pid": None, "error": "collector failure"}),
        "threads": _safe(_collect_threads, {"total": 0, "alive": 0, "utilization": 0.0, "detail": {}}),
        "tasks": _safe(_collect_tasks, {}),
        "agents": _safe(_collect_agents, {"by_status": {}, "online": 0, "active": 0, "utilization": None}),
        "slo": _safe(_collect_slo, {"availability_30d": None, "target": 0.99, "breach": None, "error_budget_remaining": None}),
        "alerts": _safe(_collect_alerts, {"active": [], "active_count": 0, "total": 0}),
        "insights": _safe(_collect_insights, {"unacknowledged": 0, "recent_total": 0, "items": []}),
        "ideas": _safe(_collect_ideas, {
            "total": 0, "by_status": {}, "generated_24h": 0, "generated_7d": 0,
            "mio_intelligence_synced": 0, "adr_count": 0, "reviewed_count": 0,
            "review_coverage": None, "broken_down_count": 0, "breakdown_coverage": None,
            "stuck_new_7d": 0, "stuck_new_ratio": None,
            "scoring": {"persisted": False, "note": "评分按需计算，未落库"},
        }),
    }


def _component(name: str, status: str, detail: str = None, value=None, threshold=None) -> dict:
    return {"name": name, "status": status, "detail": detail, "value": value, "threshold": threshold}


def integrity_check() -> dict:
    """一次性 integrity_check 风格总报告：每组件 PASS/WARN/FAIL。

    返回结构可被 agent/脚本直接消费，配合 snapshot 字段拿到完整结构化快照。
    """
    obs = collect_observability()
    components = []

    # database
    db = obs["database"]
    components.append(_component(
        "database",
        "PASS" if db.get("reachable") else "FAIL",
        "sqlite reachable" if db.get("reachable") else "db unreachable",
    ))

    # http_error_rate
    http = obs["http"]
    total = http.get("request_count_total", 0)
    rate = http.get("error_rate") or 0.0
    if total <= 10:
        components.append(_component("http_error_rate", "PASS", f"样本不足 (total={total})", rate, 0.05))
    elif rate > 0.05:
        components.append(_component("http_error_rate", "FAIL", f"错误率 {rate*100:.1f}% 超过 5% 阈值", rate, 0.05))
    elif rate > 0.02:
        components.append(_component("http_error_rate", "WARN", f"错误率 {rate*100:.1f}%", rate, 0.05))
    else:
        components.append(_component("http_error_rate", "PASS", f"错误率 {rate*100:.1f}%", rate, 0.05))

    # slo_availability
    av = obs["slo"].get("availability_30d")
    if av is None:
        components.append(_component("slo_availability", "PASS", "无 SLO 数据", av, 0.99))
    elif av < 0.99:
        components.append(_component(
            "slo_availability", "FAIL" if av < 0.95 else "WARN",
            f"30d 可用性 {av*100:.1f}% 低于 99% 目标", av, 0.99))
    else:
        components.append(_component("slo_availability", "PASS", f"30d 可用性 {av*100:.1f}%", av, 0.99))

    # task_failure_rate
    fr = obs["tasks"].get("failure_rate")
    if fr is None:
        components.append(_component("task_failure_rate", "PASS", "无终端任务", fr, {"warn": 0.05, "critical": 0.15}))
    elif fr >= 0.15:
        components.append(_component("task_failure_rate", "FAIL", f"失败率 {fr*100:.1f}% >= 15%", fr, {"warn": 0.05, "critical": 0.15}))
    elif fr >= 0.05:
        components.append(_component("task_failure_rate", "WARN", f"失败率 {fr*100:.1f}% >= 5%", fr, {"warn": 0.05, "critical": 0.15}))
    else:
        components.append(_component("task_failure_rate", "PASS", f"失败率 {fr*100:.1f}%", fr, {"warn": 0.05, "critical": 0.15}))

    # task_success_rate
    sr = obs["tasks"].get("success_rate")
    if sr is None:
        components.append(_component("task_success_rate", "PASS", "无终端任务", sr, {"warn_low": 0.90, "critical_low": 0.80}))
    elif sr <= 0.80:
        components.append(_component("task_success_rate", "FAIL", f"成功率 {sr*100:.1f}% <= 80%", sr, {"warn_low": 0.90, "critical_low": 0.80}))
    elif sr <= 0.90:
        components.append(_component("task_success_rate", "WARN", f"成功率 {sr*100:.1f}% <= 90%", sr, {"warn_low": 0.90, "critical_low": 0.80}))
    else:
        components.append(_component("task_success_rate", "PASS", f"成功率 {sr*100:.1f}%", sr, {"warn_low": 0.90, "critical_low": 0.80}))

    # thread_pool
    th = obs["threads"]
    if th["total"] == 0:
        components.append(_component("thread_pool", "PASS", "无后台线程", th["utilization"], 1.0))
    elif th["alive"] == th["total"]:
        components.append(_component("thread_pool", "PASS", f"{th['alive']}/{th['total']} 存活", th["utilization"], 1.0))
    else:
        components.append(_component("thread_pool", "FAIL", f"仅 {th['alive']}/{th['total']} 存活", th["utilization"], 1.0))

    # db_pool_utilization
    pu = db.get("pool_utilization")
    if pu is None:
        components.append(_component("db_pool_utilization", "PASS", "池指标不可用", pu, {"warn": 0.80, "critical": 0.95}))
    elif pu >= 0.95:
        components.append(_component("db_pool_utilization", "FAIL", f"池利用率 {pu*100:.1f}% >= 95%", pu, {"warn": 0.80, "critical": 0.95}))
    elif pu >= 0.80:
        components.append(_component("db_pool_utilization", "WARN", f"池利用率 {pu*100:.1f}% >= 80%", pu, {"warn": 0.80, "critical": 0.95}))
    else:
        components.append(_component("db_pool_utilization", "PASS", f"池利用率 {pu*100:.1f}%", pu, {"warn": 0.80, "critical": 0.95}))

    # process_resources
    proc = obs["process"]
    cpu = proc.get("cpu_percent")
    mem = proc.get("memory_percent")
    crit = (cpu is not None and cpu >= 95) or (mem is not None and mem >= 95)
    warn = (cpu is not None and cpu >= 80) or (mem is not None and mem >= 80)
    if crit:
        pstatus = "FAIL"
    elif warn:
        pstatus = "WARN"
    else:
        pstatus = "PASS"
    issues = []
    if cpu is not None and cpu >= 80:
        issues.append(f"CPU {cpu}%")
    if mem is not None and mem >= 80:
        issues.append(f"MEM {mem}%")
    components.append(_component(
        "process_resources", pstatus,
        "; ".join(issues) if issues else "资源正常",
        {"cpu_percent": cpu, "memory_percent": mem}, {"warn": 80, "critical": 95}))

    # insights_backlog（捕捉刷屏/积压）
    un = obs["insights"]["unacknowledged"]
    if un >= 200:
        components.append(_component("insights_backlog", "FAIL", f"{un} 条未确认洞察", un, {"warn": 50, "critical": 200}))
    elif un >= 50:
        components.append(_component("insights_backlog", "WARN", f"{un} 条未确认洞察", un, {"warn": 50, "critical": 200}))
    else:
        components.append(_component("insights_backlog", "PASS", f"{un} 条未确认洞察", un, {"warn": 50, "critical": 200}))

    # active_alerts
    ac = obs["alerts"]["active_count"]
    components.append(_component(
        "active_alerts", "FAIL" if ac > 0 else "PASS",
        f"{ac} 条活动告警", ac, 0))

    # ideas_funnel（灵感堆积健康）
    ideas = obs["ideas"]
    stuck_ratio = ideas.get("stuck_new_ratio")
    if ideas.get("total", 0) == 0:
        components.append(_component("ideas_funnel", "PASS", "无灵感数据", None, {"warn": 0.2, "critical": 0.5}))
    elif stuck_ratio is None:
        components.append(_component("ideas_funnel", "PASS", "漏斗指标不可用", None, {"warn": 0.2, "critical": 0.5}))
    elif stuck_ratio >= 0.5:
        components.append(_component(
            "ideas_funnel", "FAIL",
            f"{ideas['stuck_new_7d']} 条灵感卡在 new 超 7 天 (占比 {stuck_ratio*100:.0f}%)",
            stuck_ratio, {"warn": 0.2, "critical": 0.5}))
    elif stuck_ratio >= 0.2:
        components.append(_component(
            "ideas_funnel", "WARN",
            f"{ideas['stuck_new_7d']} 条灵感卡在 new 超 7 天 (占比 {stuck_ratio*100:.0f}%)",
            stuck_ratio, {"warn": 0.2, "critical": 0.5}))
    else:
        components.append(_component(
            "ideas_funnel", "PASS",
            f"灵感漏斗健康，卡住占比 {stuck_ratio*100:.0f}%",
            stuck_ratio, {"warn": 0.2, "critical": 0.5}))

    overall_rank = min((_STATUS_RANK[c["status"]] for c in components), default=2)
    overall_status = _STATUS_NAME[overall_rank]

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": time.time(),
        "service": "mio-taskhub",
        "overall_status": overall_status,
        "components": components,
        "snapshot": obs,
    }
