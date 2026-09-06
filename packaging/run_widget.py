# mio-taskhub floating task center panel (置顶浮动任务中心)
# Run: python packaging/run_widget.py
# 策略：用 Edge --app 模式打开任务中心，稳定可靠不依赖 pywebview
import ctypes
import os
import socket
import subprocess
import sys
import threading
import time

PORT = int(os.environ.get("MIO_TASKHUB_PORT", "48620"))

ERROR_ALREADY_EXISTS = 183
_SINGLE_INSTANCE_LOCK = "mio-taskhub-widget-instance"
WINDOW_TITLE = "MIO-TASKHUB · 任务中心"


def _hub_url() -> str:
    return f"http://127.0.0.1:{PORT}/?_={int(time.time())}"


def _res_icon() -> str:
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        cand = os.path.join(base, "web", "public", "icon.ico")
        return cand if os.path.exists(cand) else ""
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "public", "icon.ico")
    return cand if os.path.exists(cand) else ""


ICO = _res_icon()


def _hub_alive() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _single_instance():
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_LOCK)
        if not handle:
            return 0
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:
        return None


def _open_browser(url: str):
    """用 Edge 打开任务中心。降级到默认浏览器。"""
    edge_paths = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    edge_exe = None
    for p in edge_paths:
        if os.path.isfile(p):
            edge_exe = p
            break

    if edge_exe:
        subprocess.Popen(
            [
                edge_exe,
                f"--app={url}",
                "--new-window",
                "--disable-features=msEdgeTranslate",
                "--no-first-run",
                "--disable-gpu",
            ],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    else:
        os.startfile(url)


def _start_tray(on_open, on_quit):
    """系统托盘图标。"""
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None

    def _open(_icon=None, _item=None):
        on_open()

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
            "mio-taskhub-widget",
            img,
            WINDOW_TITLE,
            menu=pystray.Menu(
                pystray.MenuItem("打开任务中心", _open, default=True),
                pystray.MenuItem("退出", _quit),
            ),
        )
        t = threading.Thread(target=icon.run, daemon=True)
        t.start()
        return icon
    except Exception:
        return None


def main():
    if _single_instance() is None:
        return

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

    url = _hub_url()
    _open_browser(url)

    # hub 已在运行时不显示 widget 自己的托盘（避免两个图标）
    no_tray = os.environ.get("MIO_TASKHUB_WIDGET_NO_TRAY") == "1" or _hub_alive()
    tray = _start_tray(
        on_open=lambda: _open_browser(_hub_url()),
        on_quit=lambda: None,
    ) if not no_tray else None

    # 阻塞：等待 hub 关闭
    try:
        while _hub_alive():
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass


if __name__ == "__main__":
    main()
