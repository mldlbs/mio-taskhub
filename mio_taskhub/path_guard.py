"""路径约束（P0 安全修复，2026-11-07）。

背景：上线前审计发现三条 P0 里，两条依赖「路径没有边界」：

- `_resolve_doc_path` 接受任意绝对路径，可读 `~/.mio-intelligence/config.json`（含明文 apiKey）；
- `workspace` 可被 PATCH 成任意目录，`PUT /doc` 随即可在该目录任意写文本文件。

本模块只负责路径侧约束，鉴权侧见 `admin_guard.py`：

1. 文档读写必须落在任务 workspace 内；跨目录读取只允许在 `MIO_TASKHUB_DOC_ROOTS`
   显式登记的根下，或用 `MIO_TASKHUB_DOC_ALLOW_OUTSIDE=1` 显式恢复旧行为（不推荐）。
2. 文档写入只允许文档类后缀，且禁止写进点目录（`.ssh` / `.git` 等）。
3. workspace 必须是绝对路径，不能是盘根 / 用户主目录 / 系统目录；可用
   `MIO_TASKHUB_WORKSPACE_ROOTS`（os.pathsep 分隔）进一步收敛为白名单。
"""
import os
import pathlib

# 允许写入的文档后缀（写文档不是写任意文件）
WRITABLE_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}

# 越界错误的统一前缀，调用方据此把「策略拒绝」与「文件缺失」区分开（前者 400，后者软提示）
OUT_OF_SCOPE_PREFIX = "文档路径超出允许范围"

_TRUTHY = {"1", "true", "yes", "on"}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _env_roots(name: str) -> list[pathlib.Path]:
    raw = os.environ.get(name, "")
    roots = []
    for part in raw.split(os.pathsep):
        s = part.strip()
        if not s:
            continue
        try:
            roots.append(pathlib.Path(s).expanduser().resolve())
        except OSError:
            continue
    return roots


def is_within(target: pathlib.Path, base: pathlib.Path) -> bool:
    """target 是否在 base 之内（含相等）。Windows 下按 normcase 兜底大小写差异。"""
    try:
        target.relative_to(base)
        return True
    except ValueError:
        pass
    t = os.path.normcase(str(target))
    b = os.path.normcase(str(base)).rstrip("\\/")
    return bool(b) and (t == b or t.startswith(b + os.sep))


def resolve_target(path: str, base: pathlib.Path) -> pathlib.Path:
    """把绝对/相对路径解析为绝对路径（不要求存在）。"""
    p = pathlib.Path(path)
    return (p if p.is_absolute() else base / p).resolve()


def _system_roots() -> list[pathlib.Path]:
    roots = []
    for key in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        v = os.environ.get(key)
        if not v:
            continue
        try:
            roots.append(pathlib.Path(v).resolve())
        except OSError:
            continue
    return roots


def workspace_error(ws: str) -> str | None:
    """校验 workspace 取值。返回错误文案，None 表示通过。"""
    s = (ws or "").strip()
    if not s:
        return None
    p = pathlib.Path(s).expanduser()
    try:
        target = p.resolve()
    except OSError as e:
        return f"workspace 无法解析：{e}"
    if str(target) == target.anchor:
        return "workspace 不能是磁盘根目录"
    try:
        if target == pathlib.Path.home().resolve():
            return "workspace 不能是用户主目录（文档写入会越权落到 ~ 下）"
    except (OSError, RuntimeError):
        pass
    for root in _system_roots():
        if is_within(target, root):
            return "workspace 不能是系统目录"
    # 禁止把 workspace 指到点目录（.ssh / .mio-intelligence / .config 等）：
    # 否则「workspace 内可读」等于把密钥目录纳进可读范围。
    for part in target.parts[1:]:
        if part.startswith("."):
            return f"workspace 不能位于点目录下（{part}）"
    allowed = _env_roots("MIO_TASKHUB_WORKSPACE_ROOTS")
    if allowed and not any(is_within(target, r) for r in allowed):
        return ("workspace 不在允许的根目录内（MIO_TASKHUB_WORKSPACE_ROOTS="
                + os.pathsep.join(str(r) for r in allowed) + "）")
    return None


def _dot_part(base: pathlib.Path, target: pathlib.Path) -> str | None:
    """target 相对 base 的路径里是否含点目录/点文件（返回到第一个命中段）。

    读取侧的对称约束：写入早已禁止点目录，读取若不禁，`.env`、`.git/config`、
    `.npmrc` 这类文件仍能在「自报 workspace」下被读走。
    """
    try:
        rel = target.relative_to(base)
    except ValueError:
        t = os.path.normcase(str(target))
        b = os.path.normcase(str(base)).rstrip("\\/")
        if not (t == b or t.startswith(b + os.sep)):
            return None
        rel = pathlib.Path(t[len(b):].lstrip("\\/"))
    for part in rel.parts:
        if part.startswith(".") and part not in (".", ".."):
            return part
    return None


def doc_read_error(workspace: str, target: pathlib.Path, extra_roots=()) -> str | None:
    """文档读取的路径范围校验。返回错误文案，None 表示允许。

    `extra_roots`：调用方补充的放行根（如库里所有已知任务的 workspace）。
    老任务常把文档放在自己的工作区之外的兄弟项目目录，靠它兼容；
    这些根来自数据库里已登记的 workspace，不是请求方随便指的路径。
    """
    base_ws = (workspace or "").strip()
    if base_ws:
        try:
            base = pathlib.Path(base_ws).expanduser().resolve()
        except OSError:
            base = None
        if base is not None and is_within(target, base):
            hit = _dot_part(base, target)
            if hit:
                return f"{OUT_OF_SCOPE_PREFIX}：{target} 位于点目录/点文件（{hit}）下，不允许作为文档读取。"
            return None
    for root in list(extra_roots) + _env_roots("MIO_TASKHUB_DOC_ROOTS"):
        if is_within(target, root):
            hit = _dot_part(root, target)
            if hit:
                return f"{OUT_OF_SCOPE_PREFIX}：{target} 位于点目录/点文件（{hit}）下，不允许作为文档读取。"
            return None
    if _env_flag("MIO_TASKHUB_DOC_ALLOW_OUTSIDE"):
        return None
    where = f"任务 workspace（{base_ws}）" if base_ws else "任务未设置 workspace"
    return (f"{OUT_OF_SCOPE_PREFIX}：{target} 不在 {where} 内。"
            f"把文件放到 workspace 下，或用 MIO_TASKHUB_DOC_ROOTS 显式登记文档根目录。")


def doc_write_error(target: pathlib.Path, rel: str) -> str | None:
    """文档写入的路径范围校验（调用方已确认在 workspace 内）。"""
    if target.exists() and target.is_dir():
        return f"目标路径是目录：{rel}"
    for part in pathlib.Path(rel).parts:
        if part.startswith(".") and part not in (".", ".."):
            return f"禁止写入点目录/点文件：{rel}"
    suffix = target.suffix.lower()
    if suffix not in WRITABLE_SUFFIXES:
        return (f"只允许写入文档类文件（{'/'.join(sorted(WRITABLE_SUFFIXES))}），拒绝：{rel}")
    return None
