# -*- coding: utf-8 -*-
"""E7 决策清单扫描（capability-v0.7 · 只读实验，2026-11-04）。

目的：验证「一张人工决策清单」是否有用——先把 hub 现有任务按
「待你拍板 / 待你验收 / 待你决定重试 / 待启动 / 待补外部输入」分桶，
再单列「建议清理」的噪声量，让用户判断值不值得做 Decision 实体 + Inbox。

只读原则：只打 GET，不写库、不改状态、不触发状态机。
用法：
    python scripts/e7_decision_scan.py                # markdown 到 stdout
    MIO_HUB=http://127.0.0.1:48620 python scripts/e7_decision_scan.py
"""
import json
import os
import re
import urllib.request
from collections import Counter
from datetime import datetime, timezone

HUB = os.environ.get("MIO_HUB", "http://127.0.0.1:48620").rstrip("/")

# 噪声判定：创意引擎自动生成、洞察消费类、自测验证类——用户永远不会「决策」它们
NOISE_AGENT_TYPES = {"idea-reviewer"}
# [变更]/[溯因] 为创意引擎 idea→task 产物；__verify_ 为自测任务；想法评审为 review 噪声
NOISE_TITLE_PAT = re.compile(r"想法评审|^\[insight\]|^\[变更\]|^\[溯因\]|^__verify_")

# 「等外部输入」标题启发式（需人工二次确认，仅作候选）
EXTERNAL_PAT = re.compile(r"客户|上游|第三方|接口|资料|对接|联调|确认|审批|权限申请|数据源")


def _get(path):
    with urllib.request.urlopen(HUB + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _age_days(ts: str) -> int:
    """ISO 时间串 → 距今整天数；解析失败返回 -1（未知，不参与排序）。"""
    if not ts:
        return -1
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except (ValueError, TypeError):
        return -1


def is_noise(t: dict) -> bool:
    if (t.get("target_agent_type") or "") in NOISE_AGENT_TYPES:
        return True
    return bool(NOISE_TITLE_PAT.search(t.get("title") or ""))


def main():
    status = _get("/api/v1/tasks/status")
    tasks = _get("/api/v1/tasks?limit=5000")
    by_id = {t["id"]: t for t in tasks}

    # A 段（依赖阻塞）任务 id：D/E 段去重，避免同一件事出现三次
    alert_ids = set()
    for a in (status.get("alerts") or []):
        mm = re.search(r"（([0-9a-f]{8})）", a.get("message") or "")
        if mm:
            alert_ids.add(mm.group(1))

    noise = [t for t in tasks if is_noise(t)]
    biz = [t for t in tasks if not is_noise(t)]

    alive_states = {"queued", "running", "review", "blocked"}
    biz_alive = [t for t in biz if t["state"] in alive_states]
    biz_failed = [t for t in biz if t["state"] == "failed"]
    # 2026-11-04 修正：待验收 = stage=review 且 state=completed（Run 完成等人工采纳）。
    # 首版漏了这批——把 completed 全当历史，结果 51 条真·待验收积压被藏住。
    biz_review = [t for t in biz
                  if t["stage"] == "review" and t["state"] in ("completed", "queued", "running")]
    biz_todo = [t for t in biz_alive if t["stage"] in ("ready", "brainstorming", "planning", "design")]

    out = []
    w = out.append

    w("# E7 决策清单（实验产物 · 只读扫描）")
    w("")
    w(f"> 生成：{datetime.now().strftime('%Y-%m-%d %H:%M')} · 数据源 `{HUB}` · 脚本 `scripts/e7_decision_scan.py`")
    w("> 说明：这是「如果有一个待我决策的视图，里面该有什么」的手工版；**不改任何任务状态**。")
    w("")

    # ── 总览：先回答「为什么你觉得割裂」 ─────────────────────────────
    w("## 0. 总览：看板里到底有什么")
    w("")
    w("| 分类 | 数量 | 说明 |")
    w("|---|---|---|")
    w(f"| 全部任务 | {len(tasks)} | — |")
    w(f"| **机器噪声** | **{len(noise)}** | `idea-reviewer` 创意引擎 / `[insight]` 消费类，用户不会决策 |")
    w(f"| 真业务任务 | {len(biz)} | 人工创建或有 workspace/project |")
    w(f"| ├ 进行中（含待验收） | {len(biz_alive)} | {dict(Counter(t['stage'] for t in biz_alive))} |")
    w(f"| ├ 失败 | {len(biz_failed)} | 需决定「重试 or 放弃」 |")
    w(f"| └ 已完成/已取消 | {len(biz) - len(biz_alive) - len(biz_failed)} | 历史 |")
    w("")
    w(f"噪声占比 **{len(noise)/max(len(tasks),1):.0%}** —— 也就是说，你打开看板时，"
      f"9 成以上的卡片与你的决策无关。")
    w("")

    # ── A. 待你拍板：依赖阻塞（系统已识别但只躺在 status.alerts 里）──
    alerts = status.get("alerts") or []
    w("## A. 待你拍板（现在就能定，决定后任务才能动）")
    w("")
    if not alerts:
        w("- 无")
    for a in alerts:
        m = a.get("message") or ""
        mm = re.search(r"（([0-9a-f]{8})）", m)
        tid = mm.group(1) if mm else ""
        t = by_id.get(tid) or {}
        w(f"- **[{tid}] {t.get('title', m)[:60]}**")
        w(f"  - 现状：{m}")
        w("  - 你要决定：重开前置任务 / 换一个前置 / 砍掉这个后置 —— 三选一")
        if t:
            w(f"  - 已停 {_age_days(t.get('created_at'))} 天，stage={t.get('stage')}、state={t.get('state')}")
    w("")

    # ── B. 待你验收 ────────────────────────────────────────────────
    w(f"## B. 待你验收（{len(biz_review)} 项，Agent 说做完了，等你点头）")
    w("")
    if biz_review:
        rows = sorted(biz_review, key=lambda t: -_age_days(t.get("created_at")))[:12]
        w("| 任务 | 停留 | 你要做什么 |")
        w("|---|---|---|")
        for t in rows:
            w(f"| [{t['id']}] {t['title'][:44]} | {_age_days(t.get('created_at'))} 天 | 看 diff（`GET /tasks/{t['id']}/diff`）→ 采纳 / 退回 |")
        if len(biz_review) > len(rows):
            w(f"| …另 {len(biz_review)-len(rows)} 项 | | |")
    else:
        w("- 无")
    w("")

    # ── C. 待你决定重试 or 放弃 ─────────────────────────────────────
    w(f"## C. 待你决定「重试 or 放弃」（{len(biz_failed)} 项失败业务任务）")
    w("")
    recent_failed = sorted(biz_failed, key=lambda t: t.get("created_at") or "", reverse=True)[:12]
    if recent_failed:
        w("| 任务 | 失败于 | 你要做什么 |")
        w("|---|---|---|")
        for t in recent_failed:
            w(f"| [{t['id']}] {t['title'][:44]} | {_age_days(t.get('created_at'))} 天前 | 重试（`POST /tasks/{t['id']}/retry`）/ 拆小重做 / 关掉 |")
        if len(biz_failed) > len(recent_failed):
            w(f"| …另 {len(biz_failed)-len(recent_failed)} 项 | | |")
    else:
        w("- 无")
    w("")

    # ── D. 待启动（要不要排进日程）────────────────────────────────
    # 排除 ready：那类已在等 agent 接单，由调度器自动推进，不需要人安排
    biz_todo = [t for t in biz_todo if t["id"] not in alert_ids and t["stage"] != "ready"]
    w(f"## D. 待启动（{len(biz_todo)} 项，需你决定「什么时候做 / 还做不做」）")
    w("")
    if biz_todo:
        w("| 任务 | stage | 停留 | 你要做什么 |")
        w("|---|---|---|---|")
        for t in sorted(biz_todo, key=lambda x: -_age_days(x.get("created_at")))[:12]:
            w(f"| [{t['id']}] {t['title'][:40]} | {t['stage']} | {_age_days(t.get('created_at'))} 天 | 排期 / 拆解 / 取消 |")
    else:
        w("- 无")
    w("")

    # ── E. 待你补外部输入（标题启发式，需人工确认）────────────────
    ext = [t for t in biz_alive + biz_failed if EXTERNAL_PAT.search(t.get("title") or "")]
    w(f"## E. 可能要你去找人要东西（{len(ext)} 条候选，启发式匹配，需你自己确认）")
    w("")
    if ext:
        w("| 任务 | 状态 | 可能缺什么 |")
        w("|---|---|---|")
        for t in ext[:10]:
            w(f"| [{t['id']}] {t['title'][:46]} | {t['state']} | 外部接口/资料/对接确认 |")
    else:
        w("- 无")
    w("")

    # ── F. 建议清理 ───────────────────────────────────────────────
    stale_review = [t for t in noise if t["stage"] == "review"]
    w("## F. 建议清理（不清理，上面五张表永远看不清）")
    w("")
    w("| 对象 | 数量 | 建议动作 |")
    w("|---|---|---|")
    w(f"| `idea-reviewer` 噪声任务 | {len(noise)} | 批量 cancel / 默认在看板隐藏 |")
    w(f"| 其中已停在 review 的 | {len(stale_review)} | 全部 cancel，无业务价值 |")
    w(f"| 无 workspace 的失败任务 | {sum(1 for t in tasks if t['state']=='failed' and not (t.get('workspace') or '').strip())} | 归档，不占看板 |")
    w("")

    w("---")
    w("")
    w("## 这张清单怎么用")
    w("")
    w("1. 只看 A/B/C 三段——那是**真正卡住、且只有你能解**的东西，通常 ≤ 10 项。")
    w("2. D/E 是「该你安排」，不是紧急；F 是卫生问题，一次清完管很久。")
    w("3. 如果你看完觉得「A/B/C 有用」→ Decision 实体 + Inbox 值得做；")
    w("   如果觉得「我还得自己去翻」→ 说明瓶颈不在视图，而在任务创建时就没写清「谁等谁」。")

    print("\n".join(out))


if __name__ == "__main__":
    main()
