# -*- coding: utf-8 -*-
"""安装程序入口（打包为 `mio-taskhub-setup.exe`）。

用法：
  mio-taskhub-setup.exe                 → 图形安装向导
  mio-taskhub-setup.exe --silent        → 静默安装（无 GUI，CI / 批量部署）
  mio-taskhub-setup.exe --uninstall     → 图形卸载向导
  mio-taskhub-setup.exe --uninstall --silent  → 静默卸载
  mio-taskhub-setup.exe --status        → 查看安装状态
  mio-taskhub-setup.exe /S              → 同 --silent（Windows 传统写法）

注意：打包为无黑框 exe 后 `sys.stdout` 为 None，所有输出走
`entry_support.ensure_stdio()` 重绑的日志文件（%~/.mio_taskhub/installer.log）。
"""
from __future__ import annotations

import os
import sys

from mio_taskhub.installer.entry_support import USAGE_SETUP, emit, ensure_stdio


def _payload_beside_me():
    """安装器同目录下的 payload zip（构建时随 exe 一起分发）。"""
    from mio_taskhub.installer.paths import PAYLOAD_ZIP_NAME
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False)
                                            else __file__))
    cand = os.path.join(here, PAYLOAD_ZIP_NAME)
    return cand if os.path.isfile(cand) else None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ensure_stdio()

    if any(a in ("--help", "-h", "/?", "/help") for a in argv):
        emit(USAGE_SETUP)
        return 0

    silent = any(a in ("--silent", "/S", "/silent") for a in argv)
    do_uninstall = any(a in ("--uninstall", "/uninstall", "uninstall") for a in argv)
    do_status = any(a in ("--status", "/status") for a in argv)

    # 被改名成 uninstall.exe 的副本（安装器把自身复制进安装目录当卸载器用）
    # 默认应走**卸载**流程 —— 否则用户双击「卸载」反而触发一次安装。
    if _invoked_as_uninstaller():
        do_uninstall = True

    if do_status:
        from mio_taskhub.installer.cli import main as cli_main
        return cli_main(["status"])

    if do_uninstall:
        if silent:
            from mio_taskhub.installer.cli import main as cli_main
            return cli_main(["uninstall", "--yes"] + _passthrough(argv))
        from mio_taskhub.installer.gui import run_uninstaller
        return run_uninstaller()

    if silent:
        from mio_taskhub.installer.cli import main as cli_main
        return cli_main(["install"] + _passthrough(argv))

    from mio_taskhub.installer.gui import run_installer
    return run_installer(payload_zip=_payload_beside_me())


def _invoked_as_uninstaller() -> bool:
    """当前 exe 是否名为 uninstall.exe（被当作卸载器启动）。"""
    try:
        name = os.path.basename(sys.executable if getattr(sys, "frozen", False)
                                else __file__).lower()
    except Exception:  # noqa: BLE001
        return False
    return name in ("uninstall.exe", "uninstall")


def _passthrough(argv) -> list:
    """把安装/卸载子命令的额外参数透传给 CLI（--dir / --payload / --purge-data）。"""
    allowed = ("--dir", "--payload", "--purge-data", "--no-shortcuts", "--no-registry")
    out = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in allowed:
            out.append(a)
            if a in ("--dir", "--payload") and i + 1 < len(argv):
                out.append(argv[i + 1])
                i += 1
        i += 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
