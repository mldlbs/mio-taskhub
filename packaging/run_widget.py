# mio-taskhub floating task center panel (置顶浮动任务中心)
# Run: python packaging/run_widget.py
import ctypes
import os
import socket
import sys
import threading

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


def _start_tray(window, on_quit):
    """在独立线程启动系统托盘图标。返回 Icon 对象。

    - 单击/菜单「显示面板」→ window.show()
    - 菜单「退出」→ on_quit()（停托盘 + 销毁窗口）
    降级：pystray/PIL 不可用时不驻留托盘（保持现状行为）。
    """
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None

    def _show(_icon=None, _item=None):
        try:
            window.show()
        except Exception:
            pass

    def _quit(_icon=None, _item=None):
        try:
            _icon.stop()
        except Exception:
            pass
        on_quit()

    try:
        if ICO:
            img = Image.open(ICO)
        else:
            img = Image.new("RGB", (32, 32), (61, 220, 151))
        icon = pystray.Icon(
            "mio-taskhub",
            img,
            "MIO-TASKHUB · 任务中心",
            menu=pystray.Menu(
                pystray.MenuItem("显示面板", _show, default=True),
                pystray.MenuItem("退出", _quit),
            ),
        )
        t = threading.Thread(target=icon.run, daemon=True)
        t.start()
        return icon
    except Exception:
        return None


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

    window = webview.create_window(
        "MIO-TASKHUB · 任务中心",
        URL,
        width=1080,
        height=720,
        min_size=(640, 480),
        resizable=True,
        on_top=True,
        background_color="#0f1115",
    )

    quit_flag = {"done": False}

    def _on_quit():
        quit_flag["done"] = True
        try:
            window.destroy()
        except Exception:
            pass

    # 拦截关窗：隐藏到托盘而非退出（若托盘可用）
    tray = _start_tray(window, _on_quit)
    if tray is not None:
        def _on_closing():
            window.hide()
            return False  # 取消关闭
        window.events.closing += _on_closing

    webview.start(func=_apply_icon)
    # webview.start 返回（窗口被 destroy 或进程退出）——若托盘还在则停止
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
