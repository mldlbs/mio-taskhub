# -*- coding: utf-8 -*-
"""② 交付物门控：校验任务的 spec / api / plan 文档已 approved。

返回 (allow: bool, reason: str)。allow=False 时 reason 用于打印阻塞原因。

语义（与后端阶段门控 `task_stages._check_lifecycle_gate` 对齐）：
- 任务**未登记任何文档**（doc_statuses 与 doc_paths 均空）→ 放行并告警（向后兼容，
  不卡死历史任务 / 未跟踪任务）。
- 任务**已登记文档**（有 doc_paths 或任一 doc_statuses）→ 必须把要求的 kinds 全部
  推进到 approved，否则阻塞。

可通过环境变量覆盖：
- `MIO_DOC_GATE_KINDS`：逗号分隔的要求 kinds，默认 `spec,api,plan`。
- `MIO_DOC_GATE_STRICT=1`：连「未登记文档」的任务也强制要求（默认关闭）。

taskhub 不可达 / 查询异常 → 放行并告警（见方案 §7）。
"""
import os
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from doc_gate_common import get_task, warn_allow, block

DEFAULT_KINDS = ("spec", "api", "plan")
APPROVED = "approved"
_TRUE = ("1", "true", "yes", "on")


def required_kinds():
    raw = (os.environ.get("MIO_DOC_GATE_KINDS") or "").strip()
    if not raw:
        return list(DEFAULT_KINDS)
    return [k.strip() for k in raw.split(",") if k.strip()]


def is_strict():
    return (os.environ.get("MIO_DOC_GATE_STRICT") or "").strip().lower() in _TRUE


def check_doc_approved(task_id, task=None):
    if task is None:
        try:
            task = get_task(task_id)
        except urllib.error.URLError as e:
            return warn_allow("无法连接 taskhub（%s），跳过文档批准校验" % e)
        except OSError as e:
            return warn_allow("无法连接 taskhub（%s），跳过文档批准校验" % e)
        except Exception as e:  # noqa: BLE001
            return warn_allow("查询任务 %s 失败：%s" % (task_id, e))

    kinds = required_kinds()
    statuses = task.get("doc_statuses") or {}
    doc_paths = task.get("doc_paths") or {}
    tracked = bool(statuses) or bool(doc_paths)

    if not tracked and not is_strict():
        return warn_allow(
            "任务 %s 未登记文档，跳过 %s 批准校验（设 MIO_DOC_GATE_STRICT=1 可强制）"
            % (task_id, "/".join(kinds))
        )

    missing = []
    for kind in kinds:
        st = (statuses.get(kind) or {}).get("state")
        if st != APPROVED:
            missing.append("%s=%s" % (kind, st or "未登记/草稿"))
    if missing:
        return block(
            "任务 %s 的文档尚未批准，禁止推送：%s\n"
            "  → 先在 DocPanel / taskhub 将 %s 推进到 approved，再 push"
            % (task_id, "，".join(missing), "、".join(kinds))
        )
    return (True, "任务 %s 的 %s 均已 approved ✅" % (task_id, "/".join(kinds)))


if __name__ == "__main__":
    tid = sys.argv[1] if len(sys.argv) > 1 else None
    if not tid:
        print("用法：check_doc_approved.py <task_id>")
        sys.exit(2)
    allow, reason = check_doc_approved(tid)
    print(reason)
    sys.exit(0 if allow else 1)
