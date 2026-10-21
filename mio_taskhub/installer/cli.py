# -*- coding: utf-8 -*-
"""安装 / 卸载的**命令行**入口（无 GUI 环境、CI、脚本化场景）。

用法：
  python -m mio_taskhub.installer.cli install  [--payload ZIP] [--dir DIR] [--no-shortcuts]
  python -m mio_taskhub.installer.cli uninstall [--dir DIR] [--purge-data] [--yes]
  python -m mio_taskhub.installer.cli status
"""
from __future__ import annotations

import argparse
import json
import sys

from . import registry
from .entry_support import emit
from .install import detect_install_dir, install, is_installed, uninstall
from .paths import APP_DISPLAY_NAME, default_install_dir


def _print(text: str) -> None:
    """输出文本。无控制台环境（无黑框 exe）下走日志文件，不抛 NoneType 错误。"""
    emit(text, log_name="installer.log")


def _cmd_install(args) -> int:
    res = install(
        payload_zip=args.payload,
        install_dir=args.dir or default_install_dir(),
        create_shortcuts=not args.no_shortcuts,
        register=not args.no_registry,
    )
    if args.json:
        _print(json.dumps({"ok": res.ok, "message": res.message,
                            "install_dir": res.install_dir, "steps": res.steps},
                           ensure_ascii=False, indent=2))
    else:
        for s in res.steps:
            _print("  [%s] %s%s" % ("OK" if s["ok"] else "!!", s["name"],
                                    (" — " + s["detail"]) if s["detail"] else ""))
        _print(res.message)
    return 0 if res.ok else 1


def _cmd_uninstall(args) -> int:
    if not args.yes:
        _print("将卸载 %s（安装目录：%s）" % (APP_DISPLAY_NAME, detect_install_dir()))
        if args.purge_data:
            _print("警告：--purge-data 会一并删除用户数据（数据库/日志/备份）")
        try:
            answer = input("确认卸载？输入 y 继续：").strip().lower()
        except (EOFError, OSError):
            answer = ""
        if answer not in ("y", "yes"):
            _print("已取消")
            return 2
    res = uninstall(
        install_dir=args.dir or detect_install_dir(),
        remove_data=args.purge_data,
    )
    if args.json:
        _print(json.dumps({"ok": res.ok, "message": res.message, "steps": res.steps},
                          ensure_ascii=False, indent=2))
    else:
        for s in res.steps:
            _print("  [%s] %s%s" % ("OK" if s["ok"] else "!!", s["name"],
                                    (" — " + s["detail"]) if s["detail"] else ""))
        _print(res.message)
    return 0 if res.ok else 1


def _cmd_status(args) -> int:
    d = detect_install_dir()
    info = {
        "installed": is_installed(d),
        "install_dir": str(d),
        "default_install_dir": str(default_install_dir()),
        "registry": registry.read_entry(),
    }
    if args.json:
        _print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        _print("已安装: %s" % info["installed"])
        _print("安装目录: %s" % info["install_dir"])
        for k, v in (info["registry"] or {}).items():
            _print("  %s = %s" % (k, v))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="mio-taskhub-installer",
                                     description="mio-taskhub 安装 / 卸载工具")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    sub = parser.add_subparsers(dest="cmd")

    p_install = sub.add_parser("install", help="安装程序")
    p_install.add_argument("--payload", help="payload zip 路径（默认自动搜索）")
    p_install.add_argument("--dir", help="安装目录（默认 %%LOCALAPPDATA%%\\Programs\\mio-taskhub）")
    p_install.add_argument("--no-shortcuts", action="store_true", help="不创建快捷方式")
    p_install.add_argument("--no-registry", action="store_true", help="不写注册表项")
    p_install.set_defaults(func=_cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="卸载程序")
    p_uninstall.add_argument("--dir", help="安装目录（默认自动探测）")
    p_uninstall.add_argument("--purge-data", action="store_true", help="一并删除用户数据")
    p_uninstall.add_argument("--yes", "-y", action="store_true", help="跳过确认")
    p_uninstall.set_defaults(func=_cmd_uninstall)

    p_status = sub.add_parser("status", help="查看安装状态")
    p_status.set_defaults(func=_cmd_status)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
