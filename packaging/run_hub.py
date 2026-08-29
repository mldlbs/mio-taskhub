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
    """命名互斥锁：确保只有一个 hub 实例（避免重复启动出现多托盘）。

    读取 GetLastError 必须用 WinDLL(use_last_error=True) + ctypes.get_last_error()；
    原实现跨两次 FFI 调用读 kernel32.GetLastError()，错误码会被 ctypes 内部调用覆盖，
    漏判 ALREADY_EXISTS → 多 hub 并发 → 端口冲突 → 无限重启僵尸进程（2026-08-29 实测）。
    """
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.CreateMutexW(None, False, _HUB_LOCK)
        if not handle:
            return 0  # 创建失败：保持旧行为放行（_release_hub_lock 对 0 自动跳过）
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return None  # 已有 hub 实例
        return handle
    except Exception:
        return "unknown"


def _release_hub_lock(lock):
    try:
        if lock and lock != "unknown":
            ctypes.windll.kernel32.CloseHandle(lock)
    except Exception:
        pass


def _probe_service(url) -> bool:
    """探测端口上是自己的服务（返回 JSON 数组/任务特征）。"""
    try:
        import urllib.request

        with urllib.request.urlopen(f"{url}/api/v1/tasks", timeout=2) as r:
            body = r.read(200).decode("utf-8", "replace")
            return r.status == 200 and body.strip().startswith("[")
    except Exception:
        return False


def _reclaim_port(port):
    """端口被残留进程占用（无响应或非本服务）时，杀掉占用者并接管。

    返回 True 表示已清理成功（可重试启动），False 表示无法接管。
    """
    try:
        import urllib.request
        import json as _json
        import subprocess as _sp

        out = _sp.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess"],
            capture_output=True, text=True, timeout=10,
        )
        pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
        for pid in pids:
            if pid <= 0 or pid == os.getpid():
                continue
            info = _sp.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName"],
                capture_output=True, text=True, timeout=10,
            )
            name = info.stdout.strip()
            # 只清理 mio-taskhub 相关进程，绝不接管无关程序
            if name.lower() in ("python", "mio-taskhub", "mio-taskhub.exe"):
                _log(f"reclaim port {port}: kill pid={pid} name={name}")
                try:
                    _sp.run(["taskkill", "/pid", str(pid), "/f"], capture_output=True, timeout=10)
                except Exception:
                    pass
        return True
    except Exception as e:
        _log(f"reclaim port failed: {e!r}")
        return False


def main():
    port = int(os.environ.get("MIO_TASKHUB_PORT", "48620"))
    url = f"http://127.0.0.1:{port}"

    lock = _single_hub_instance()
    if lock is None:
        # 已有 hub 在运行，静默退出（避免出现第二个托盘图标）
        return

    if _port_in_use("127.0.0.1", port):
        if _probe_service(url):
            # 端口上是健康的本服务——互斥锁漏判兜底：已有 hub 在跑，静默退出
            _log(f"port {port} served by healthy hub -> duplicate launch, exit")
            _release_hub_lock(lock)
            return
        # 端口被占用但服务无响应——大概率是残留进程占着端口，清理后接管
        _log(f"port {port} busy, no healthy service -> reclaim")
        if not _reclaim_port(port):
            _msgbox(
                "mio-taskhub",
                f"端口 {port} 已被其他程序占用且无法自动接管。\n\n"
                f"请关闭占用该端口的程序后重试。",
            )
            _release_hub_lock(lock)
            return
        import time as _time

        _time.sleep(2)

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    current = {"server": None}  # 可变的当前 server 引用，托盘/守卫共用
    tray = _start_tray(url, current)
    try:
        # 守卫循环：uvicorn 崩溃/异常后退 2 秒自动重启，托盘持续驻留
        while True:
            server = uvicorn.Server(config)
            current["server"] = server
            try:
                server.run()
                break  # 正常退出（托盘「退出」设 should_exit）
            except (SystemExit, KeyboardInterrupt):
                # uvicorn 端口占用等启动失败会抛 SystemExit(3)：重启只会无限循环
                # 制造僵尸实例（runtime.log 实测 "crashed, restart in 2s: SystemExit(3)"），
                # 改为直接退出走 finally 清理
                _log("hub exit (SystemExit/KeyboardInterrupt), no restart")
                break
            except BaseException as e:
                _log(f"hub crashed, restart in 2s: {e!r}")
                import time as _time

                _time.sleep(2)
                continue
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
