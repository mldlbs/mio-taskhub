# mio-taskhub floating task center panel (置顶浮动任务中心)
# Run: python packaging/run_widget.py
import ctypes
import os
import socket
import sys

import webview

PORT = int(os.environ.get("MIO_TASKHUB_PORT", "48620"))
URL = f"http://127.0.0.1:{PORT}/"


def _res_icon() -> str:
    """解析 icon 路径：打包后取 _MEIPASS 内的资源，源码模式取 web/public。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        cand = os.path.join(base, "web", "public", "icon.ico")
        return cand if os.path.exists(cand) else ""
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "public", "icon.ico")
    return cand if os.path.exists(cand) else ""


ICO = _res_icon()

WM_SETICON = 0x0080
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
ICON_SMALL = 0
ICON_BIG = 1


def _apply_icon():
    """给窗口标题栏设置自定义图标（Windows）。"""
    if not ICO:
        return
    try:
        w = webview.windows[0]
        hwnd = w.native.Handle
        hico = ctypes.windll.user32.LoadImageW(None, ICO, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if hico:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hico)
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hico)
    except Exception:
        pass


def _hub_alive() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def main():
    if not _hub_alive():
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                f"mio-taskhub hub 未运行（127.0.0.1:{PORT}）。\n\n请先启动 mio-taskhub，再打开此面板。",
                "mio-taskhub",
                0x10,
            )
        except Exception:
            pass
        return

    webview.create_window(
        "mio-taskhub · 任务中心",
        URL,
        width=1080,
        height=720,
        min_size=(640, 480),
        resizable=True,
        on_top=True,
        background_color="#0f1115",
    )
    webview.start(func=_apply_icon)


if __name__ == "__main__":
    main()
