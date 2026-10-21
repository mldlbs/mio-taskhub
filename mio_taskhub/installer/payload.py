# -*- coding: utf-8 -*-
"""payload（绿色版 zip）的定位、解压与校验。

payload 契约：zip **根目录**下直接是 `mio-taskhub.exe` 与 `_internal/...`。
若 zip 顶层带一层目录（历史产物 `mio-taskhub-v3/`），解压时**自动剥离**该前缀，
使安装目录布局与更新器的 `verify_staging` 期望一致。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from mio_taskhub.payload_layout import extract_zip, verify

from .paths import PAYLOAD_ZIP_NAME


def extract_payload(zip_path, dest) -> str:
    """把 payload 解压到 dest（自动剥离顶层前缀目录）。返回被剥离的目录名。

    解压/校验契约复用 `mio_taskhub.payload_layout`（与更新器同一份事实源），
    因此历史产物（顶层带 `mio-taskhub-v3/`）与新产物（根目录即内容）都能装。
    """
    return extract_zip(zip_path, dest)


def verify_payload(root) -> tuple:
    """校验解压根目录是否含关键项。返回 (ok, message)。"""
    return verify(root)


def find_payload(explicit=None, search_dirs=None) -> Path:
    """定位 payload zip。

    优先级：显式路径 → **安装器同级目录** → 常见下载目录 → 临时目录。
    找不到抛 FileNotFoundError。

    「安装器同级目录」是**真实分发的形态**（setup.exe 与 zip 放在一起），
    必须排在最前面 —— 否则打包后的静默安装会找不到包（2026-10-21 实测）。
    """
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return p
        raise FileNotFoundError("找不到 payload：%s" % explicit)

    names = (PAYLOAD_ZIP_NAME, "mio-taskhub.zip")
    dirs = [Path(d) for d in (search_dirs or [])]
    # 1) 安装器自身所在目录（打包分发时 zip 与 setup.exe 同目录）
    dirs.append(_exe_dir())
    # 2) 当前工作目录
    dirs.append(Path.cwd())
    # 3) 常见下载目录
    for env in ("USERPROFILE", "HOME"):
        home = os.environ.get(env)
        if home:
            dirs.append(Path(home) / "Downloads")
            dirs.append(Path(home) / "Desktop")
    # 4) 临时目录
    dirs.append(Path(tempfile.gettempdir()))

    seen = set()
    for d in dirs:
        key = str(d).lower()
        if key in seen:
            continue
        seen.add(key)
        for n in names:
            p = d / n
            if p.is_file():
                return p
    raise FileNotFoundError(
        "未找到安装包 %s（已搜索：%s）" % (" / ".join(names), "、".join(str(d) for d in dirs)))


def _exe_dir() -> Path:
    """当前可执行文件所在目录（打包态 = exe 目录；源码态 = 仓库根）。"""
    import sys
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent
