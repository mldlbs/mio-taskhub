# -*- coding: utf-8 -*-
"""卸载程序入口（打包为 `uninstall.exe`，随 payload 一起分发到安装目录）。

用法：
  uninstall.exe            → 图形卸载向导
  uninstall.exe --silent   → 静默卸载（保留用户数据）
  uninstall.exe --silent --purge-data  → 静默卸载并删除用户数据
  uninstall.exe /S         → 同 --silent

注意：打包为无黑框 exe 后 `sys.stdout` 为 None，所有输出走
`entry_support.ensure_stdio()` 重绑的日志文件（%~/.mio_taskhub/installer.log）。
"""
from __future__ import annotations

import sys

from mio_taskhub.installer.entry_support import USAGE_UNINSTALL, emit, ensure_stdio


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ensure_stdio()

    if any(a in ("--help", "-h", "/?", "/help") for a in argv):
        emit(USAGE_UNINSTALL)
        return 0

    silent = any(a in ("--silent", "/S", "/silent") for a in argv)
    purge = any(a in ("--purge-data", "/purge-data") for a in argv)

    if silent:
        from mio_taskhub.installer.cli import main as cli_main
        args = ["uninstall", "--yes"]
        if purge:
            args.append("--purge-data")
        return cli_main(args)

    from mio_taskhub.installer.gui import run_uninstaller
    return run_uninstaller()


if __name__ == "__main__":
    raise SystemExit(main())
