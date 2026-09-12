# Seed test data with proper UTF-8 encoding
import json, urllib.request

HUB = "http://127.0.0.1:48620/api/v1"

def req(method, path, body=None):
    url = HUB + path
    data = json.dumps(body).encode("utf-8") if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            if resp.status == 204: return None
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 204: return None
        print(f"Error {e.code}: {e.read().decode()[:200]}")
        return None

# Register agents
for name in ["codex", "claude-code", "hermes", "workbuddy", "opencode"]:
    r = req("POST", "/agents/register", {"name": name, "agent_type": "cli"})
    print(f"Registered: {r['name']}")

# Create tasks with Chinese titles
tasks = [
    {"title": "数据清洗脚本", "description": "清洗用户行为日志数据", "priority": 2, "est_duration_min": 60},
    {"title": "训练推荐模型", "description": "基于ALS的协同过滤算法", "priority": 3, "est_duration_min": 240},
    {"title": "生成项目周报", "description": "汇总本周开发进度和成果", "priority": 1, "est_duration_min": 30},
    {"title": "修复登录Bug", "description": "用户反馈无法正常登录系统", "priority": 3, "est_duration_min": 45},
    {"title": "API性能优化", "description": "减少核心接口响应时间", "priority": 1, "est_duration_min": 90},
    {"title": "数据库备份", "description": "全量备份加增量备份验证", "priority": 0, "est_duration_min": 20},
]

created = []
for t in tasks:
    r = req("POST", "/tasks", t)
    if r:
        created.append(r)
        print(f"Created: {r['id']} {t['title']}")

# Claim some tasks
for agent in ["codex", "hermes", "opencode"]:
    r = req("POST", f"/tasks/claim?agent={agent}")
    if r and "id" in r:
        print(f"{agent} claimed: {r['task_id']}")

print(f"\nDone! {len(created)} tasks created.")
