# -*- coding: utf-8 -*-
"""README 统计生成/校验脚本（T3.4 / M2）。

从代码派生 README 表格中的数字，避免人工维护导致漂移。
用法：
  python scripts/gen_readme_stats.py          # 打印当前实际值
  python scripts/gen_readme_stats.py --check  # 校验 README 与实际值一致，漂移 exit 1（CI 用）
  python scripts/gen_readme_stats.py --write  # 就地更新 README 表格
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

# 强制 UTF-8 输出：CI（Windows cp1252）下打印中文 drift 详情会 UnicodeEncodeError（T4.3）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent

# 统计口径：**只算 git 已跟踪的文件**（2026-10-21）。
# 原因：README 的数字写进 commit 后必须能被任何人 checkout 出来复现。
# 若按「工作树全部文件」统计，未提交的并行开发代码会被算进去 ——
# 表现为 README 数字与该 commit 自身的代码对不上（实测 2026-10-21：
# 外部 agent 并行开发的 installer 1929 行 + 23 个用例被计入，
# 而这些文件并不在该 commit 里）。
#
# 未跟踪文件不计入 → 提交它们时再跑一次 --write 即可，数字随之更新。
def _tracked_files() -> set:
    """git ls-files 的结果（仓库不是 git 仓库时退化为「工作树全部文件」）。"""
    import subprocess
    try:
        out = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        if out.returncode == 0:
            files = {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}
            if files:
                return files
    except (OSError, subprocess.SubprocessError):
        pass
    return None


_TRACKED = _tracked_files()


def _iter_py(rel_dir: str, suffix: str = ".py"):
    """遍历 rel_dir 下**已跟踪**的 .py 文件（绝对路径）。"""
    base = ROOT / rel_dir
    if not base.exists():
        return
    for f in base.rglob(f"*{suffix}"):
        if _TRACKED is not None:
            try:
                rel = f.relative_to(ROOT).as_posix()
            except ValueError:
                continue
            if rel not in _TRACKED:
                continue
        yield f


def _version() -> str:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    return m.group(1) if m else "unknown"


def _count_rest_endpoints() -> int:
    """统计 api/*.py 中 @router.get/post/put/patch/delete 装饰器数量。"""
    total = 0
    for f in _iter_py("mio_taskhub"):
        text = f.read_text(encoding="utf-8", errors="replace")
        total += len(re.findall(r'@router\.(get|post|put|patch|delete)\b', text))
    return total


def _count_mcp_tools() -> int:
    """统计 mcp_server.py 中 @_tool 装饰器数量。"""
    p = ROOT / "mio_taskhub" / "mcp_server.py"
    if not p.exists():
        return 0
    return len(re.findall(r'@_tool\b', p.read_text(encoding="utf-8")))


def _count_test_functions() -> int:
    """统计 tests/*.py 中 test_ 函数数量（仅已跟踪）。"""
    total = 0
    for f in _iter_py("tests"):
        if not f.name.startswith("test_"):
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        total += len(re.findall(r'^def test_', text, re.M))
    return total


def _count_loc() -> int:
    """统计 mio_taskhub/**/*.py 总行数（仅已跟踪）。"""
    total = 0
    for f in _iter_py("mio_taskhub"):
        total += len(f.read_text(encoding="utf-8", errors="replace").splitlines())
    return total


def _count_doc_kinds() -> int:
    """统计 doc_paths.py 中 DOC_KINDS 元组元素数量。"""
    p = ROOT / "mio_taskhub" / "doc_paths.py"
    if not p.exists():
        return 0
    text = p.read_text(encoding="utf-8")
    m = re.search(r'DOC_KINDS\s*=\s*\(([^)]+)\)', text, re.S)
    if m:
        return len(re.findall(r'"[^"]+"', m.group(1)))
    return 0


def _iter_frontend():
    """遍历 web/src 下已跟踪的前端源文件。"""
    fe = ROOT / "web" / "src"
    if not fe.exists():
        return
    for f in fe.rglob("*"):
        if f.suffix not in (".tsx", ".ts", ".jsx", ".js"):
            continue
        if _TRACKED is not None:
            try:
                rel = f.relative_to(ROOT).as_posix()
            except ValueError:
                continue
            if rel not in _TRACKED:
                continue
        yield f


def _count_frontend_files() -> int:
    """统计 web/src 下 .tsx/.ts/.jsx/.js 文件数（仅已跟踪）。"""
    return len(list(_iter_frontend()))


def _count_frontend_loc() -> int:
    fe = ROOT / "web" / "src"
    if not fe.exists():
        return 0
    total = 0
    for f in _iter_frontend():
        total += len(f.read_text(encoding="utf-8", errors="replace").splitlines())
    return total


def collect_stats() -> dict:
    return {
        "version": _version(),
        "rest_endpoints": _count_rest_endpoints(),
        "mcp_tools": _count_mcp_tools(),
        "test_functions": _count_test_functions(),
        "loc": _count_loc(),
        "doc_kinds": _count_doc_kinds(),
        "frontend_files": _count_frontend_files(),
        "frontend_loc": _count_frontend_loc(),
    }


def write(readme_text: str, stats: dict) -> str:
    """就地更新 README 中的数字（regex 定位，避免一次性 replace 二次失效）。"""
    text = readme_text
    text = re.sub(r'v\d+\.\d+\.\d+', f'v{stats["version"]}', text, count=1)
    text = re.sub(r'\*\*\d+ 个 REST 端点\*\*',
                  lambda m: f'**{stats["rest_endpoints"]} 个 REST 端点**', text)
    text = re.sub(r'\*\*\d+ 个 MCP 工具\*\*',
                  lambda m: f'**{stats["mcp_tools"]} 个 MCP 工具**', text)
    text = re.sub(r'\*\*\d+ 个用例\*\*',
                  lambda m: f'**{stats["test_functions"]} 个用例**', text)
    # 后端 LOC：`模块 / N 行`
    text = re.sub(r'(模块\s*/\s*)[\d,]+(\s*行)',
                  lambda m: m.group(1) + f'{stats["loc"]:,}' + m.group(2), text)
    # 前端 LOC：`文件 / N 行`（表格中第一个"文件 /"是前端行）
    text = re.sub(r'(文件\s*/\s*)[\d,]+(\s*行)',
                  lambda m: m.group(1) + f'{stats["frontend_loc"]:,}' + m.group(2), text, count=1)
    # 前端文件数：`· N 文件 /`
    text = re.sub(r'(·\s*)\d+(\s*文件\s*/)',
                  lambda m: m.group(1) + f'{stats["frontend_files"]}' + m.group(2), text)
    return text


def check(readme_text: str, stats: dict) -> list[str]:
    """返回漂移列表；空 = 一致。"""
    drifts = []
    # version
    expected_v = f'v{stats["version"]}'
    if expected_v not in readme_text:
        drifts.append(f"版本: 期望 '{expected_v}' 未在 README 中找到")
    # REST 端点
    expected_rest = f'**{stats["rest_endpoints"]} 个 REST 端点**'
    if expected_rest not in readme_text:
        m = re.search(r'\*\*(\d+) 个 REST 端点\*\*', readme_text)
        drifts.append(f"REST端点: README={m.group(1) if m else '?'} 实际={stats['rest_endpoints']}")
    # MCP 工具
    expected_mcp = f'**{stats["mcp_tools"]} 个 MCP 工具**'
    if expected_mcp not in readme_text:
        m = re.search(r'\*\*(\d+) 个 MCP 工具\*\*', readme_text)
        drifts.append(f"MCP工具: README={m.group(1) if m else '?'} 实际={stats['mcp_tools']}")
    # 测试用例
    expected_test = f'**{stats["test_functions"]} 个用例**'
    if expected_test not in readme_text:
        m = re.search(r'\*\*(\d+) 个用例\*\*', readme_text)
        drifts.append(f"测试用例: README={m.group(1) if m else '?'} 实际={stats['test_functions']}")
    # 后端 LOC
    expected_loc = f'{stats["loc"]:,}'
    if expected_loc not in readme_text:
        drifts.append(f"后端行数: README 期望 {expected_loc} 未找到")
    # 前端 LOC
    expected_fe_loc = f'{stats["frontend_loc"]:,}'
    if expected_fe_loc not in readme_text:
        drifts.append(f"前端行数: README 期望 {expected_fe_loc} 未找到")
    # 前端文件数
    expected_fe_files = f'{stats["frontend_files"]} 文件 /'
    if expected_fe_files not in readme_text:
        drifts.append(f"前端文件数: README 期望 {expected_fe_files} 未找到")
    return drifts


def main():
    stats = collect_stats()
    readme_path = ROOT / "README.md"

    if "--write" in sys.argv:
        old = readme_path.read_text(encoding="utf-8")
        new = write(old, stats)
        readme_path.write_text(new, encoding="utf-8")
        print(f"README updated: {stats}")
        return 0

    if "--check" in sys.argv:
        readme = readme_path.read_text(encoding="utf-8")
        drifts = check(readme, stats)
        if drifts:
            print("README drift detected:")
            for d in drifts:
                print(f"  - {d}")
            return 1
        print("README stats OK (no drift)")
        return 0

    print(f"version:         {stats['version']}")
    print(f"rest_endpoints:  {stats['rest_endpoints']}")
    print(f"mcp_tools:       {stats['mcp_tools']}")
    print(f"test_functions:  {stats['test_functions']}")
    print(f"loc:             {stats['loc']:,}")
    print(f"doc_kinds:       {stats['doc_kinds']}")
    print(f"frontend_files:  {stats['frontend_files']}")
    print(f"frontend_loc:    {stats['frontend_loc']:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
