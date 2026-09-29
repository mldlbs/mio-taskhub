# -*- coding: utf-8 -*-
"""mio-taskhub 空闲执行 worker：领取任务 → 执行 → 回写结果（供「空闲计划」到点拉起）。

用法：
  python idle_worker.py <agent_name> [--project P1,P2] [--cli "opencode run {prompt}"]
                        [--once] [--max N] [--interval 20] [--idle-timeout 600] [--dry-run]

行为：
  1) 注册/心跳（agents/register）；
  2) 循环 claim（可带 --project 限定项目范围；与空闲计划的「项目范围」对应）；
  3) 有任务 → 拼 prompt → 按 --cli 模板执行（占位符 {prompt}/{title}/{task_id}），执行期间每 60s 心跳；
  4) 结果回写（exit 0 = success）；
  5) 无任务 → --once 直接退出；否则按 --interval 轮询，--idle-timeout 到点退出（避免空转）。

不传 --cli 时只领取并报告（用于连通性自检）；--dry-run 只打印 prompt，不执行、不回写。
"""
import argparse
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HUB = os.environ.get("MIO_TASKHUB_URL") or "http://127.0.0.1:48620/api/v1"


def req(method, path, body=None, timeout=20):
    url = HUB.rstrip("/") + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    tok = os.environ.get("MIO_TASKHUB_TOKEN")
    if tok:
        r.add_header("Authorization", "Bearer %s" % tok)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            if resp.status == 204:
                return None
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return None
        return {"__error": e.code, "body": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:  # noqa: BLE001 —— hub 不可达按无任务处理
        return {"__error": "conn", "body": str(e)}


def build_prompt(task: dict) -> str:
    """把任务拼成给 agent CLI 的提示词。"""
    lines = ["【taskhub 任务】%s" % (task.get("title") or ""),
             "任务 id：%s" % (task.get("id") or "")]
    if task.get("description"):
        lines += ["", "描述：", str(task["description"])]
    if task.get("acceptance_criteria"):
        lines += ["", "验收标准：", str(task["acceptance_criteria"])]
    lines += ["", "要求：完成任务并写清结果（改了哪些文件、怎么验证、结论）。"]
    return "\n".join(lines)


def claim_once(agent: str, project: str):
    path = "/tasks/claim?agent=%s" % urllib.parse.quote(agent)
    if project:
        path += "&project=" + urllib.parse.quote(project)
    return req("POST", path)


def main() -> int:
    ap = argparse.ArgumentParser(description="mio-taskhub idle worker")
    ap.add_argument("agent", help="agent 名称（需与空闲计划配置一致）")
    ap.add_argument("--project", default="", help="只领取这些项目（逗号分隔；空=全部）")
    ap.add_argument("--cli", default="", help="执行模板，如: opencode run {prompt}（占位符 {prompt}/{title}/{task_id}）")
    ap.add_argument("--once", action="store_true", help="领到并执行完一个任务即退出；无任务也退出")
    ap.add_argument("--max", type=int, default=0, help="最多执行 N 个任务（0=不限）")
    ap.add_argument("--interval", type=float, default=20.0, help="无任务时的轮询间隔秒")
    ap.add_argument("--idle-timeout", type=float, default=0.0, help="连续无任务超过 N 秒退出（0=不限）")
    ap.add_argument("--dry-run", action="store_true", help="只领取并打印 prompt，不执行/不回写")
    args = ap.parse_args()

    print("[idle-worker] agent=%s project=%s cli=%s" %
          (args.agent, args.project or "(全部)", args.cli or "(仅领取)"))
    req("POST", "/agents/register", {"name": args.agent, "agent_type": "cli"})

    done = 0
    idle_since = None
    while True:
        res = claim_once(args.agent, args.project)
        if not res or res.get("__error") or "id" not in res:
            if args.once:
                print("[idle-worker] 无任务，退出")
                return 0
            if idle_since is None:
                idle_since = time.time()
            if args.idle_timeout and (time.time() - idle_since) > args.idle_timeout:
                print("[idle-worker] 空闲超时，退出")
                return 0
            time.sleep(args.interval)
            continue

        idle_since = None
        run_id = res["id"]
        task_id = res.get("task_id")
        task = res.get("task") or {}
        print("[idle-worker] claimed task=%s run=%s" % (task_id, run_id))

        prompt = build_prompt(task)
        ok, msg = True, "completed"
        if args.dry_run:
            print("----- prompt -----")
            print(prompt)
            print("------------------")
            ok, msg = True, "dry-run（未执行、未回写）"
        elif args.cli:
            cmd = (args.cli.replace("{prompt}", prompt)
                           .replace("{title}", str(task.get("title") or ""))
                           .replace("{task_id}", str(task_id or "")))
            stop_hb = threading.Event()

            def _hb():
                while not stop_hb.wait(60):
                    req("POST", "/runs/%s/heartbeat" % run_id, {"progress": 50})

            threading.Thread(target=_hb, daemon=True).start()
            print("[idle-worker] exec: %s" % cmd[:200])
            try:
                proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace")
                ok = proc.returncode == 0
                out = ((proc.stdout or "") + (proc.stderr or "")).strip()
                msg = out[-800:] if out else ("exit %s" % proc.returncode)
            except Exception as e:  # noqa: BLE001
                ok, msg = False, "worker exec failed: %s" % e
            finally:
                stop_hb.set()
        else:
            msg = "claimed by idle worker（未配置 --cli，仅领取）"

        req("POST", "/runs/%s/result" % run_id, {"success": bool(ok), "result": str(msg)[:2000]})
        print("[idle-worker] submitted run=%s success=%s" % (run_id, ok))
        done += 1
        if args.once or (args.max and done >= args.max):
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
