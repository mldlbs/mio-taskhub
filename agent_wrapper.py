# mio-taskhub agent wrapper
# Usage: python agent_wrapper.py <agent_name> <action> [args...]
# Actions: register | claim | heartbeat | result

import sys, json, time, urllib.request, urllib.error

HUB = "http://127.0.0.1:8080/api/v1"

def req(method, path, body=None):
    url = HUB + path
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            if resp.status == 204: return None
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 204: return None
        return {"error": e.code, "body": e.read().decode()[:200]}

def main():
    if len(sys.argv) < 3:
        print("Usage: python agent_wrapper.py <agent_name> <action> [args...]")
        print("Actions: register | claim | heartbeat <run_id> <progress> | result <run_id> <success> <msg>")
        sys.exit(1)

    agent = sys.argv[1]
    action = sys.argv[2]

    if action == "register":
        r = req("POST", "/agents/register", {"name": agent, "agent_type": "cli"})
        print(f"Registered: {r['name']} status={r['status']}")

    elif action == "claim":
        r = req("POST", f"/tasks/claim?agent={agent}")
        if r and "id" in r:
            print(f"Claimed task: {r['task_id']} (run={r['id']}, attempt={r.get('attempt',1)})")
            print(f"  → Execute this task, then send heartbeats and result")
        else:
            print("No tasks available (204)")

    elif action == "heartbeat":
        run_id, progress = sys.argv[3], int(sys.argv[4] or 50)
        r = req("POST", f"/runs/{run_id}/heartbeat", {"progress": progress})
        print(f"Heartbeat: progress={r['progress']}% state={r['state']}")

    elif action == "result":
        run_id = sys.argv[3]
        success = sys.argv[4].lower() in ("true", "1", "yes", "success")
        msg = sys.argv[5] if len(sys.argv) > 5 else ("done" if success else "failed")
        r = req("POST", f"/runs/{run_id}/result", {"success": success, "result": msg})
        print(f"Result submitted: state={r['state']} result={r['result']}")

    elif action == "list":
        r = req("GET", "/tasks")
        for t in r:
            print(f"  [{t['state']:10s}] P{t['priority']} {t['title']}")

    else:
        print(f"Unknown action: {action}")

if __name__ == "__main__":
    main()
