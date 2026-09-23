# -*- coding: utf-8 -*-
"""③ FR-n 可追溯校验：diff 中引用的 FR-n 必须真实存在于「已批准」需求文档。

返回 (allow: bool, reason: str)。
- 未引用任何 FR-n → 放行（info）。
- 需求文档缺失/无法读取 → 放行并告警。
- 引用的 FR-n 有不存在于需求文档的 → 阻塞，列出缺失项。
- 需求文档本身未 approved → 仅告警，不阻塞（FR 存在性仍是硬校验）。
"""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from doc_gate_common import (
    collect_diff_frs, get_doc_content, extract_frs_from_text,
    warn_allow, block,
)

REQUIREMENT_KIND = "requirement"


def check_fr_trace(task_id, local_sha, remote_sha, requirement_content=None):
    frs = collect_diff_frs(local_sha, remote_sha)
    if frs is None:
        return warn_allow("无法取得 diff，跳过 FR-n 可追溯校验")
    if not frs:
        return (True, "diff 未引用 FR-n，跳过可追溯校验")

    if requirement_content is None:
        try:
            content, status = get_doc_content(task_id, REQUIREMENT_KIND)
        except urllib.error.URLError as e:
            return warn_allow("无法连接 taskhub（%s），跳过 FR-n 校验" % e)
        except OSError as e:
            return warn_allow("无法连接 taskhub（%s），跳过 FR-n 校验" % e)
        except Exception as e:  # noqa: BLE001
            return warn_allow("读取需求文档失败：%s" % e)
        if not content or "文件不存在" in content or "缺失" in content:
            return warn_allow("任务 %s 无需求文档，跳过 FR-n 校验" % task_id)
        st = (status or {}).get("state")
        if st != "approved":
            sys.stderr.write(
                "[doc-gate] ⚠️  任务 %s 的需求文档未 approved（state=%s），"
                "FR 仍存在性校验照常，但建议先批准需求文档\n"
                % (task_id, st)
            )
    else:
        content = requirement_content

    doc_frs = extract_frs_from_text(content)
    missing = sorted(frs - doc_frs)
    if missing:
        return block(
            "任务 %s 引用了需求文档中不存在的 FR-n：%s\n"
            "  → 这些编号必须先在需求文档（requirement）里定义并编号，再在代码中引用"
            % (task_id, "、".join(missing))
        )
    return (True, "引用的 FR-n 均存在于需求文档 ✅（%s）" % "、".join(sorted(frs)))


if __name__ == "__main__":
    tid = sys.argv[1] if len(sys.argv) > 1 else None
    lsha = sys.argv[2] if len(sys.argv) > 2 else "HEAD"
    rsha = sys.argv[3] if len(sys.argv) > 3 else "0" * 40
    if not tid:
        print("用法：check_fr_trace.py <task_id> <local_sha> <remote_sha>")
        sys.exit(2)
    allow, reason = check_fr_trace(tid, lsha, rsha)
    print(reason)
    sys.exit(0 if allow else 1)
