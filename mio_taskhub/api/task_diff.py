# -*- coding: utf-8 -*-
"""FR-07（任务 c8316737）：任务级只读 diff 路由。

GET /api/v1/tasks/{task_id}/diff —— 提交结果前给「唯一用户」核对一眼：
本次任务的变更文件清单 + 聚合统计，文件尽量标注关联的 FR-n。

设计要点（requirement FR-07 验收标准逐条对应）：
  - 只读：不触发状态机、不写质量分、不落事件、不改 run/task 任何字段。
  - 状态码：任务不存在 → 404；workspace 非空但目录不存在/不可读 → 403；
    无变更 → 200 空集；任何写方法 → 405（FastAPI 仅注册 GET，天然满足）；
    采集失败 fail-open 返回可用子集，不得 500。
  - 证据源聚合（三路，全部只读）：
      1) 各 run.diff_summary['after'] 的快照（run 级证据，含 paths 与行数）
      2) diff_gate.snapshot(task.workspace) 的当前快照（工作树级证据）
      3) 文件 stat（mtime/size，文件存在时附带）
  - FR-n 映射：扫描 requirement 文档正文（确定性文本聚合，不调 LLM——
    上游规划 S1.4 的同一条铁律），建立「路径片段 → 最近 FR-n 标题」映射；
    映射不到 → "UNMAPPED"。
  - A-P6 修正说明：PRD 原写「文件级 mtime/size 聚合、不引 git 依赖」，
    实现时发现 d468e633 窄门禁已引入 mio_taskhub.diff_gate（只读 git 子命令、
    run 创建时自动拍 before 快照）——复用它无新增依赖且证据更准，予以修正。
"""
import json
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from mio_taskhub.db import get_session
from mio_taskhub.models import Run, Task

router = APIRouter(prefix="/tasks", tags=["task-diff"])

# requirement 文档中「文件路径片段」的识别模式：含扩展名的相对路径词。
_PATH_TOKEN = re.compile(r"[\w\-./\\]+\.[A-Za-z0-9]{1,6}")
# FR 标题行：### FR-07 / ## FR-12：xxx / 行内 FR-3（宽口径，宁可多建候选映射）。
_FR_TOKEN = re.compile(r"\bFR[-–—]?\s?(\d+)\b", re.IGNORECASE)

# 单文件路径片段最大长度（过滤明显不是路径的 token）。
_MAX_TOKEN_LEN = 120


def _load_doc_paths(task: Task) -> dict:
    """task.doc_paths → dict（JSON 列可能已是 dict 或 str）。"""
    raw = getattr(task, "doc_paths", None)
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _build_fr_map(requirement_text: str) -> dict:
    """确定性聚合：requirement 正文 → {路径片段: FR-n}。

    规则：逐行扫描，维护「最近出现的 FR 编号」；行内出现的每个路径 token
    映射到该 FR。同一 token 后出现的 FR 覆盖先出现的（文档靠后的更具体）。
    """
    mapping: dict = {}
    current_fr = None
    for line in (requirement_text or "").splitlines():
        m = _FR_TOKEN.search(line)
        if m:
            current_fr = f"FR-{int(m.group(1)):02d}"
        if current_fr:
            for tok in _PATH_TOKEN.findall(line):
                tok = tok.strip("\"'`()[]，。；：:")
                if 3 < len(tok) <= _MAX_TOKEN_LEN and ("/" in tok or "." in tok):
                    mapping[tok.replace("\\", "/")] = current_fr
    return mapping


def _match_fr(path: str, fr_map: dict) -> str:
    """changed_file 路径 → FR-n 或 UNMAPPED。路径与 basename 双重匹配。"""
    if not fr_map:
        return "UNMAPPED"
    norm = path.replace("\\", "/")
    base = norm.rsplit("/", 1)[-1]
    for token, fr in fr_map.items():
        if token and (token == norm or token == base
                      or norm.endswith("/" + token) or token.endswith("/" + base)):
            return fr
    return "UNMAPPED"


def _file_stat(workspace: str, rel_path: str) -> dict:
    """文件存在时附带的 stat（mtime/size）。不存在返回空串字段。"""
    full = os.path.join(workspace, rel_path)
    try:
        st = os.stat(full)
        return {"mtime": int(st.st_mtime), "size": st.st_size}
    except OSError:
        return {"mtime": None, "size": None}


# ── E3 假门埋点（FR-03，任务 c8316737，临时实验装置）──────────────────
# 只追加本地 jsonl（不联网、不回写数据库）；实验结束可整体移除（A-P5）。
# 路径可用 MIO_E3_VIEW_LOG 覆盖（测试隔离用），缺省 ~/.mio_taskhub/e3_diff_view.jsonl。
def _e3_log_view(task_id: str, status: int, n_files: int, fr_ref: str = ""):
    try:
        from datetime import datetime, timezone as _tz
        path = os.environ.get("MIO_E3_VIEW_LOG") or os.path.join(
            os.path.expanduser("~/.mio_taskhub"), "e3_diff_view.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(_tz.utc).isoformat(),
                "task_id": task_id,
                "action": "view",
                "http_status": status,
                "fr_ref": fr_ref,
                "files": n_files,
            }, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — 埋点失败绝不影响路由本身
        pass


@router.get("/{task_id}/diff")
def task_diff(task_id: str, fr_ref: str = "", db: Session = Depends(get_session)):
    # E3 假门埋点（FR-03）：任何出口（200/403/404）都记一行本地 jsonl
    _status = [0]
    stat = {"files": 0}  # finally 引用兜底（404/403 路径不会走到 ④）

    def _log(status_code: int):
        _status[0] = status_code

    try:
        # ① 404：任务不存在（先于一切，不泄漏其他信息）
        task = db.get(Task, task_id)
        if not task:
            _log(404)
            raise HTTPException(404, detail={
                "code": "task.not_found",
                "message": f"task {task_id} 不存在",
            })

        ws = (task.workspace or "").strip()

        # ② 无 workspace：数据常态（创意/观察类任务），200 + reason + 空证据
        if not ws:
            _log(200)
            return {
                "task_id": task_id,
                "workspace": "",
                "reason": "no_workspace",
                "stat": {"files": 0, "insertions": 0, "deletions": 0, "runs": 0},
                "changed_files": [],
            }

        # ③ 403：声明了 workspace 但目录不存在或不可读（含无权限）
        if not os.path.isdir(ws) or not os.access(ws, os.R_OK):
            _log(403)
            raise HTTPException(403, detail={
                "code": "task.workspace_unreadable",
                "message": f"workspace 不可读：{ws}",
            })

        # ④ 聚合 run 级证据（run.diff_summary['after']）
        runs = db.exec(select(Run).where(Run.task_id == task_id)).all()
        run_sources: dict = {}   # path -> source 标签（run:<id>）
        stat = {"files": 0, "insertions": 0, "deletions": 0, "runs": len(runs)}
        for run in runs:
            ds = run.diff_summary if isinstance(run.diff_summary, dict) else {}
            after = ds.get("after") if isinstance(ds.get("after"), dict) else None
            before = ds.get("before") if isinstance(ds.get("before"), dict) else None
            for snap in (after, before):
                if not snap:
                    continue
                for p in (snap.get("paths") or []):
                    run_sources[p.replace("\\", "/")] = f"run:{run.id}"
            if after:
                stat["insertions"] += int(after.get("insertions") or 0)
                stat["deletions"] += int(after.get("deletions") or 0)

        # ⑤ 聚合工作树级证据（diff_gate.snapshot，fail-open）
        now_paths: dict = {}
        try:
            from mio_taskhub.diff_gate import snapshot as _dg_snapshot
            snap = _dg_snapshot(ws)
            if snap.ok:
                for p in snap.paths:
                    now_paths[p.replace("\\", "/")] = "workspace_now"
                if snap.files:
                    stat["files"] = snap.files
                stat["insertions"] += snap.insertions
                stat["deletions"] += snap.deletions
        except Exception:  # noqa: BLE001 — 采集失败 fail-open，返回 run 级子集
            pass

        # ⑥ 合并去重（run 证据优先；工作树当前态补充）
        merged: dict = dict(now_paths)
        merged.update(run_sources)

        # ⑦ FR-n 映射：读 requirement 文档（doc_paths 注册的路径，相对 workspace）
        fr_map: dict = {}
        req_rel = _load_doc_paths(task).get("requirement")
        if req_rel:
            req_full = os.path.join(ws, req_rel)
            try:
                with open(req_full, "r", encoding="utf-8", errors="replace") as f:
                    fr_map = _build_fr_map(f.read())
            except OSError:
                pass

        # ⑧ 组装 changed_files
        changed_files = []
        for path, source in sorted(merged.items()):
            entry = {
                "path": path,
                "fr": _match_fr(path, fr_map),
                "source": source,
            }
            entry.update(_file_stat(ws, path))
            changed_files.append(entry)

        stat["files"] = max(stat["files"], len(changed_files))

        _log(200)
        return {
            "task_id": task_id,
            "workspace": ws,
            "reason": "ok" if changed_files else "no_changes",
            "stat": stat,
            "changed_files": changed_files,
        }
    finally:
        # E3 假门埋点（FR-03）：任何出口（200/403/404）都记一行
        if _status[0]:
            _e3_log_view(task_id, _status[0], stat.get("files", 0), fr_ref)
