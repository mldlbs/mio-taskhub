import ctypes
import os
import socket
import subprocess
import sys
import threading
import traceback
import webbrowser

import uvicorn

from mio_taskhub.main import app

DATA_DIR = os.path.join(os.path.expanduser("~"), ".mio_taskhub")
os.makedirs(DATA_DIR, exist_ok=True)
LOG = os.path.join(DATA_DIR, "runtime.log")
CONSOLE_LOG = os.path.join(DATA_DIR, "console.log")

if sys.stdout is None or sys.stderr is None:
    _f = open(CONSOLE_LOG, "a", encoding="utf-8", buffering=1)
    if sys.stdout is None:
        sys.stdout = _f
    if sys.stderr is None:
        sys.stderr = _f


def _msgbox(title, text):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x10)
    except Exception:
        pass


def _res_icon() -> str:
    """解析 icon 路径：打包后取 _MEIPASS 内的资源，源码模式取 web/public。"""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        cand = os.path.join(base, "web", "public", "icon.ico")
        return cand if os.path.exists(cand) else ""
    cand = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "web", "public", "icon.ico")
    return cand if os.path.exists(cand) else ""


ICO = _res_icon()


def _start_tray(url: str, server_ref: dict):
    """系统托盘驻留：打开浮动面板 / 退出服务。

    - 菜单「打开面板」→ 启动 widget 浮动窗口（run_widget.main，单独线程）
    - 菜单「退出」→ 停托盘 + 请求 uvicorn 优雅退出
    降级：pystray/PIL 不可用或面板启动失败时回退为打开浏览器。
    """
    try:
        import pystray
        from PIL import Image
    except Exception:
        return None

    def _open_panel(_icon=None, _item=None):
        # 独立进程启动 widget（webview 事件循环需在各自进程的主线程）
        try:
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable, "widget"])
            else:
                script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.py")
                subprocess.Popen([sys.executable, script, "widget"])
        except Exception:
            webbrowser.open(url)

    def _quit(_icon=None, _item=None):
        try:
            _icon.stop()
        except Exception:
            pass
        srv = server_ref.get("server")
        if srv is not None:
            srv.should_exit = True

    try:
        if ICO:
            img = Image.open(ICO)
        else:
            img = Image.new("RGB", (32, 32), (61, 220, 151))
        icon = pystray.Icon(
            "mio-taskhub-hub",
            img,
            "MIO-TASKHUB · 任务中心",
            menu=pystray.Menu(
                pystray.MenuItem("打开面板", _open_panel, default=True),
                pystray.MenuItem("退出", _quit),
            ),
        )
        t = threading.Thread(target=icon.run, daemon=True)
        t.start()
        return icon
    except Exception:
        return None


def _port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def main():
    port = int(os.environ.get("MIO_TASKHUB_PORT", "48620"))
    url = f"http://127.0.0.1:{port}"
    if _port_in_use("127.0.0.1", port):
        _msgbox(
            "mio-taskhub",
            f"端口 {port} 已被占用，可能任务中心已经在运行。\n\n"
            f"本窗口可关闭，浏览器将打开 http://127.0.0.1:{port}",
        )
        webbrowser.open(url)
        return
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    tray = _start_tray(url, {"server": server})
    try:
        server.run()
    finally:
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        pass
    except BaseException:
        try:
            with open(LOG, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        _msgbox("mio-taskhub 启动失败", f"启动失败，错误详情已写入：\n{LOG}")
        raise
