# -*- coding: utf-8 -*-
"""把 payload zip **合并进** `mio-taskhub-setup.exe` → 单文件安装包。

**为什么合并**：真实世界的安装器（NSIS / Inno Setup）都是**单文件**
`mio-taskhub-setup.exe` —— 用户只下一个文件、双击、装完就走。
原来的形态要用户下两个文件（setup.exe + win64.zip）再放到同一目录，
明显反人类（用户会问「为什么不能直接双击那个 exe」）。

合并方式：把 zip **追加**到 exe 末尾 + 写一个 16 字节尾部标记
（magic + 偏移 + 长度），运行时由 `installer/embedded.py` 解出来。
详见 `mio_taskhub/installer/embedded.py` 的说明。

用法：
    python scripts/make_setup.py --setup dist_installer/mio-taskhub-setup.exe \\
                                 --payload dist/mio-taskhub-win64.zip \\
                                 --out dist/mio-taskhub-installer/mio-taskhub-setup.exe
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from mio_taskhub.installer import embedded  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="合并 payload 到 setup.exe（单文件安装包）")
    ap.add_argument("--setup", required=True, help="PyInstaller 产出的 setup.exe")
    ap.add_argument("--payload", required=True, help="payload zip")
    ap.add_argument("--out", help="输出路径（默认：覆盖 --setup）")
    ap.add_argument("--verify", action="store_true", help="合并后校验内嵌内容")
    args = ap.parse_args(argv)

    setup = Path(args.setup)
    payload = Path(args.payload)
    out = Path(args.out) if args.out else setup

    if not setup.is_file():
        print("[FAIL] 找不到 setup.exe：%s" % setup, file=sys.stderr)
        return 1
    if not payload.is_file():
        print("[FAIL] 找不到 payload：%s" % payload, file=sys.stderr)
        return 1

    # 校验 payload 里有关键内容（别把坏包合并进去）
    try:
        with zipfile.ZipFile(payload) as zf:
            names = set(zf.namelist())
            if "mio-taskhub.exe" not in names:
                print("[FAIL] payload 缺少 mio-taskhub.exe", file=sys.stderr)
                return 1
    except zipfile.BadZipFile as e:
        print("[FAIL] payload 不是合法 zip：%r" % e, file=sys.stderr)
        return 1

    if out != setup:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(setup, out)

    before = out.stat().st_size
    embedded.append_payload(out, payload)
    after = out.stat().st_size

    print("[OK] 单文件安装包: %s" % out)
    print("     %.1f MB (setup %.1f MB + payload %.1f MB)"
          % (after / 1048576, before / 1048576,
             (after - before) / 1048576))

    if args.verify:
        marker = embedded.read_embedded_marker(out)
        if not marker:
            print("[FAIL] 合并后读不到内嵌标记", file=sys.stderr)
            return 1
        offset, plen = marker
        extracted = embedded.extract_embedded_payload(out)
        try:
            with zipfile.ZipFile(extracted) as zf:
                if "mio-taskhub.exe" not in zf.namelist():
                    print("[FAIL] 解出的内嵌包内容不对", file=sys.stderr)
                    return 1
            if extracted.stat().st_size != plen:
                print("[FAIL] 内嵌长度不符", file=sys.stderr)
                return 1
        finally:
            try:
                os.unlink(extracted)
            except OSError:
                pass
        print("     校验通过: offset=%d size=%d" % (offset, plen))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
