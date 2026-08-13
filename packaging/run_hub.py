import ctypes
import os
import socket
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


def _port_in_use(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def main():
    port = int(os.environ.get("MIO_TASKHUB_PORT", "8080"))
    if _port_in_use("127.0.0.1", port):
        _msgbox(
            "mio-taskhub",
            f"端口 {port} 已被占用，可能任务中心已经在运行。\n\n"
            f"本窗口可关闭，浏览器将打开 http://127.0.0.1:{port}",
        )
        webbrowser.open(f"http://127.0.0.1:{port}")
        return
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


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
