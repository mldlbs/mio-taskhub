# -*- coding: utf-8 -*-
"""ideas_sync.py — mio-intelligence 创意想法 → taskhub Idea 批量同步（HOWTO「方式二」）。

- 扫描 ~/.mio-intelligence/ideas.jsonl（MIO_HOME / MIO_DATA_DIR 可覆盖）
- 按 title 去重（taskhub 已存在或本批内重复即跳过）
- 调用 taskhub REST API POST /api/v1/ideas 创建 Idea（status=new）
- 同步状态写 ~/.mio-intelligence/idea_sync_state.json，按源 idea id 避免重复

用法：
  python scripts/ideas_sync.py --dry-run            # 预览待同步的想法
  python scripts/ideas_sync.py                      # 执行同步
  python scripts/ideas_sync.py --project my-project # 指定项目

Base URL 默认 http://127.0.0.1:48620，可用环境变量 MIO_TASKHUB_BASE 覆盖。
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = "http://127.0.0.1:48620"
TIMEOUT = 15.0


def _io_fix():
    """控制台统一 UTF-8（含 GBK 控制台），避免中文/emoji 打印崩溃或乱码。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def intel_home() -> Path:
    for env in ("MIO_HOME", "MIO_DATA_DIR"):
        v = (os.environ.get(env) or "").strip()
        if v:
            return Path(v)
    return Path.home() / ".mio-intelligence"


def http_json(method: str, url: str, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_source(path: Path):
    """读 ideas.jsonl，跳过坏行；同 id 重复行合并。返回 (dict{id: idea}, 坏行数, 合并行数)。"""
    ideas = {}
    bad = 0
    dup = 0
    if not path.exists():
        return ideas, bad, dup
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if not (isinstance(obj, dict) and obj.get("id")):
            bad += 1
            continue
        if obj["id"] in ideas:
            dup += 1
            continue
        ideas[obj["id"]] = obj
    return ideas, bad, dup


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def build_payload(idea: dict, project: str) -> dict:
    """HOWTO 数据映射：title/description 直映，goal 作描述前缀，labels 三件套。"""
    strategy = (idea.get("provenance") or {}).get("strategy") or idea.get("strategy") or ""
    goal = (idea.get("goal") or "").strip()
    desc = (idea.get("description") or "").strip()
    if goal:
        desc = ("Goal: " + goal + "\n\n" + desc).strip()
    labels = ["mio-intelligence", "auto-generated"]
    if strategy:
        labels.append("strategy:" + str(strategy).strip())
    body = {"title": (idea.get("title") or "").strip(), "description": desc, "labels": labels}
    if project:
        body["project"] = project
    return body


def main(argv=None) -> int:
    _io_fix()
    ap = argparse.ArgumentParser(description="同步 ~/.mio-intelligence/ideas.jsonl 到 taskhub Idea 系统")
    ap.add_argument("--dry-run", action="store_true", help="预览待同步的想法，不创建、不写状态")
    ap.add_argument("--project", default="", help="创建 Idea 的关联项目名")
    ap.add_argument("--base", default=os.environ.get("MIO_TASKHUB_BASE", DEFAULT_BASE),
                    help="taskhub base URL（默认 %(default)s）")
    args = ap.parse_args(argv)

    home = intel_home()
    src_path = home / "ideas.jsonl"
    state_path = home / "idea_sync_state.json"

    source, bad_lines, dup_lines = load_source(src_path)
    state = load_state(state_path)
    if not source:
        print(f"[ideas_sync] 源为空或不存在: {src_path}")
        return 0

    existing_titles = set()
    try:
        listing = http_json("GET", args.base.rstrip("/") + "/api/v1/ideas")
        existing_titles = {(i.get("title") or "").strip()
                           for i in listing.get("ideas", []) if isinstance(i, dict)}
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"[ideas_sync] ERROR 无法连接 taskhub {args.base}: {e}")
        return 1

    created = skipped_state = skipped_title = skipped_dup = failed = 0
    pending = []
    seen_titles = set()
    for sid, idea in source.items():
        title = (idea.get("title") or "").strip()
        if not title:
            continue
        if sid in state:
            skipped_state += 1
            continue
        if title in existing_titles or title in seen_titles:
            skipped_title += 1
            state[sid] = {"title": title, "idea_id": (state.get(sid) or {}).get("idea_id"),
                          "synced_at": int(time.time() * 1000), "action": "skipped_title"}
            continue
        seen_titles.add(title)
        pending.append((sid, idea))

    mode = "DRY-RUN" if args.dry_run else "SYNC"
    print(f"[ideas_sync] {mode} source={src_path} 唯一 {len(source)}"
          f"（坏行 {bad_lines}，重复合并 {dup_lines}，状态命中 {skipped_state}，"
          f"标题去重 {skipped_title}），待同步 {len(pending)}")

    for sid, idea in pending:
        payload = build_payload(idea, args.project)
        if args.dry_run:
            print(f"  [would-create] {payload['title'][:70]}  labels={','.join(payload['labels'])}")
            continue
        try:
            resp = http_json("POST", args.base.rstrip("/") + "/api/v1/ideas", payload)
            idea_id = resp.get("id") or ""
            if not idea_id:
                raise ValueError(f"响应缺少 id: {str(resp)[:120]}")
            state[sid] = {"title": payload["title"], "idea_id": idea_id,
                          "synced_at": int(time.time() * 1000), "action": "created"}
            created += 1
            print(f"  [created] {idea_id}  {payload['title'][:70]}")
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            failed += 1
            print(f"  [failed] {payload['title'][:70]}  -> {e}")

    if not args.dry_run:
        save_state(state_path, state)
        # 回读校验（写盘失败会在此暴露）
        if load_state(state_path).keys() != state.keys():
            print(f"[ideas_sync] ERROR 状态回读不一致: {state_path}")
            return 1

    print(f"[ideas_sync] 完成 created={created} skipped_state={skipped_state} "
          f"skipped_title={skipped_title} failed={failed}"
          + ("" if args.dry_run else f" state={state_path}"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
