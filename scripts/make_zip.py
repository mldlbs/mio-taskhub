# -*- coding: utf-8 -*-
"""确定性打包 zip（替代 PowerShell 的 Compress-Archive）。

为什么不用 `Compress-Archive`：
1. **只读文件会失败** —— 打包产物的 `_internal/base_library.zip` 是只读的，
   Compress-Archive 直接抛 `CompressArchiveUnauthorizedAccessError`。
2. **布局不稳定** —— 实测发布出去的 v0.6.1 资产顶层带一层 `mio-taskhub-v3/`，
   而 `Compress-Archive -Path (Join-Path $distDir '*')` 出来的是平铺结构；
   更新器的 `verify_staging` 按平铺校验 → **自动更新永远装不上**。
3. **不可复现** —— Compress-Archive 的成员顺序/时间戳不稳定。

本脚本产出**平铺**（根目录直接是 `mio-taskhub.exe`）且**可复现**的 zip，
与 `mio_taskhub.payload_layout` 的契约一致。

用法：
  python scripts/make_zip.py <源目录> <输出 zip> [--prefix NAME]
"""
from __future__ import annotations

import os
import stat
import sys
import zipfile
from pathlib import Path

#: 固定时间戳（1980-01-01，zip 格式下限）—— 保证同样输入产出同样字节
FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def _iter_files(root: Path):
    """按相对路径排序遍历文件（排序 → 成员顺序可复现）。"""
    items = []
    for p in root.rglob("*"):
        if p.is_file():
            items.append(p)
    return sorted(items, key=lambda p: str(p.relative_to(root)).replace("\\", "/"))


def make_zip(src_dir, out_path, prefix: str = "") -> Path:
    """把 src_dir 打成平铺 zip。

    prefix 非空时所有成员名前加 `prefix/`（默认空 = 平铺）。
    """
    src = Path(src_dir)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for p in _iter_files(src):
            rel = str(p.relative_to(src)).replace("\\", "/")
            name = (prefix + "/" + rel) if prefix else rel
            # 只读文件在 Windows 上 zipfile 读取没问题，写入端不需要改属性
            zi = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            with open(p, "rb") as f:
                zf.writestr(zi, f.read())
    return out


def clear_readonly(root) -> int:
    """去掉目录树里所有只读属性（返回处理的文件数）。

    删除安装目录时需要 —— 打包产物里 `base_library.zip` 是只读的，
    `shutil.rmtree` 不处理就删不掉。
    """
    n = 0
    for p in Path(root).rglob("*"):
        try:
            mode = p.stat().st_mode
            if not (mode & stat.S_IWRITE):
                os.chmod(p, mode | stat.S_IWRITE)
                n += 1
        except OSError:
            continue
    return n


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    prefix = ""
    if "--prefix" in argv:
        i = argv.index("--prefix")
        prefix = argv[i + 1]
        del argv[i:i + 2]
    if len(argv) < 2:
        print(__doc__)
        return 2
    src, out = argv[0], argv[1]
    p = make_zip(src, out, prefix=prefix)
    size = p.stat().st_size
    print("zip: %s (%.1f MB, prefix=%r)" % (p, size / 1024 / 1024, prefix))
    return 0


if __name__ == "__main__":
    sys.exit(main())
