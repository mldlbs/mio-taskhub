# -*- coding: utf-8 -*-
"""payload（程序压缩包）布局契约 —— **安装器与更新器共用的唯一事实源**。

契约：payload zip **根目录**下直接是 `TaskHub.exe` 与 `_internal/...`。

历史上发布过的 zip 顶层带一层目录（`mio-taskhub-v3/`），而更新器的
`verify_staging` 按「根目录即内容」校验 → 校验失败 → **自动更新装不上**。
本模块统一「解压时自动剥离顶层前缀目录」，让两条路径的期望一致。

放在 `mio_taskhub/` 根下（而非 installer/ 或 update/ 内），是为了让
`installer.payload` 与 `update.apply` 都能依赖它而不产生包间循环引用。
"""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

#: payload 必须包含的关键项（相对 payload 根目录）
REQUIRED_ENTRIES = (
    "TaskHub.exe",
    os.path.join("_internal", "web", "dist", "index.html"),
)

#: 已知的历史顶层目录名（优先按白名单剥离，避免误剥）
KNOWN_PREFIXES = ("mio-taskhub-v3", "mio-taskhub")


def _has_root_entries(names) -> bool:
    """zip 是否以内容条目为根（而非套了一层目录）。"""
    for n in names:
        if n.endswith("/"):
            continue
        head = n.split("/", 1)[0]
        if head in ("TaskHub.exe", "_internal"):
            return True
    return False


def detect_prefix(names) -> str:
    """返回需要剥离的顶层目录名；无需剥离时返回 ""。"""
    if _has_root_entries(names):
        return ""
    for n in names:
        if not n:
            continue
        head = n.split("/", 1)[0]
        if head in KNOWN_PREFIXES:
            return head
    # 兜底：单一顶层目录。**排除 "." / ".."** —— 它们不是目录名，
    # 当成前缀剥离会把越界路径伪装成正常条目（静默放过 zip 路径穿越）。
    tops = {n.split("/", 1)[0] for n in names if n}
    tops = {t for t in tops if t not in ("", ".", "..")}
    if len(tops) == 1:
        return tops.pop()
    return ""


def extract_zip(zip_path, dest) -> str:
    """把 zip 解压到 dest，**自动剥离顶层前缀目录**。

    返回实际被剥离的目录名（"" 表示无前缀）。
    抛 OSError / zipfile.BadZipFile 表示失败。
    """
    zip_path = Path(zip_path)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        prefix = detect_prefix(zf.namelist())
        for info in zf.infolist():
            name = info.filename
            if not name:
                continue
            if prefix:
                if not name.startswith(prefix + "/"):
                    continue
                name = name[len(prefix) + 1:]
            if not name:
                continue
            target = (dest / name).resolve()
            try:
                target.relative_to(dest_resolved)
            except ValueError:
                raise OSError("zip 内含越界路径：%s" % info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    dst.write(chunk)
    return prefix


def verify(root) -> tuple:
    """校验 payload 根目录是否含关键项。返回 (ok, message)。"""
    root = Path(root)
    for rel in REQUIRED_ENTRIES:
        if not (root / rel).exists():
            return False, "payload 缺少关键项：%s" % rel
    return True, "ok"
