# -*- coding: utf-8 -*-
"""P4 标注：规则辅助双遍 + 争议标记。

这不是人工金标准。规则版本见 calibration/RULES-20261028.md。
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Dict, Tuple

VERBS = (
    "是", "为", "必须", "禁止", "应", "需", "返回", "新增", "删除", "保留",
    "采用", "通过", "失败", "错误", "支持", "实现", "走", "落", "改", "加",
    "减", "校验", "拦截", "超时", "发布", "推出", "宣布", "加入", "泄露",
    "起诉", "禁止", "裁员", "收购", "上线", "修复", "移除", "打破", "证实",
    "发现", "建议", "要求", "禁止", "不再", "不能", "不会", "可以", "能",
    "is", "are", "was", "were", "has", "have", "can", "will", "adds",
    "added", "released", "launches", "announces", "joins", "bans", "leaks",
    "hacked", "breaks", "fixes", "removes", "passes", "passed", "failed",
    "supports", "implements", "returns", "builds", "build", "new",
)
SIGNAL_VERBS = (
    "released", "launches", "announces", "joins", "bans", "leaks", "hacked",
    "breaks", "adds", "fixes", "removes", "is", "are", "has", "have", "can",
    "will", "new", "just", "keeps", "escapes", "purges", "bankrupted",
    "ported", "makes", "hitting", "found", "lost", "watching",
)
FAIL = re.compile(r"不通过|驳回|拒绝|reject|failed|失败|blocked|✗|error|错误", re.I)
SUCCESS = re.compile(r"零失败|无失败|0 failed|全绿|全部通过|验收通过|批准|PASS|passed|ok\b", re.I)
GENERIC_FAIL = {
    "agent offline", "heartbeat timeout", "never started (no heartbeat)",
    "legacy 失败提交", "hermes failed/timed out", "build · big-pickle",
    "exit 3221226505", "worker exec failed: [winerror 2] 系统找不到指定的文件。",
}
LIST_FRAGMENT = re.compile(r"^\s*(?:[①-⑩]|\d+[)）.、]|[-*]\s+)")
TRUNCATED = re.compile(
    r"^(?:\.|/|node:internal|traceback|npm err|e:internal|\[0m)|"
    r"\.pytest_tmp|^\s*$", re.I)
BOILER_ASCII = re.compile(r"\b(?:read more|sign in|log in|subscribe|share this|comments)\b", re.I)
BOILER_CN = ("点击这里", "登录后", "注册")
QUESTION = re.compile(r"^(?:what|why|how|is|are|can|do|does|should|ask hn|tell hn)\b", re.I)
ITEM = re.compile(r"[①-⑩]|(?<!\d)\d+[)）、]")


def terms(q: str) -> int:
    return len(re.findall(r"[。！？]|[.!?](?=\s|$)", q))


def has_verb(q: str, markers=VERBS) -> bool:
    low = q.lower()
    return any(m in low for m in markers)


def clean_ansi(q: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", q).strip()


def classify_kind(row: dict) -> str:
    src = row["source_key"]
    q = clean_ansi(row["quote"])
    if src in ("hn", "github-trending"):
        return "signal"
    if src in ("run.failure", "history.root_cause"):
        return "problem"
    if src == "doc.review":
        if FAIL.search(q) and not SUCCESS.search(q):
            return "problem"
        return "decision"
    if src == "run.result":
        if FAIL.search(q) and not SUCCESS.search(q):
            return "problem"
        if "易踩坑" in q or q.startswith("注："):
            return "lesson"
        if q.startswith(("git_sync.py 是", "现行", "task 表", "运行进程 PID", "测试跑在")):
            return "fact"
        return "result"
    return "decision"


def usable(row: dict, version: int) -> Tuple[bool, str]:
    q = clean_ansi(row["quote"])
    src = row["source_key"]
    kind = classify_kind(row)
    max_len = 240 if version >= 2 else 300
    max_terms = 2 if version >= 2 else 3
    if len(q) < 20:
        if src == "hn" and len(q) >= 12 and re.search(
                r"released|launches|announces", q, re.I):
            pass
        else:
            return False, "too_short"
    if len(q) > max_len:
        return False, "not_atomic_length"
    if terms(q) > max_terms:
        return False, "not_atomic_sentences"
    if len(ITEM.findall(q)) >= 3:
        return False, "not_atomic_items"
    if BOILER_ASCII.search(q) or any(x in q for x in BOILER_CN):
        return False, "boilerplate"
    if TRUNCATED.search(q):
        return False, "truncated_fragment"
    low = q.lower()
    if src in ("run.failure", "history.root_cause"):
        if low in GENERIC_FAIL or q in GENERIC_FAIL:
            return False, "generic_status"
    if src == "run.result":
        if LIST_FRAGMENT.match(q) and len(q) < 80:
            return False, "list_fragment"
    if src in ("doc.requirement", "doc.review", "discussion.closed"):
        if kind == "decision" and not has_verb(q):
            return False, "fragment_no_claim"
        if kind == "problem" and not has_verb(q):
            return False, "fragment_no_claim"
    if src in ("hn", "github-trending"):
        if q.startswith("Ask HN:"):
            return False, "question"
        if QUESTION.match(q) and not has_verb(q, SIGNAL_VERBS):
            return False, "question"
        if not has_verb(q, SIGNAL_VERBS):
            return False, "no_claim"
    return True, ""


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--overrides", default=None)
    args = p.parse_args()
    overrides = {}
    if args.overrides:
        overrides = json.loads(Path(args.overrides).read_text(encoding="utf-8"))
    rows = [json.loads(x) for x in Path(args.sample).read_text(
        encoding="utf-8").splitlines() if x.strip()]
    out = []
    for r in rows:
        k1, u1 = classify_kind(r), usable(r, 1)
        k2, u2 = classify_kind(r), usable(r, 2)
        ov = overrides.get(str(r["sample_id"]), {})
        if ov:
            if "kind" in ov:
                k1 = k2 = ov["kind"]
            if "usable" in ov:
                yes = ov["usable"] == "yes"
                u1 = (yes, "manual")
                u2 = (yes, "manual")
            agreed = True
            confidence = "high"
            note = ov.get("note", "manual_adjudication")
        else:
            agreed = (k1 == k2 and u1 == u2)
            confidence = "high" if agreed else "medium"
            note = ""
        out.append({
            **r,
            "rule_version": "20261028-v1.1",
            "labeler": "opencode-rule-assisted; not human gold",
            "sample_version": 2,
            "pass1_kind": k1, "pass1_usable": u1[0], "pass1_note": u1[1],
            "pass2_kind": k2, "pass2_usable": u2[0], "pass2_note": u2[1],
            "label_kind": k2,
            "label_usable": "yes" if u2[0] else "no",
            "label_duplicate_of": ov.get("duplicate_of", ""),
            "label_confidence": confidence,
            "review_status": "reviewed" if agreed else "contested",
            "label_notes": note or ("" if agreed else
                                    f"pass1={k1}/{u1[0]}({u1[1]}) pass2={k2}/{u2[0]}({u2[1]})"),
        })
    with Path(args.out).open("w", encoding="utf-8") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {
        "n": len(out),
        "reviewed": sum(1 for r in out if r["review_status"] == "reviewed"),
        "contested": sum(1 for r in out if r["review_status"] == "contested"),
        "usable_yes": sum(1 for r in out if r["label_usable"] == "yes"),
        "usable_no": sum(1 for r in out if r["label_usable"] == "no"),
        "kind_errors": sum(1 for r in out if r["label_kind"] != r["kind"]),
        "contested_ids": [r["sample_id"] for r in out if r["review_status"] == "contested"],
    }
    Path(args.summary).write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
