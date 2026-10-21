# -*- coding: utf-8 -*-
"""把已写好的 P1-2 文档登记到 taskhub（doc_paths + doc_statuses）。

背景：`taskhub_write_document` 是「写文件 + 登记」二合一，直接用它会覆盖磁盘上
已经写好的正文（本任务文档已用 write_file 写好并经过质量章节校验）。
REST `PUT /api/v1/tasks/{id}/doc?kind=` 同理会覆盖。

本脚本改为：直接把磁盘文件内容读出来 → 通过 REST PUT 回写（内容一致，等价于
幂等登记）→ 触发质量校验 → 打印质量分与 errors。

用法：
    .venv/Scripts/python.exe scripts/register_task_docs.py a1aba9ef
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:48620/api/v1"

KINDS = ("requirement", "spec", "api", "test", "plan")


def _req(method: str, url: str, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def main(task_id: str) -> int:
    root = Path(__file__).resolve().parent.parent
    doc_dir = root / "docs" / "taskhub" / task_id
    rc = 0
    for kind in KINDS:
        path = doc_dir / f"{kind}.md"
        if not path.is_file():
            print(f"  [skip] {kind}: 文件不存在 {path}")
            continue
        content = path.read_text(encoding="utf-8")
        rel = f"docs/taskhub/{task_id}/{kind}.md"
        status, body = _req(
            "PUT",
            f"{BASE}/tasks/{task_id}/doc?kind={kind}",
            {"content": content, "path": rel, "mode": "overwrite"},
        )
        if status != 200:
            print(f"  [FAIL] {kind}: HTTP {status} {str(body)[:200]}")
            rc = 1
            continue
        q = body.get("quality") or {}
        errs = q.get("errors") or []
        warns = q.get("warns") or []
        flag = "OK  " if not errs else "ERR "
        print(f"  [{flag}] {kind}: score={q.get('score')} errors={len(errs)} warns={len(warns)} "
              f"path={body.get('path')}")
        for e in errs:
            print(f"         - {e}")
    return rc


if __name__ == "__main__":
    tid = sys.argv[1] if len(sys.argv) > 1 else "a1aba9ef"
    sys.exit(main(tid))
