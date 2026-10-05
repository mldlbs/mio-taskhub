# -*- coding: utf-8 -*-
"""安装 mio-taskhub 文档门控 pre-push 钩子（跨仓库 / 批量）。

校验点：
- ② 任务 spec/api/plan 文档已 approved（`check_doc_approved.py`）
- ③ diff 引用的 FR-n 存在于已批准需求文档（`check_fr_trace.py`）

taskhub 需在本地 http://127.0.0.1:48620 运行；不可达时钩子放行并告警，不卡死开发。

用法：
    python scripts/install_hooks.py                      # 装到 mio-taskhub 本仓库
    python scripts/install_hooks.py --repo <path> ...    # 装到指定仓库（可重复）
    python scripts/install_hooks.py --scan <root>        # 递归扫描 root 下所有 git 仓库
    python scripts/install_hooks.py --scan E:/work/code/agent-dev --dry-run

钩子生成的 pre-push 会把 mio-taskhub 根目录**绝对路径**写死进去，因此从任意仓库
push 都能定位到 `scripts/git-hooks/pre_push_runner.py`；可用环境变量
`MIO_TASKHUB_ROOT` 覆盖。
"""
import argparse
import os
import stat
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PRUNE = {".git", "node_modules", ".venv", "venv", "dist", "build",
         "__pycache__", ".mypy_cache", ".pytest_cache", ".idea", ".vscode"}

PRE_PUSH_TEMPLATE = """#!/bin/sh
# mio-taskhub 文档门控 pre-push（由 scripts/install_hooks.py 生成）
# ② 任务 spec/api/plan 已 approved；③ diff 引用的 FR-n 存在于需求文档。
# 可用环境变量覆盖：MIO_TASKHUB_ROOT / MIO_DOC_GATE_KINDS / MIO_DOC_GATE_STRICT。
MIO_ROOT="${MIO_TASKHUB_ROOT:-@MIO_ROOT@}"
if [ -x "$MIO_ROOT/.venv/Scripts/python.exe" ]; then
  PY="$MIO_ROOT/.venv/Scripts/python.exe"
elif [ -x "$MIO_ROOT/.venv/bin/python" ]; then
  PY="$MIO_ROOT/.venv/bin/python"
else
  PY="$(command -v python3 2>/dev/null || command -v python 2>/dev/null)"
fi
if [ -z "$PY" ]; then
  echo "[doc-gate] 未找到 python，跳过文档门控（放行）" >&2
  exit 0
fi
if [ ! -f "$MIO_ROOT/scripts/git-hooks/pre_push_runner.py" ]; then
  echo "[doc-gate] 未找到 pre_push_runner.py，跳过文档门控（放行）" >&2
  exit 0
fi
exec "$PY" "$MIO_ROOT/scripts/git-hooks/pre_push_runner.py" "$@"
"""


def render_pre_push(mio_root):
    """渲染 pre-push 钩子内容，写入 mio-taskhub 根的绝对路径（正斜杠）。"""
    return PRE_PUSH_TEMPLATE.replace("@MIO_ROOT@", mio_root.replace("\\", "/"))


def _git_dir(repo_path):
    """返回仓库的 .git 目录；worktree / submodule（.git 为文件）解析 gitdir。"""
    dotgit = os.path.join(repo_path, ".git")
    if os.path.isdir(dotgit):
        return dotgit
    if os.path.isfile(dotgit):
        try:
            with open(dotgit, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if line.lower().startswith("gitdir:"):
                        rel = line.split(":", 1)[1].strip()
                        if not os.path.isabs(rel):
                            rel = os.path.join(repo_path, rel)
                        return os.path.normpath(rel)
        except OSError:
            return None
    return None


def is_git_repo(path):
    return _git_dir(path) is not None


def find_repos(root):
    """递归查找 root 下所有 git 仓库路径（跳过 PRUNE 目录）。

    - root 本身是仓库时也计入，但**继续下钻**以发现子仓库/子项目仓。
    - 命中嵌套仓库后不再深入其内部（避免扫到 vendored 仓库）。
    - 结果去重（root 不会因 walk 首轮重复出现）。
    """
    root = os.path.abspath(root)
    repos, seen = [], set()

    def add(path):
        path = os.path.abspath(path)
        if path not in seen:
            seen.add(path)
            repos.append(path)

    if is_git_repo(root):
        add(root)
    for cur, dirs, _files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in PRUNE and not d.startswith(".")]
        if os.path.abspath(cur) == root:
            continue              # root 已处理，但保持下钻
        if is_git_repo(cur):
            add(cur)
            dirs[:] = []          # 嵌套仓库：不再深入
    return repos


def install_one(repo_path, content, dry_run=False):
    """把钩子写入 repo_path/.git/hooks/pre-push。返回 (ok, message)。"""
    gitdir = _git_dir(repo_path)
    if gitdir is None:
        return (False, "不是 git 仓库：%s" % repo_path)
    hooks_dir = os.path.join(gitdir, "hooks")
    dst = os.path.join(hooks_dir, "pre-push")
    if dry_run:
        return (True, "[dry-run] 将安装 → %s" % dst)
    os.makedirs(hooks_dir, exist_ok=True)
    if os.path.exists(dst):
        shutil.copyfile(dst, dst + ".bak")
    with open(dst, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    try:
        st = os.stat(dst)
        os.chmod(dst, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass
    return (True, "已安装 pre-push → %s" % dst)


def main(argv=None):
    # Windows 控制台默认 GBK，打印 emoji / 中文可能崩；统一切到 UTF-8（失败则忽略）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description="安装 mio-taskhub 文档门控 pre-push 钩子")
    ap.add_argument("--repo", action="append", default=[],
                    help="目标仓库根目录（可重复）")
    ap.add_argument("--scan", default=None,
                    help="递归扫描该目录下所有 git 仓库并全部安装")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写入")
    args = ap.parse_args(argv)

    targets = list(args.repo)
    if args.scan:
        found = find_repos(args.scan)
        if not found:
            print("在 %s 下未找到 git 仓库" % args.scan)
        targets.extend(found)
    if not targets:
        targets = [ROOT]

    content = render_pre_push(ROOT)
    print("mio-taskhub 根：%s" % ROOT)
    ok_count = 0
    for repo in targets:
        ok, msg = install_one(repo, content, dry_run=args.dry_run)
        print(("  [ok]   " if ok else "  [warn] ") + msg)
        ok_count += 1 if ok else 0

    print("\n完成：%d/%d 个仓库%s。" % (
        ok_count, len(targets), "（dry-run）" if args.dry_run else ""))
    print("taskhub 需在本地 http://127.0.0.1:48620 运行；不可达时放行并告警。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
