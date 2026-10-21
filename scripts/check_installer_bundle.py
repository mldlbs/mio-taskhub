# -*- coding: utf-8 -*-
"""校验分发产物的一致性（安装器 ↔ payload ↔ 独立安装包目录）。

**为什么需要**：安装器（`uninstall.exe`）既被单独分发，又被**打进
payload zip**。这两份极易不同步 —— 实测踩过（2026-10-21）：
重建了 `dist_installer/uninstall.exe` 却没重打 `dist/mio-taskhub-win64.zip`，
于是用户装到机器上的是**旧卸载器**，静默卸载直接异常退出
（rc=23、目录还在、进程秒退）。

只靠 `build.ps1` 的顺序保证是不够的 —— 手工重建单个产物就会打破它。
本脚本把「三个产物必须一致」变成可检查的断言。

用法：
    python scripts/check_installer_bundle.py          # 校验，失败时退出码 1
    python scripts/check_installer_bundle.py --quiet  # 只在失败时输出
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "dist"
INSTALLER_DIST = REPO / "dist_installer"
BUNDLE = DIST / "mio-taskhub-installer"
PAYLOAD_ZIP = DIST / "mio-taskhub-win64.zip"
SETUP_EXE = "mio-taskhub-setup.exe"
UNINSTALL_EXE = "uninstall.exe"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check() -> list:
    """返回问题列表（空列表 = 全部一致）。"""
    problems = []

    live_setup = INSTALLER_DIST / SETUP_EXE
    live_uninstall = INSTALLER_DIST / UNINSTALL_EXE
    for p in (live_setup, live_uninstall):
        if not p.exists():
            problems.append("缺少安装器产物：%s" % p)

    if not PAYLOAD_ZIP.exists():
        problems.append("缺少 payload zip：%s" % PAYLOAD_ZIP)
        return problems

    with zipfile.ZipFile(PAYLOAD_ZIP) as zf:
        names = set(zf.namelist())
        if UNINSTALL_EXE in names and live_uninstall.exists():
            in_zip = _sha_bytes(zf.read(UNINSTALL_EXE))
            on_disk = _sha(live_uninstall)
            if in_zip != on_disk:
                problems.append(
                    "payload zip 里的 %s 是**旧版本**（%s ≠ %s）—— "
                    "重新构建安装器后必须重打 payload zip"
                    % (UNINSTALL_EXE, in_zip[:12], on_disk[:12]))
        elif UNINSTALL_EXE not in names:
            problems.append("payload zip 里没有 %s" % UNINSTALL_EXE)

    if BUNDLE.exists():
        for name, live in ((SETUP_EXE, live_setup),
                           (UNINSTALL_EXE, live_uninstall)):
            in_bundle = BUNDLE / name
            if not in_bundle.exists():
                problems.append("独立安装包目录缺少 %s" % name)
            elif live.exists() and _sha(in_bundle) != _sha(live):
                problems.append(
                    "独立安装包目录里的 %s 与当前产物不一致 —— "
                    "重建后要重新组装 dist/mio-taskhub-installer/" % name)
        in_bundle_zip = BUNDLE / PAYLOAD_ZIP.name
        if not in_bundle_zip.exists():
            problems.append("独立安装包目录缺少 payload zip")
        elif in_bundle_zip.exists() and PAYLOAD_ZIP.exists():
            if _sha(in_bundle_zip) != _sha(PAYLOAD_ZIP):
                problems.append(
                    "独立安装包目录里的 payload zip 与 dist/ 下的不一致 —— "
                    "重建后要重新组装 dist/mio-taskhub-installer/")

    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="校验安装器分发产物一致性")
    ap.add_argument("--quiet", action="store_true", help="只在失败时输出")
    args = ap.parse_args(argv)

    problems = check()
    if problems:
        print("[FAIL] 安装器分发产物不一致：", file=sys.stderr)
        for p in problems:
            print("  - %s" % p, file=sys.stderr)
        print("", file=sys.stderr)
        print("修复：重跑 packaging/build.ps1（会重打 zip 并重新组装安装包目录）",
              file=sys.stderr)
        return 1
    if not args.quiet:
        print("[OK] 安装器分发产物一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
