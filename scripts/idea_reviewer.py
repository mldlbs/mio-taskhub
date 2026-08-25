# scripts/idea_reviewer.py
"""常驻 idea reviewer agent：注册 → 周期心跳 → 领取评审任务 → 规则化评审 → 提交结论。

评审规则（阈值可用环境变量覆盖）：
- 描述完整度：description 非空白字符数 >= DESC_MIN_CHARS
- 讨论活跃度：所有讨论的消息总数 >= DISCUSS_MIN
- 存活时长：now - created_at >= MIN_AGE_SEC
- 重复检测：与其它非终态 idea 的标题 token 相似度 >= DUP_SIM 视为重复

推荐逻辑（hub 只推进当前状态下一档，脚本按当前状态保守给建议）：
- 重复               → archive
- 描述完整+讨论活跃   → 推进一档（new→ferment / fermenting→form）
- 其余               → nothing
推进越级/参数错误时降级 nothing 重试一次。

用法：python scripts/idea_reviewer.py          （前台，Ctrl+C 退出）
      MIO_TASKHUB_TOKEN=xxx python ...          （带鉴权）
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HUB = os.environ.get("MIO_TASKHUB_HUB", "http://127.0.0.1:48620/api/v1")
AGENT = os.environ.get("IDEA_REVIEWER_NAME", "idea-reviewer-bot")
POLL_SEC = int(os.environ.get("IDEA_REVIEWER_POLL_SEC", "60"))
HEARTBEAT_SEC = int(os.environ.get("IDEA_REVIEWER_HEARTBEAT_SEC", "60"))

DESC_MIN_CHARS = int(os.environ.get("IDEA_REVIEWER_DESC_MIN", "40"))
DISCUSS_MIN = int(os.environ.get("IDEA_REVIEWER_DISCUSS_MIN", "1"))
MIN_AGE_SEC = int(os.environ.get("IDEA_REVIEWER_MIN_AGE_SEC", str(60 * 60 * 24)))
DUP_SIM = float(os.environ.get("IDEA_REVIEWER_DUP_SIM", "0.6"))


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def req(method, path, body=None, params=None):
    url = HUB + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    tok = os.environ.get("MIO_TASKHUB_TOKEN")
    if tok:
        r.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        err = {}
        try:
            err = json.loads(e.read().decode())
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {err}")


def register():
    out = req("POST", "/agents/register", {"name": AGENT, "agent_type": "idea-reviewer"})
    log(f"registered: {out.get('status')}")


def agent_heartbeat():
    req("POST", "/agents/heartbeat", {"name": AGENT})


def claim():
    try:
        out = req("POST", "/tasks/claim",
                  params={"agent": AGENT, "agent_type": "idea-reviewer"})
    except RuntimeError as e:
        if "204" in str(e):
            return None
        raise
    if not out or "id" not in out:
        return None
    return out


def get_task(task_id):
    return req("GET", f"/tasks/{task_id}")


def get_idea(idea_id):
    return req("GET", f"/ideas/{idea_id}")


def list_ideas():
    out = req("GET", "/ideas")
    return out.get("ideas", [])


def submit_review(idea_id, recommend, reasoning):
    return req("POST", f"/ideas/{idea_id}/review",
               {"recommend": recommend, "reasoning": reasoning, "actor": "agent"})


def submit_result(run_id, success, message):
    return req("POST", f"/runs/{run_id}/result",
               {"success": success, "result": message})


_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")


def _tokens(text):
    return set(_TOKEN_RE.findall((text or "").lower()))


def _title_sim(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(len(ta), len(tb))


_FINAL = {"archived", "cancelled", "broken_down"}


def evaluate(idea, all_ideas):
    """返回 (recommend, reasons)。reasons 为逐项判定说明列表。"""
    desc = (idea.get("description") or "").strip()
    desc_ok = len(desc) >= DESC_MIN_CHARS

    discussions = idea.get("discussions", [])
    msg_count = sum(len(d.get("messages", [])) for d in discussions)
    discuss_ok = msg_count >= DISCUSS_MIN

    created = idea.get("created_at")
    age_ok = False
    try:
        created_dt = datetime.fromisoformat(created)
        if created_dt.tzinfo is None:
            created_dt = created_dt.replace(tzinfo=timezone.utc)
        age_ok = (datetime.now(timezone.utc) - created_dt).total_seconds() >= MIN_AGE_SEC
    except Exception:
        pass

    title = idea.get("title") or ""
    dup = None
    for other in all_ideas:
        if other.get("id") == idea.get("id"):
            continue
        if other.get("status") in _FINAL:
            continue
        if _title_sim(title, other.get("title") or "") >= DUP_SIM:
            dup = other
            break

    reasons = [
        f"描述完整{'[+]' if desc_ok else '[-]'}({len(desc)}字)",
        f"讨论活跃{'[+]' if discuss_ok else '[-]'}({msg_count}条)",
        f"存活足够{'[+]' if age_ok else '[-]'}",
    ]
    if dup:
        reasons.append(f"与「{dup['title']}」重复")
        return "archive", reasons

    status = idea.get("status")
    if desc_ok and discuss_ok:
        if status == "new":
            return "ferment", reasons
        if status == "fermenting":
            return "form", reasons
    return "nothing", reasons


def process(run):
    run_id = run["id"]
    task_id = run["task_id"]
    try:
        task = get_task(task_id)
    except Exception as e:
        log(f"run={run_id} get_task failed: {e}")
        submit_result(run_id, False, f"获取任务详情失败: {e}")
        return

    idea_id = task.get("idea_id")
    if not idea_id:
        submit_result(run_id, True, "任务未关联 idea，无需评审")
        return

    try:
        idea = get_idea(idea_id)
        all_ideas = list_ideas()
    except Exception as e:
        log(f"run={run_id} idea={idea_id} fetch failed: {e}")
        submit_result(run_id, False, f"获取想法失败: {e}")
        return

    recommend, reasons = evaluate(idea, all_ideas)
    reasoning = "；".join(reasons)
    log(f"run={run_id} idea={idea_id}「{idea.get('title')}」→ recommend={recommend} ({reasoning})")

    try:
        submit_review(idea_id, recommend, reasoning)
    except RuntimeError as e:
        # 越级/参数错误：降级 nothing 重试一次，避免评审任务卡死
        if recommend != "nothing":
            log(f"  submit_review({recommend}) failed: {e} → 降级 nothing")
            try:
                submit_review(idea_id, "nothing", reasoning + "；越级目标被拒，本次不推进")
            except RuntimeError as e2:
                log(f"  submit_review(nothing) also failed: {e2}")
                submit_result(run_id, False, f"提交评审失败: {e2}")
                return
        else:
            log(f"  submit_review failed: {e}")
            submit_result(run_id, False, f"提交评审失败: {e}")
            return

    submit_result(run_id, True, f"评审完成：recommend={recommend}，{reasoning}")


def main():
    register()
    last_heartbeat = 0.0
    while True:
        now = time.time()
        try:
            if now - last_heartbeat >= HEARTBEAT_SEC:
                agent_heartbeat()
                last_heartbeat = now
            run = claim()
            if run:
                log(f"claimed run={run['id']} task={run['task_id']}")
                process(run)
                continue
        except Exception as e:
            log(f"loop error: {e}")
            time.sleep(POLL_SEC)
            continue
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("stopped")
        sys.exit(0)