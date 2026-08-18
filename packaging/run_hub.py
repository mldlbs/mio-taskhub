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


def _log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[tray] {msg}\n")
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

    - 菜单「打开面板」→ 启动 widget 浮动窗口（独立进程）
    - 菜单「退出」→ 停托盘 + 请求 uvicorn 优雅退出
    失败时写日志到 console.log 并返回 None（保持仅服务运行）。
    """
    try:
        import pystray
        from PIL import Image
        has = f"pystray={getattr(pystray, '__version__', '?')} PIL={getattr(Image, '__version__', '?')}"
        _log(f"tray deps: {has}")
    except Exception as e:
        _log(f"tray deps import failed: {e!r}")
        return None

    def _open_panel(_icon=None, _item=None):
        # 独立进程启动 widget（webview 事件循环需在各自进程的主线程）。
        # 从 hub 打开的面板不显示自己的托盘（避免出现两个图标）。
        try:
            env = dict(os.environ)
            env["MIO_TASKHUB_WIDGET_NO_TRAY"] = "1"
            if getattr(sys, "frozen", False):
                subprocess.Popen([sys.executable, "widget"], env=env)
            else:
                script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.py")
                subprocess.Popen([sys.executable, script, "widget"], env=env)
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
        _log("tray started")
        return icon
    except Exception as e:
        _log(f"tray start failed: {e!r}")
        return None


def _port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


_HUB_LOCK = "mio-taskhub-hub-instance"
_ERROR_ALREADY_EXISTS = 183


def _single_hub_instance():
    """命名互斥锁：确保只有一个 hub 实例（避免重复启动出现多托盘）。"""
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.CreateMutexW(None, False, _HUB_LOCK)
        if handle and kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
            return None
        return handle
    except Exception:
        return "unknown"


def _release_hub_lock(lock):
    try:
        if lock and lock != "unknown":
            ctypes.windll.kernel32.CloseHandle(lock)
    except Exception:
        pass


def main():
    port = int(os.environ.get("MIO_TASKHUB_PORT", "48620"))
    url = f"http://127.0.0.1:{port}"

    lock = _single_hub_instance()
    if lock is None:
        # 已有 hub 在运行，静默退出（避免出现第二个托盘图标）
        return

    if _port_in_use("127.0.0.1", port):
        _msgbox(
            "mio-taskhub",
            f"端口 {port} 已被占用，可能任务中心已经在运行。\n\n"
            f"请从系统托盘打开面板，或访问 {url}",
        )
        return
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
        _release_hub_lock(lock)


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
