# -*- coding: utf-8 -*-
"""检查 GitHub 分支保护是否强制要求指定 status checks。

需要 token（GH_TOKEN / GITHUB_TOKEN），且对仓库有 Administration 读权限。
用法：
    python scripts/check_required_checks.py --repo owner/repo --branch main \
        --require test --require "Materials gate tests"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Iterable, List, Optional, Set


def _token() -> Optional[str]:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def fetch_required(token: str, repo: str, branch: str) -> Set[str]:
    url = (f"https://api.github.com/repos/{repo}/branches/{branch}/"
           f"protection/required_status_checks")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "mio-taskhub-gate-audit",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    contexts = set(data.get("contexts") or [])
    for check in data.get("checks") or []:
        ctx = check.get("context")
        if ctx:
            contexts.add(ctx)
    return contexts


def audit(token: Optional[str], repo: str, branch: str,
          required: Iterable[str]) -> dict:
    required_set = {r for r in required if r}
    if not token:
        return {"ok": False, "error": "token_missing",
                "required": sorted(required_set)}
    try:
        actual = fetch_required(token, repo, branch)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"http_{exc.code}", "repo": repo,
                "branch": branch, "required": sorted(required_set)}
    except Exception as exc:
        return {"ok": False, "error": f"request_failed:{exc}",
                "required": sorted(required_set)}
    missing = sorted(required_set - actual)
    return {
        "ok": not missing,
        "repo": repo,
        "branch": branch,
        "required": sorted(required_set),
        "actual": sorted(actual),
        "missing": missing,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--branch", default="main")
    p.add_argument("--require", action="append", default=[])
    args = p.parse_args()
    result = audit(_token(), args.repo, args.branch, args.require)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
