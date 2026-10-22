# -*- coding: utf-8 -*-
"""把 payload zip **附加**到安装器 exe 自身 → 单文件安装包。

**为什么要合并**：真实世界的安装器（NSIS / Inno Setup）都是**单文件**
`setup.exe` —— 用户只下一个文件、双击、装完就走。原来的形态要用户下
两个文件（setup.exe + win64.zip）再放到同一目录，明显反人类。

**做法（self-extracting）**：
1. 打包阶段（`scripts/make_setup.py`）：把 payload zip 追加到
   `mio-taskhub-setup.exe` 末尾，再写一个**尾部标记**（magic + 偏移量）。
2. 运行阶段（`installer/embedded.py`）：读自己 exe 末尾的标记 → 按偏移量
   把那段 zip 解出来 → 交给安装流程。

为什么用「尾部标记」而不是「按 PE 结构找 overlay」：
PE 头里没有「附加数据」的字段，只能靠自己的标记定位。标记放在文件**最末尾**
且固定 16 字节，读的时候 seek 到 `size - 16` 就能判断 —— 不用扫描整个文件。

**标记格式**（16 字节，小端）：
    magic  : 8 字节  b"MIOSTUP1"
    offset : 4 字节  payload zip 在 exe 里的起始偏移
    size   : 4 字节  payload zip 长度
"""
from __future__ import annotations

import os
import struct
import tempfile
import zipfile
from pathlib import Path

#: 尾部标记的 magic（8 字节）
MAGIC = b"MIOSTUP1"

#: 尾部标记长度（magic 8 + offset 4 + size 4）
MARKER_LEN = len(MAGIC) + 8

#: 标记在文件里的结构格式
_MARKER_FMT = "<8sII"


def marker_bytes(offset: int, size: int) -> bytes:
    """生成尾部标记。"""
    return struct.pack(_MARKER_FMT, MAGIC, int(offset), int(size))


def append_payload(exe_path, payload_zip) -> Path:
    """把 payload zip 附加到 exe 末尾，返回新 exe 路径（同路径覆盖）。

    先检查 exe 末尾是否已有标记 —— 有的话说明是重复调用，**先剥掉旧的**
    再追加新的（否则 payload 会越叠越大）。
    """
    exe = Path(exe_path)
    payload = Path(payload_zip)
    if not exe.is_file():
        raise FileNotFoundError("找不到 exe：%s" % exe)
    if not payload.is_file():
        raise FileNotFoundError("找不到 payload：%s" % payload)

    # 剥掉可能存在的旧附加数据
    base = _strip_payload(exe)

    offset = base
    with open(exe, "r+b") as f:
        f.seek(offset)
        with open(payload, "rb") as pf:
            while True:
                chunk = pf.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        f.write(marker_bytes(offset, payload.stat().st_size))
    return exe


def _strip_payload(exe) -> int:
    """剥掉 exe 末尾已附加的 payload，返回剥完后的基础长度。

    没有标记时原样返回（文件长度）。
    """
    exe = Path(exe)
    size = exe.stat().st_size
    if size < MARKER_LEN:
        return size
    with open(exe, "rb") as f:
        f.seek(size - MARKER_LEN)
        tail = f.read(MARKER_LEN)
    try:
        magic, offset, plen = struct.unpack(_MARKER_FMT, tail)
    except struct.error:
        return size
    if magic != MAGIC:
        return size
    # 合理性检查：offset + plen 必须正好等于「标记之前」的长度
    if offset + plen != size - MARKER_LEN:
        return size
    with open(exe, "r+b") as f:
        f.truncate(offset)
    return offset


def read_embedded_marker(exe_path):
    """读 exe 末尾的标记。返回 (offset, size)；没有标记返回 None。"""
    exe = Path(exe_path)
    size = exe.stat().st_size
    if size < MARKER_LEN:
        return None
    with open(exe, "rb") as f:
        f.seek(size - MARKER_LEN)
        tail = f.read(MARKER_LEN)
    try:
        magic, offset, plen = struct.unpack(_MARKER_FMT, tail)
    except struct.error:
        return None
    if magic != MAGIC:
        return None
    if offset + plen != size - MARKER_LEN:
        return None
    return offset, plen


def extract_embedded_payload(exe_path, dest_dir=None) -> Path:
    """把 exe 里附加的 payload 取出成一个 zip 文件，返回其路径。

    优先取到 `dest_dir`（默认临时目录）；取完**不删**（调用方决定生命周期），
    但用完要自己清（安装流程结束时会删临时目录）。
    """
    exe = Path(exe_path)
    marker = read_embedded_marker(exe)
    if not marker:
        raise FileNotFoundError("该 exe 没有内嵌安装包：%s" % exe)
    offset, plen = marker
    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix="mio_embedded_")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / "payload.zip"
    with open(exe, "rb") as f:
        f.seek(offset)
        remaining = plen
        with open(out, "wb") as w:
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                w.write(chunk)
                remaining -= len(chunk)
    if remaining != 0:
        raise IOError("内嵌安装包不完整（少了 %d 字节）" % remaining)
    return out


def has_embedded_payload(exe_path) -> bool:
    """exe 是否内嵌了 payload。"""
    try:
        return read_embedded_marker(exe_path) is not None
    except OSError:
        return False
