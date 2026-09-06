"""Enhanced idea generation with templates and improved prompts."""
import json
import os
import subprocess
import sys
from typing import List, Dict, Any
import httpx

from mio_taskhub.idea_templates import (
    DEFAULT_TEMPLATES,
    IdeaTemplate,
    get_template_by_id,
    render_template_prompt,
    template_to_api_payload,
)


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
    """Call mio-intelligence MCP tool."""
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


def generate_ideas_with_template(
    template: IdeaTemplate,
    values: dict,
    num_ideas: int = 3,
) -> List[dict]:
    """Generate ideas using a template with provided values."""
    # Build enhanced context from template values
    context_parts = []
    for field in template.fields:
        key = field["key"]
        if key in values and values[key]:
            context_parts.append(f"{field['label']}：{values[key]}")
    
    context = "\n\n".join(context_parts)
    goal = values.get("title", template.name)
    
    # Generate with enhanced context
    result = call_mcp_tool("mio.idea.generate", {
        "goal": goal,
        "context": context,
        "constraints": ["不引入外部依赖", "保持本机单用户", "复用现有 MCP 工具"],
        "numIdeas": num_ideas,
    })
    return result.get("ideas", [])


def generate_ideas_freeform(
    goal: str,
    context: str = "",
    num_ideas: int = 3,
) -> List[dict]:
    """Generate ideas without a template (freeform)."""
    result = call_mcp_tool("mio.idea.generate", {
        "goal": goal,
        "context": context,
        "constraints": ["不引入外部依赖", "保持本机单用户", "复用现有 MCP 工具"],
        "numIdeas": num_ideas,
    })
    return result.get("ideas", [])


def sync_to_taskhub(ideas: List[dict], template: str = "") -> int:
    """Sync ideas to mio-taskhub."""
    client = httpx.Client(timeout=10)
    try:
        client.get(f"{HUB_URL}/tasks")
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
        labels = ["mio-intelligence", "auto-generated"]
        if template:
            labels.append(f"template:{template}")
        if strategy:
            labels.append(f"strategy:{strategy}")
        
        body = {
            "title": title[:200],
            "description": idea.get("description", ""),
            "project": "",
            "labels": labels,
        }
        r = client.post(f"{HUB_URL}/ideas", json=body)
        if r.status_code == 200:
            print(f"  [OK] {title[:60]} → id={r.json()['id']}")
            created += 1
        else:
            print(f"  [FAIL] {title[:60]}: {r.text[:100]}")
    
    client.close()
    return created


def list_templates():
    """Print available templates."""
    print("Available templates:")
    for t in DEFAULT_TEMPLATES:
        print(f"  {t.icon} {t.id:20s} {t.name} ({t.category})")
        print(f"      {t.description}")
        for f in t.fields:
            req = "*" if f.get("required") else ""
            opts = f" [{', '.join(f['options'])}]" if f.get("options") else ""
            print(f"    - {f['label']:12s} ({f['key']}{req}){opts}")
        print()


def interactive_generate():
    """Interactive template-based idea generation."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Generate ideas with templates")
    parser.add_argument("--template", "-t", help="Template ID to use")
    parser.add_argument("--list", "-l", action="store_true", help="List available templates")
    parser.add_argument("--num", "-n", type=int, default=3, help="Number of ideas to generate")
    parser.add_argument("--no-sync", action="store_true", help="Don't sync to taskhub")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    args = parser.parse_args()
    
    if args.list:
        list_templates()
        return
    
    if args.interactive or args.template:
        # Interactive template selection
        if args.template:
            template = get_template_by_id(args.template)
            if not template:
                print(f"Template not found: {args.template}")
                return
        else:
            list_templates()
            choice = input("\n选择模板 (输入 ID): ").strip()
            template = get_template_by_id(choice)
            if not template:
                print("Invalid template ID")
                return
        
        print(f"\n使用模板: {template.icon} {template.name}")
        print("请填写字段 (留空跳过非必填):\n")
        
        values = {}
        for field in template.fields:
            prompt = f"{field['label']}"
            if field.get("required"):
                prompt += " *"
            if field.get("options"):
                prompt += f" [{', '.join(field['options'])}]"
            prompt += ": "
            
            value = input(prompt).strip()
            if field.get("required") and not value:
                print(f"  必填字段不能为空: {field['label']}")
                return
            if value:
                values[key] = value
        
        # Generate ideas
        print(f"\n正在生成 {args.num} 个想法...")
        ideas = generate_ideas_with_template(template, values, args.num)
        print(f"生成了 {len(ideas)} 个想法\n")
        
        for i, idea in enumerate(ideas, 1):
            s = idea["provenance"]["strategy"]
            print(f"[{s}] {idea['title'][:80]}")
        
        if not args.no_sync:
            print(f"\n正在同步到 taskhub...")
            n = sync_to_taskhub(ideas, template.id)
            print(f"已同步 {n} 个想法")
        
        # Save to local file
        ideas_path = os.path.join(DATA_DIR, "ideas.jsonl")
        with open(ideas_path, "a", encoding="utf-8") as f:
            for idea in ideas:
                f.write(json.dumps(idea, ensure_ascii=False) + "\n")
        print(f"已保存到 {ideas_path}")
    else:
        # Freeform generation
        import sys
        goal = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "如何让 mio-taskhub 更好用"
        context = ""
        
        print(f"Generating {args.num} ideas for: {goal[:50]}...")
        ideas = generate_ideas_freeform(goal, context, args.num)
        print(f"Generated {len(ideas)} ideas\n")
        
        for i, idea in enumerate(ideas, 1):
            s = idea["provenance"]["strategy"]
            print(f"[{s}] {idea['title'][:80]}")
        
        if not args.no_sync:
            print(f"\nSyncing to taskhub...")
            n = sync_to_taskhub(ideas)
            print(f"Synced {n} ideas to taskhub")
        
        ideas_path = os.path.join(DATA_DIR, "ideas.jsonl")
        with open(ideas_path, "a", encoding="utf-8") as f:
            for idea in ideas:
                f.write(json.dumps(idea, ensure_ascii=False) + "\n")
        print(f"Saved to {ideas_path}")


if __name__ == "__main__":
    interactive_generate()