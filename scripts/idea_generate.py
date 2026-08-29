#!/usr/bin/env python3
"""Generate ideas via mio-intelligence MCP and sync to mio-taskhub."""
import json, os, subprocess, sys, httpx

NODE = r"C:\Users\admin\.workbuddy\binaries\node\versions\22.22.2\node.exe"
MCP_SCRIPT = r"D:\node_global\node_modules\mio-agent-runtime\server\mio-intelligence-mcp\index.js"
DATA_DIR = os.path.join(os.path.expanduser("~"), ".mio-intelligence")
HUB_URL = os.environ.get("MIO_TASKHUB_URL", "http://127.0.0.1:48620/api/v1")

ENV = {
    **os.environ,
    "MIO_DATA_DIR": DATA_DIR,
    "MIO_CONTEXT": json.dumps({
        "agentId": "opencode",
        "project": "2026-08-22-12-13-49",
        "workspace": r"c:\Users\admin\WorkBuddy\2026-08-22-12-13-49",
        "sessionId": "opencode-session",
    }),
}


def call_mcp_tool(tool_name: str, arguments: dict) -> dict:
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    proc = subprocess.run(
        [NODE, MCP_SCRIPT],
        input=json.dumps(request) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=ENV,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"MCP error: {proc.stderr[:500]}")
    lines = proc.stdout.strip().split("\n")
    resp = json.loads(lines[0])
    text = resp["result"]["content"][0]["text"]
    return json.loads(text, strict=False)


def generate_ideas(goal: str, context: str = "", num_ideas: int = 3) -> list[dict]:
    result = call_mcp_tool("mio.idea.generate", {
        "goal": goal,
        "context": context,
        "constraints": ["不引入外部依赖", "保持本机单用户", "复用现有 MCP 工具"],
        "numIdeas": num_ideas,
    })
    return result.get("ideas", [])


def sync_to_taskhub(ideas: list[dict]) -> int:
    client = httpx.Client(timeout=10)
    try:
        client.get(f"{HUB_URL}/tasks")  # connectivity check
    except Exception as e:
        print(f"[ERROR] Cannot connect to taskhub: {e}")
        return 0

    # Get existing for dedup
    try:
        existing = client.get(f"{HUB_URL}/ideas").json().get("ideas", [])
        existing_titles = {i["title"].strip().lower() for i in existing}
    except Exception:
        existing_titles = set()

    created = 0
    for idea in ideas:
        title = idea.get("title", "").strip()
        if title.lower() in existing_titles:
            print(f"  [SKIP] {title[:60]}")
            continue
        strategy = idea.get("provenance", {}).get("strategy", "")
        body = {
            "title": title[:200],
            "description": idea.get("description", ""),
            "project": "",
            "labels": ["mio-intelligence", "auto-generated"] + ([f"strategy:{strategy}"] if strategy else []),
        }
        r = client.post(f"{HUB_URL}/ideas", json=body)
        if r.status_code == 200:
            print(f"  [OK] [{strategy}] {title[:60]} → id={r.json()['id']}")
            created += 1
        else:
            print(f"  [FAIL] {title[:60]}: {r.text[:100]}")
    client.close()
    return created


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("goal", nargs="?", default="如何让 mio-taskhub 和 mio-intelligence 的想法系统双向联通")
    p.add_argument("--context", default="两者目前单向连通，需要双向")
    p.add_argument("--num", type=int, default=3)
    p.add_argument("--no-sync", action="store_true")
    args = p.parse_args()

    print(f"Generating {args.num} ideas for: {args.goal[:50]}...")
    ideas = generate_ideas(args.goal, args.context, args.num)
    print(f"Generated {len(ideas)} ideas\n")

    for i, idea in enumerate(ideas, 1):
        s = idea["provenance"]["strategy"]
        print(f"[{s}] {idea['title'][:80]}")

    if not args.no_sync:
        print(f"\nSyncing to taskhub...")
        n = sync_to_taskhub(ideas)
        print(f"Synced {n} ideas to taskhub")

    # Save to ideas.jsonl
    ideas_path = os.path.join(DATA_DIR, "ideas.jsonl")
    with open(ideas_path, "a", encoding="utf-8") as f:
        for idea in ideas:
            f.write(json.dumps(idea, ensure_ascii=False) + "\n")
    print(f"Saved to {ideas_path}")


if __name__ == "__main__":
    main()
