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
        in_bundle_uninstall = BUNDLE / UNINSTALL_EXE
        if not in_bundle_uninstall.exists():
            problems.append("独立安装包目录缺少 %s" % UNINSTALL_EXE)
        elif live_uninstall.exists() and \
                _sha(in_bundle_uninstall) != _sha(live_uninstall):
            problems.append(
                "独立安装包目录里的 %s 与当前产物不一致 —— "
                "重建后要重新组装 dist/mio-taskhub-installer/" % UNINSTALL_EXE)

    # ── 单文件安装包校验（payload 已合并进 setup.exe）──
    bundled_setup = BUNDLE / SETUP_EXE
    if not BUNDLE.exists():
        problems.append("缺少独立安装包目录：%s" % BUNDLE)
    elif not bundled_setup.exists():
        problems.append("独立安装包目录缺少 %s" % SETUP_EXE)
    else:
        problems.extend(_check_single_file(bundled_setup, live_uninstall))

    return problems


def _check_single_file(bundled_setup: Path, live_uninstall) -> list:
    """校验单文件安装包：内嵌标记 + 内嵌内容与源产物一致。"""
    problems = []
    try:
        from mio_taskhub.installer import embedded
    except Exception as e:  # noqa: BLE001
        return ["无法导入 installer.embedded：%r" % e]

    marker = embedded.read_embedded_marker(bundled_setup)
    if not marker:
        problems.append(
            "单文件安装包 %s **没有内嵌 payload** —— "
            "应运行 scripts/make_setup.py 合并" % bundled_setup.name)
        return problems

    offset, plen = marker
    try:
        extracted = embedded.extract_embedded_payload(bundled_setup)
    except Exception as e:  # noqa: BLE001
        problems.append("解出内嵌 payload 失败：%r" % e)
        return problems
    try:
        if extracted.stat().st_size != plen:
            problems.append("内嵌 payload 长度不符（标记 %d，实际 %d）"
                            % (plen, extracted.stat().st_size))
        with zipfile.ZipFile(extracted) as zf:
            names = set(zf.namelist())
            if "mio-taskhub.exe" not in names:
                problems.append("内嵌 payload 缺少 mio-taskhub.exe")
            if UNINSTALL_EXE in names and live_uninstall and \
                    live_uninstall.exists():
                in_zip = _sha_bytes(zf.read(UNINSTALL_EXE))
                on_disk = _sha(live_uninstall)
                if in_zip != on_disk:
                    problems.append(
                        "内嵌 payload 里的 %s 与当前产物不一致（%s ≠ %s）"
                        % (UNINSTALL_EXE, in_zip[:12], on_disk[:12]))
    except zipfile.BadZipFile as e:
        problems.append("内嵌 payload 不是合法 zip：%r" % e)
    finally:
        try:
            extracted.unlink()
        except OSError:
            pass
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
