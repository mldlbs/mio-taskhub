# -*- coding: utf-8 -*-
"""安装 / 卸载前的进程清理。

复用 `mio_taskhub.update.apply` 里已验证的 psutil 探测逻辑（同一份语义：
「映像路径位于目标目录下的进程」），避免两套实现漂移。

杀进程用 **psutil + taskkill /T 兜底**：
- hub 是 supervisor→worker 双进程结构，杀掉 supervisor 后 worker 可能被
  Job Object 连带清理，也可能残留 —— `taskkill /T` 按**进程树**清理，更彻底。
- 只用 psutil 时，打包产物（无黑框 exe）里 psutil 的 C 扩展行为偶有差异，
  兜底一层 taskkill 可避免「卸不干净」。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time


def _norm(p: str) -> str:
    """规范化路径用于比较。

    **空串/无目录部分必须原样返回**：某些系统进程（如 Windows 的 Registry）
    的 exe 没有路径，`os.path.realpath("")` 会解析成**当前工作目录** ——
    卸载器恰好在安装目录里跑时，它就会被误判成「占用安装目录的进程」，
    导致永远删不掉。
    """
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.realpath(p))
    except Exception:  # noqa: BLE001
        return os.path.normcase(p)


def _is_under(exe: str, root_s: str) -> bool:
    """exe 是否位于 root 之下。裸文件名（无目录部分）一律判 False。"""
    if not exe:
        return False
    parent = os.path.dirname(exe)
    if not parent:
        return False  # 系统进程常见的裸 exe 名，不是真实安装路径
    return _norm(parent).startswith(root_s)


def list_processes_under(root) -> list:
    """枚举映像路径位于 root 目录下的 (pid, exe)。无 psutil / 无权限时跳过。"""
    try:
        import psutil
    except ImportError:  # noqa: F401
        return []
    root_s = _norm(str(root))
    me = os.getpid()
    # PyInstaller onefile：父进程是 bootloader，**子进程**才真正跑我们的代码，
    # 而 sys.executable 指向的是外层 exe。两者都在 root 下 → 都要排除，
    # 否则「卸载器删不掉自己所在的目录」会永远失败。
    me_exe = _norm(sys.executable) if getattr(sys, "frozen", False) else ""
    out = []
    try:
        iterator = psutil.process_iter(["pid", "exe", "ppid"])
    except Exception:  # noqa: BLE001
        return []
    for info in iterator:
        try:
            pid = int(info.info["pid"])
            if pid == me:
                continue
            exe = info.info.get("exe") or ""
            if not exe:
                continue
            if me_exe and _norm(exe) == me_exe:
                continue  # 自己的 bootloader 父进程
            if _is_under(exe, root_s):
                out.append((pid, exe))
        except Exception:  # noqa: BLE001 —— 进程可能已退出 / 无权限
            continue
    return out


def _debug(msg: str) -> None:
    """调试输出（设 MIO_INSTALLER_DEBUG=1 时写日志）。

    写**日志文件**而不是 stderr —— 无黑框 exe 里 stderr 是 None，
    写它会抛异常。日志路径：%USERPROFILE%\\.mio_taskhub\\installer.log
    """
    if not os.environ.get("MIO_INSTALLER_DEBUG"):
        return
    try:
        from .entry_support import log_path
        with open(log_path("installer.log"), "a", encoding="utf-8") as f:
            f.write("[debug] %s\n" % msg)
    except Exception:  # noqa: BLE001
        pass


def _journal(msg: str) -> None:
    """**无条件**写一条操作日志到 installer.log。

    为什么单独一条通道：进程清理是卸载流程里最容易出问题的一步
    （杀不掉 → 目录删不掉）。出问题时唯一能查的线索就是这个文件，
    所以这里**不设开关** —— 静默失败比多写几行糟糕得多。
    """
    try:
        from .entry_support import log_path
        with open(log_path("installer.log"), "a", encoding="utf-8") as f:
            f.write("[proc] %s\n" % msg)
    except Exception:  # noqa: BLE001
        pass


def _taskkill_tree(pid: int) -> None:
    """强杀指定 pid（**不带 /T**，见下）。失败不抛异常。

    **为什么不用 `taskkill /F /T`**：`/T` 会连带杀掉该 pid 的**整棵子孙树**。
    在真实场景里这会误伤：hub supervisor 常常是从用户的 shell / 脚本里
    启动的，`/T` 顺着父链往上会波及**启动者**（实测把 bash 一起杀掉，
    卸载器连日志都没来得及写就消失了，表现为「进程秒退、目录还在、
    退出码 23」）。

    正确做法：只杀目标 pid 本身（`/F` 不带 `/T`），
    子孙进程由 `psutil` 的 `children(recursive=True)` 单独枚举，
    避免把无关的祖先/兄弟进程卷进来。
    """
    try:
        subprocess.run(
            ["taskkill", "/F", "/PID", str(pid)],
            capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001
        pass


def _child_pids(pid: int) -> list:
    """列出 pid 的**子孙** pid（不含自己）。取不到就返回空表。"""
    out = []
    try:
        import psutil
        p = psutil.Process(pid)
        for c in p.children(recursive=True):
            try:
                out.append(c.pid)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return out


def purge_processes_under(root, timeout: float = 10.0) -> bool:
    """强杀 root 目录下的所有进程直至清空；超时仍有残留返回 False。"""
    me = os.getpid()
    deadline = time.monotonic() + timeout
    try:
        import psutil
    except ImportError:
        return not list_processes_under(root)

    while True:
        left = list_processes_under(root)
        _journal("purge root=%s left=%s me=%s frozen=%s exe=%s"
                 % (root, left, me, getattr(sys, "frozen", False), sys.executable))
        if not left:
            return True
        for pid, _exe in left:
            if pid == me:
                continue
            # 先记下子孙 pid 并**单独杀**（子先父后）——
            # 不靠 taskkill /T（那会连启动者一起杀掉，见 _taskkill_tree 注释）。
            kids = _child_pids(pid)
            for kpid in kids:
                try:
                    psutil.Process(kpid).kill()
                    _journal("kill child pid=%s (of %s)" % (kpid, pid))
                except Exception as e:  # noqa: BLE001
                    _journal("kill child pid=%s failed: %r" % (kpid, e))
            try:
                psutil.Process(pid).kill()
                _journal("kill sent pid=%s" % pid)
            except Exception as e:  # noqa: BLE001
                _journal("kill pid=%s failed: %r" % (pid, e))
            # 兜底：只杀目标 pid 本身
            _taskkill_tree(pid)
        if time.monotonic() >= deadline:
            time.sleep(0.5)
            left2 = list_processes_under(root)
            _journal("purge timeout, left=%s" % left2)
            return not left2
        time.sleep(0.3)


def hub_running() -> bool:
    """本机是否正有 mio-taskhub 实例在跑（按进程名粗判，不依赖安装目录）。"""
    try:
        import psutil
    except ImportError:
        return False
    for info in psutil.process_iter(["name"]):
        try:
            if (info.info.get("name") or "").lower() in ("mio-taskhub.exe",):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False
