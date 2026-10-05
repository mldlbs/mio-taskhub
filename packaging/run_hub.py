import ctypes
from ctypes import wintypes
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import webbrowser

# uvicorn 与业务 app 只在 _worker_main 内 import：supervisor 进程不承载
# 业务 import（NFR-4：无 uvicorn/调度器/数据库连接）。

DATA_DIR = os.path.join(os.path.expanduser("~"), ".mio_taskhub")
os.makedirs(DATA_DIR, exist_ok=True)
LOG = os.path.join(DATA_DIR, "runtime.log")
CONSOLE_LOG = os.path.join(DATA_DIR, "console.log")
WINDOW_TITLE = "MIO·HUB — 任务总线"

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


def _log(msg, role="tray"):
    """按角色写 runtime.log：[tray]/[supervisor]/[worker]（NFR-6 日志可归因）。"""
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{role}] {msg}\n")
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


def _update_menu_label(_item=None) -> str:
    """托盘「更新」项的动态文案（按 UpdateService 状态计算）。"""
    try:
        from mio_taskhub.update.service import get_service
        st = get_service().status()
        state = st.get("state")
        if state == "available":
            return "立即更新 (v%s)" % st.get("latest")
        if state == "ready":
            return "重启并应用更新"
        if state == "downloading":
            return "下载中 %d%%" % st.get("progress", 0)
        if state == "needs_manual":
            return "需手动更新 (v%s)" % st.get("latest")
    except Exception:  # noqa: BLE001
        pass
    return "检查更新"


def _notify(icon, msg):
    try:
        if icon is not None:
            icon.notify(msg, "mio-taskhub")
    except Exception:  # noqa: BLE001
        pass


_update_busy = threading.Event()


def _on_update_clicked(icon=None, _item=None):
    """托盘点击：在**工作线程**里跑 check→download→apply，避免冻结托盘消息循环。"""
    from mio_taskhub.update.service import get_service
    svc = get_service()
    try:
        state = svc.status().get("state")
    except Exception as e:  # noqa: BLE001
        _log("tray: update status failed: %r" % e)
        return

    if state == "ready":
        steps = ["apply"]
    elif state in ("available", "check_failed", "up_to_date", "idle", "dismissed"):
        steps = ["check", "download", "apply"]
    else:
        return

    if _update_busy.is_set():      # 防重入：下载/应用中再点不重复触发
        _notify(icon, "更新正在进行中…")
        return
    _update_busy.set()

    def _work():
        try:
            if "check" in steps:
                svc.check()
            if "download" in steps and svc.status().get("state") == "available":
                svc.download()
            if "apply" in steps and svc.status().get("state") == "ready":
                svc.apply()
            st = svc.status()
            if st.get("state") == "failed":
                _notify(icon, "更新失败：%s" % (st.get("error") or "见 apply.log"))
        except Exception as e:  # noqa: BLE001
            _log("tray: update action failed: %r" % e)
            _notify(icon, "更新失败：%s" % e)
        finally:
            _update_busy.clear()

    threading.Thread(target=_work, daemon=True, name="update-action").start()


def _start_tray(url: str, exit_controller):
    """系统托盘驻留：打开浮动面板 / 退出服务（托盘只在 supervisor 进程创建）。

    - 菜单「打开面板」→ 启动 widget 浮动窗口（独立进程）
    - 菜单「退出」→ 停托盘 + exit_controller.request()（优雅退出收敛，FR-4）
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

    _tray_lock = threading.Lock()
    _tray_created = False

    _open_pid = [None]  # track the opened Edge window PID

    def _find_edge_window():
        """查找已打开的 MIO-TASKHUB Edge 窗口，返回 hwnd 或 None"""
        user32 = ctypes.windll.user32
        found = []
        WNDENUMPROC2 = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def _cb(hwnd, _):
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if "MIO" in buf.value and "HUB" in buf.value:
                pid = ctypes.wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found.append((hwnd, pid.value))
            return True
        user32.EnumWindows(WNDENUMPROC2(_cb), 0)
        # 检查进程是否还活着
        kernel32 = ctypes.windll.kernel32
        for hwnd, pid in found:
            proc = kernel32.OpenProcess(0x1000, False, pid)
            if proc:
                kernel32.CloseHandle(proc)
                return hwnd
        return None

    def _open_panel(_icon=None, _item=None):
        _log("tray: _open_panel called")
        # 如果窗口已打开，尝试前置
        existing = _find_edge_window()
        if existing:
            _log(f"tray: reusing existing window hwnd={existing}")
            user32 = ctypes.windll.user32
            user32.SetForegroundWindow(existing)
            user32.ShowWindow(existing, 9)  # SW_RESTORE
            return

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
        _log(f"tray: edge_exe={edge_exe}")
        try:
            if edge_exe:
                sw = ctypes.windll.user32.GetSystemMetrics(0)
                sh = ctypes.windll.user32.GetSystemMetrics(1)
                ww, wh = 1920, 1080
                x = max(0, (sw - ww) // 2)
                y = max(0, (sh - wh) // 2)
                # 用 SW_HIDE 启动 Edge，窗口创建时不可见，调好位置再显示
                cmd = [edge_exe, f"--app={url}", "--new-window",
                       f"--window-position={x},{y}", f"--window-size={ww},{wh}",
                       "--no-first-run"]
                _log(f"tray: launching Edge hidden: {cmd}")
                si = subprocess.STARTUPINFO()
                si.dwFlags = subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0  # SW_HIDE
                proc = subprocess.Popen(cmd, startupinfo=si)
                _open_pid[0] = proc.pid

                def _show_later():
                    user32 = ctypes.windll.user32
                    hwnd = None
                    for _ in range(40):
                        time.sleep(0.15)
                        hwnd = user32.FindWindowW(None, WINDOW_TITLE)
                        if hwnd:
                            break
                        found = []
                        def _cb(h, _):
                            length = user32.GetWindowTextLengthW(h)
                            if length <= 0:
                                return True
                            buf = ctypes.create_unicode_buffer(length + 1)
                            user32.GetWindowTextW(h, buf, length + 1)
                            if "MIO" in buf.value and "HUB" in buf.value:
                                found.append(h)
                            return True
                        WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
                        user32.EnumWindows(WNDPROC(_cb), 0)
                        if found:
                            hwnd = found[0]
                            break
                    if hwnd:
                        SWP_NOZORDER = 0x0004
                        SWP_SHOWWINDOW = 0x0040
                        # 确保位置和大小正确，然后显示
                        user32.SetWindowPos(hwnd, 0, x, y, ww, wh, SWP_NOZORDER | SWP_SHOWWINDOW)
                        user32.ShowWindow(hwnd, 5)  # SW_SHOW
                        _log(f"tray: placed window {ww}x{wh} at ({x},{y})")
                    else:
                        _log("tray: could not find Edge window")

                threading.Thread(target=_show_later, daemon=True).start()
            else:
                _log("tray: Edge not found, falling back to webbrowser")
                webbrowser.open(url)
        except Exception as e:
            _log(f"tray: Edge launch failed: {e!r}")
            webbrowser.open(url)

    def _quit(_icon=None, _item=None):
        try:
            _icon.stop()
        except Exception:
            pass
        # 退出收敛单入口：置位命名事件 → worker drain ≤3s → 超时强杀（FR-4）
        if exit_controller is not None:
            exit_controller.request()

    try:
        # 防止重复创建托盘图标：枚举所有窗口查找同名类
        import ctypes
        import ctypes.wintypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        found_hwnd = [None]
        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def _enum_cb(hwnd, _):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if "mio-taskhub" in buf.value and "SystemTrayIcon" in buf.value:
                # 检查窗口所属进程是否还活着
                pid = ctypes.wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                proc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
                if proc:
                    kernel32.CloseHandle(proc)
                    found_hwnd[0] = hwnd
                    _log(f"tray: found alive icon window hwnd={hwnd} pid={pid.value}")
                    return False
                else:
                    _log(f"tray: found dead icon window hwnd={hwnd} pid={pid.value}, will replace")
            return True
        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        if found_hwnd[0]:
            _log("tray: existing alive icon found, skip creation")
            return None

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
                pystray.MenuItem(
                    lambda item: _update_menu_label(),
                    _on_update_clicked),
                pystray.MenuItem("退出", _quit),
            ),
        )
        t = threading.Thread(target=icon.run, daemon=True)
        t.start()
        _log("tray started")

        def _notify_loop():
            import time as _t
            last = None
            last_label = _update_menu_label()
            while True:
                _t.sleep(30)
                try:
                    from mio_taskhub.update.service import get_service
                    st = get_service().status()
                    if st.get("state") == "available" and st.get("latest") != last:
                        last = st.get("latest")
                        icon.notify("发现新版本 v%s，点击托盘图标更新" % st.get("latest"), "mio-taskhub")
                    # win32 后端菜单只在创建时求值一次，动态文案需主动刷新
                    label = _update_menu_label()
                    if label != last_label:
                        last_label = label
                        # 已知低概率竞态：若此刻菜单正打开，win32 后端跨线程 update_menu
                        # 可能短暂异常/失效。pystray 无 marshalling API，暂接受（下轮刷新自愈）。
                        icon.update_menu()
                except Exception:  # noqa: BLE001
                    pass

        threading.Thread(target=_notify_loop, daemon=True).start()
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
            capture_output=True, text=True, encoding='utf-8', timeout=10,
        )
        pids = [int(x) for x in out.stdout.split() if x.strip().isdigit()]
        for pid in pids:
            if pid <= 0 or pid == os.getpid():
                continue
            info = _sp.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName"],
                capture_output=True, text=True, encoding='utf-8', timeout=10,
            )
            name = info.stdout.strip()
            # 只清理 mio-taskhub 相关进程，绝不接管无关程序
            if name.lower() in ("python", "mio-taskhub", "mio-taskhub.exe"):
                _log(f"reclaim port {port}: kill pid={pid} name={name}", role="supervisor")
                try:
                    _sp.run(["taskkill", "/pid", str(pid), "/f"], capture_output=True, timeout=10)
                except Exception:
                    pass
        return True
    except Exception as e:
        _log(f"reclaim port failed: {e!r}", role="supervisor")
        return False


# ── supervisor / worker 双进程（516bd0c7）────────────────────────────────────
# supervisor：单实例锁 / 端口探测回收 / 托盘 / spawn+监视 worker / 退出收敛；
# worker：uvicorn Server 守卫循环 + apscheduler + 后台线程（FR-1）。

BACKOFF = [0, 2, 5, 15]        # FR-2 崩溃重启退避秒数（封顶 15s）
COOLDOWN_STREAK = 3            # FR-2 连续相同非 0 码进入冷却的次数
COOLDOWN_SECONDS = 60          # FR-2 冷却时长
DRAIN_TIMEOUT_S = 3            # FR-4 优雅退出等待 worker drain 的兜底超时
CREATE_SUSPENDED = 0x00000004
WAIT_OBJECT_0 = 0
SYNCHRONIZE = 0x00100000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
    )]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_K32 = None


def _k32():
    """kernel32 句柄级 API：必须配 argtypes/restype（64 位句柄默认 c_int 会截断）。"""
    global _K32
    if _K32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.CreateProcessW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
            wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.c_void_p, ctypes.c_void_p]
        k.CreateProcessW.restype = wintypes.BOOL
        k.ResumeThread.argtypes = [wintypes.HANDLE]
        k.ResumeThread.restype = wintypes.DWORD
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k.GetExitCodeProcess.restype = wintypes.BOOL
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.CreateEventW.argtypes = [
            ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        k.CreateEventW.restype = wintypes.HANDLE
        k.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        k.OpenEventW.restype = wintypes.HANDLE
        k.SetEvent.argtypes = [wintypes.HANDLE]
        k.SetEvent.restype = wintypes.BOOL
        _K32 = k
    return _K32


def _exit_event_name(port) -> str:
    """退出通知命名事件：端口后缀防多实例互扰（FR-4；不用 HTTP 退出端点）。"""
    return f"Local\\mio-taskhub-worker-exit-{port}"


class _ExitController:
    """退出收敛单入口（FR-4）：托盘「退出」与更新回调都进 request()。

    - expected_exit：主动退出标志（supervisor 收敛，不进退避重启）
    - stop：中断退避/冷却等待
    - 命名事件通知 worker drain；worker 句柄等 drain ≤3s，超时 TerminateProcess 兜底
    - hlock：保护 worker 句柄的等待/关闭竞态（评审风险 R5，防句柄泄漏 WaitFor 失效）
    """

    def __init__(self, port: int):
        self.port = port
        self.expected_exit = False
        self.stop = threading.Event()
        self.hlock = threading.Lock()
        self._worker = None
        self._event = None
        try:
            ev = _k32().CreateEventW(None, True, False, _exit_event_name(port))
            if not ev:
                _log("exit event create failed err=%d" % ctypes.get_last_error(),
                     role="supervisor")
            self._event = ev
        except Exception as e:  # noqa: BLE001
            _log("exit event create failed: %r" % e, role="supervisor")

    def set_worker(self, handle) -> None:
        with self.hlock:
            self._worker = handle

    def wait(self, seconds: float) -> bool:
        """可中断的退避/冷却等待；True=退出请求已到，应立即收敛。"""
        return self.stop.wait(seconds)

    def request(self) -> None:
        """优雅退出：置位事件 → 等 worker drain ≤3s → 超时 TerminateProcess 兜底。"""
        with self.hlock:
            self.expected_exit = True
            self.stop.set()
            if self._event:
                try:
                    _k32().SetEvent(self._event)
                except Exception:  # noqa: BLE001
                    pass
            h = self._worker
            if h is None:
                return
            k32 = _k32()
            if k32.WaitForSingleObject(h, DRAIN_TIMEOUT_S * 1000) != WAIT_OBJECT_0:
                _log("drain timeout %ds -> terminate worker" % DRAIN_TIMEOUT_S,
                     role="supervisor")
                try:
                    k32.TerminateProcess(h, 1)
                except Exception:  # noqa: BLE001
                    pass
                k32.WaitForSingleObject(h, 2000)

    def close(self) -> None:
        with self.hlock:
            for h in (self._worker, self._event):
                if h:
                    try:
                        _k32().CloseHandle(h)
                    except Exception:  # noqa: BLE001
                        pass
            self._worker = None
            self._event = None


def _create_job():
    """FR-3 防孤儿：Job Object + KILL_ON_JOB_CLOSE（supervisor 死 → 内核连带杀 worker）。
    任一步失败 → CloseHandle + warn 降级返回 None（不阻断启动，_reclaim_port 兜底）。"""
    k32 = _k32()
    try:
        job = k32.CreateJobObjectW(None, None)
        if not job:
            _log("job object degraded: CreateJobObjectW err=%d"
                 % ctypes.get_last_error(), role="supervisor")
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            _log("job object degraded: SetInformationJobObject err=%d"
                 % ctypes.get_last_error(), role="supervisor")
            k32.CloseHandle(job)
            return None
        return job
    except Exception as e:  # noqa: BLE001
        _log("job object degraded: %r" % e, role="supervisor")
        return None


def _assign_job(job, handle) -> bool:
    """worker 归入 job；失败 warn 降级但不阻断（评审风险 R1）。"""
    if job is None:
        return False
    if _k32().AssignProcessToJobObject(job, handle):
        return True
    _log("job object degraded: AssignProcessToJobObject err=%d"
         % ctypes.get_last_error(), role="supervisor")
    return False


def _worker_argv() -> list:
    """worker 命令行（FR-1）：冻结态复用自身 exe，源码态走 run.py 同一分派入口。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "hub", "--worker"]
    run_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run.py")
    return [sys.executable, run_py, "hub", "--worker"]


def _spawn_worker(job):
    """CreateProcessW(CREATE_SUSPENDED) → _assign_job → ResumeThread（FR-1/FR-3）。
    返回 (process_handle, pid)；spawn 失败返回 (None, 0)。"""
    k32 = _k32()
    cmd = subprocess.list2cmdline(_worker_argv())
    buf = ctypes.create_unicode_buffer(cmd)
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(_STARTUPINFOW)
    pi = _PROCESS_INFORMATION()
    try:
        ok = k32.CreateProcessW(None, buf, None, None, False, CREATE_SUSPENDED,
                                None, None, ctypes.byref(si), ctypes.byref(pi))
    except Exception as e:  # noqa: BLE001
        _log("spawn worker failed: %r" % e, role="supervisor")
        return None, 0
    if not ok:
        _log("spawn worker failed err=%d cmd=%s" % (ctypes.get_last_error(), cmd),
             role="supervisor")
        return None, 0
    _assign_job(job, pi.hProcess)
    k32.ResumeThread(pi.hThread)
    k32.CloseHandle(pi.hThread)
    return pi.hProcess, pi.dwProcessId


def _supervisor_loop(controller, job, icon) -> None:
    """spawn → 等待 → 分支判定 → 退避/冷却重启 → 收敛退出（FR-1/FR-2）。"""
    k32 = _k32()
    handle = None
    pid = 0
    attempt = 0
    streak = 0
    last_code = None
    try:
        while True:
            if handle is None:
                if controller.stop.is_set():
                    break
                # 每次重启前 spawn 会生成新句柄；旧句柄在收尸分支已 CloseHandle（评审 R5）
                handle, pid = _spawn_worker(job)
                if handle is None:
                    _log("spawn worker failed, retry in 2s", role="supervisor")
                    if controller.wait(2):
                        break
                    continue
                controller.set_worker(handle)
                _log("spawn worker pid=%d" % pid, role="supervisor")
            k32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
            code = wintypes.DWORD(0)
            k32.GetExitCodeProcess(handle, ctypes.byref(code))
            code = int(code.value)
            with controller.hlock:
                if controller._worker is handle:
                    controller._worker = None
                k32.CloseHandle(handle)
            handle = None
            if controller.expected_exit or code == 0:
                # 主动退出 / worker 正常结束：收敛退出，不重启（FR-2 防僵尸循环）
                _log("worker exit code=%d -> converge exit (expected_exit=%s)"
                     % (code, controller.expected_exit), role="supervisor")
                break
            if code == last_code:
                streak += 1
            else:
                streak = 1
                last_code = code
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            if streak >= COOLDOWN_STREAK:
                _log("cooldown after %d identical failures, sleep %ds"
                     % (COOLDOWN_STREAK, COOLDOWN_SECONDS), role="supervisor")
                _notify(icon, "hub 重启连续失败，%d 秒冷却后继续重试" % COOLDOWN_SECONDS)
                streak = 0
                last_code = None
                delay = COOLDOWN_SECONDS
            _log("restart backoff=%ds code=%d" % (delay, code), role="supervisor")
            if controller.wait(delay):
                break
    finally:
        if handle is not None:
            with controller.hlock:
                if controller._worker is handle:
                    controller._worker = None
                try:
                    k32.CloseHandle(handle)
                except Exception:  # noqa: BLE001
                    pass


def _watch_exit_event(port: int, current: dict) -> None:
    """worker 侧 drain 监听（FR-4）：等 supervisor 置位命名事件 → 优雅退出。"""
    k32 = _k32()
    try:
        ev = k32.OpenEventW(SYNCHRONIZE, False, _exit_event_name(port))
    except Exception as e:  # noqa: BLE001
        _log("exit event open failed: %r" % e, role="worker")
        return
    if not ev:
        _log("exit event open failed err=%d, drain 由 supervisor 兜底强杀"
             % ctypes.get_last_error(), role="worker")
        return
    try:
        k32.WaitForSingleObject(ev, 0xFFFFFFFF)  # INFINITE：等到置位
        current["exit"] = True
        srv = current.get("server")
        if srv is not None:
            srv.should_exit = True
        _log("exit event signaled -> should_exit=true", role="worker")
    finally:
        try:
            k32.CloseHandle(ev)
        except Exception:  # noqa: BLE001
            pass


def _worker_main(port: int):
    """worker 分支（FR-1/FR-4）：uvicorn 守卫循环 + 命名事件 drain 监听。

    不创建托盘/单实例锁、不做端口回收（运维静态用例守住，见 spec §3）；
    `_stdio()` 已由 run.py 在分派前完成（windowed stdout 重绑）。
    """
    import uvicorn

    from mio_taskhub.main import app

    _log("started pid=%d port=%d" % (os.getpid(), port), role="worker")

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    current = {"server": None, "exit": False}  # 可变引用：事件线程/守卫/更新回调共用

    from mio_taskhub.update.runner import set_exit_callback

    def _request_hub_exit():
        # HTTP /api/v1/update/apply 在 worker 进程执行：请求本进程优雅退出，
        # supervisor 见退出码 0 收敛（FR-2），updater 随后整树替换。
        current["exit"] = True
        srv = current.get("server")
        if srv is not None:
            srv.should_exit = True

    set_exit_callback(_request_hub_exit)
    threading.Thread(target=_watch_exit_event, args=(port, current),
                     daemon=True, name="worker-exit-event").start()

    # 守卫循环：uvicorn 崩溃/异常后退 2 秒自动重启（既有语义原样迁移）
    while True:
        server = uvicorn.Server(config)
        current["server"] = server
        if current["exit"]:
            server.should_exit = True
        try:
            server.run()
            _log("hub run returned normally (should_exit=true or clean exit)",
                 role="worker")
            break
        except (SystemExit, KeyboardInterrupt) as e:
            # uvicorn 端口占用等启动失败会抛 SystemExit(3)：重启只会无限循环
            # 制造僵尸实例（runtime.log 实测 "crashed, restart in 2s: SystemExit(3)"），
            # 改为直接退出：退出码 0 → supervisor 收敛不重启（FR-2）
            _log("hub exit (SystemExit/KeyboardInterrupt) type=%s code=%s, no restart"
                 % (type(e).__name__, getattr(e, "code", None)), role="worker")
            break
        except BaseException as e:
            _log("hub crashed, restart in 2s: type=%s e=%r" % (type(e).__name__, e),
                 role="worker")
            time.sleep(2)
            continue


def main(worker: bool = False):
    """入口双分支（FR-1）：worker=True → 仅 uvicorn 守卫循环；否则 supervisor 生命周期。"""
    port = int(os.environ.get("MIO_TASKHUB_PORT", "48620"))
    if worker:
        return _worker_main(port)

    url = f"http://127.0.0.1:{port}"

    lock = _single_hub_instance()
    if lock is None:
        # 已有 hub 在运行，静默退出（避免出现第二个托盘图标）
        return

    if _port_in_use("127.0.0.1", port):
        if _probe_service(url):
            # 端口上是健康的本服务——互斥锁漏判兜底：已有 hub 在跑，静默退出
            _log(f"port {port} served by healthy hub -> duplicate launch, exit",
                 role="supervisor")
            _release_hub_lock(lock)
            return
        # 端口被占用但服务无响应——大概率是残留进程占着端口，清理后接管
        _log(f"port {port} busy, no healthy service -> reclaim", role="supervisor")
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

    exit_controller = _ExitController(port)

    from mio_taskhub.update.runner import set_exit_callback
    # 更新回调注册在 supervisor（评审决策⑥）：动作=退出收敛单入口 request()
    set_exit_callback(exit_controller.request)

    tray = _start_tray(url, exit_controller)
    # 托盘「更新」状态机与 worker 进程的 UpdateService 单例互相独立：
    # supervisor 自起 check-only 后台线程，保证托盘「发现新版本」提示行为等价（FR-5）
    try:
        from mio_taskhub.update.service import get_service
        _interval = float(os.environ.get("MIO_UPDATE_INTERVAL_H", "6") or 6)
        get_service().start_background(delay=20.0, interval_h=_interval)
    except Exception as e:  # noqa: BLE001
        _log("update background check start failed: %r" % e, role="supervisor")

    job = None
    try:
        job = _create_job()
        _supervisor_loop(exit_controller, job, tray)
    finally:
        # 退出顺序（spec 退出路径）：停托盘 → 关 worker/事件句柄 → 关 job（连带杀残留）
        # → 释放单实例锁 → supervisor 自身退出
        if tray is not None:
            try:
                tray.stop()
            except Exception:  # noqa: BLE001
                pass
        exit_controller.close()
        if job is not None:
            try:
                _k32().CloseHandle(job)
            except Exception:  # noqa: BLE001
                pass
        _release_hub_lock(lock)
        _log("supervisor exit", role="supervisor")


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
