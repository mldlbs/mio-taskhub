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


def purge_processes_under(root, timeout: float = 10.0) -> bool:
    """强杀 root 目录下的所有进程直至清空；超时仍有残留返回 False。

    **两个必须做对的点**（实测踩过）：

    1. **先杀 supervisor，再杀 worker** —— hub 是 supervisor→worker 双进程，
       supervisor 会在 worker 意外退出时**把它重新拉起来**（退避重试）。
       先杀 worker → supervisor 立刻 spawn 新 worker → 新 worker 又把安装
       目录钉住 → 目录永远删不掉。
       所以：按「父进程在前」排序，先杀父后杀子。

    2. **杀之前重新核对进程身份** —— psutil 的 `Process(pid)` 只缓存了 pid，
       目标若在两次调用之间退出，pid 可能被系统**复用**给别的进程 →
       `taskkill /PID` 就会杀掉无辜进程（实测把用户的 shell 一起杀掉）。
       杀掉前重新读一次 `exe`，与目标路径比对，不符就跳过。
    """
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
        # 父进程在前：先杀 supervisor，再杀 worker（见 docstring 坑 1）
        ordered = sorted(left, key=lambda item: _depth_of(item[0]))
        for pid, exe in ordered:
            if pid == me:
                continue
            # 杀掉前**重新核对身份**（见 docstring 坑 2）：pid 可能已被复用
            if not _still_same_process(pid, exe):
                _journal("skip pid=%s (进程已退出或 pid 被复用)" % pid)
                continue
            # **只杀目标 pid 本身**，不在这里连带杀它的子进程 ——
            # 父进程在前已保证 supervisor 先死，它不会再把 worker 拉起来。
            # （之前先杀子进程，正好给了 supervisor 重启 worker 的窗口。）
            killed = False
            try:
                p = psutil.Process(pid)
                p.kill()
                # 等它真的死掉再往下走 —— 死了 pid 就被系统回收，
                # 下一个进程可能立刻复用它。
                try:
                    p.wait(timeout=3)
                except Exception:  # noqa: BLE001
                    pass
                killed = True
                _journal("kill sent pid=%s" % pid)
            except Exception as e:  # noqa: BLE001
                _journal("kill pid=%s failed: %r" % (pid, e))

            # **psutil 杀成功就不再 taskkill**：进程已经死了，pid 已释放，
            # 这时再 `taskkill /F /PID <pid>` 极可能命中**被复用的新 pid**
            # → 杀掉无辜进程（实测把用户的 shell 一起杀掉，
            #   连带把卸载器自己也带走 → 进程秒退、日志都没有）。
            # 只在 psutil 没杀掉时才用 taskkill 兜底。
            if not killed:
                if not _still_same_process(pid, exe):
                    _journal("skip taskkill pid=%s (已退出)" % pid)
                    continue
                _taskkill_tree(pid)
        if time.monotonic() >= deadline:
            time.sleep(0.5)
            left2 = list_processes_under(root)
            _journal("purge timeout, left=%s" % left2)
            return not left2
        time.sleep(0.3)


def _depth_of(pid: int) -> int:
    """进程的祖先链深度（根 = 0）。取不到就当最深。"""
    depth = 0
    try:
        import psutil
        p = psutil.Process(pid)
        while p.ppid() and depth < 32:
            p = p.parent()
            if p is None:
                break
            depth += 1
    except Exception:  # noqa: BLE001
        return 99
    return depth


def _still_same_process(pid: int, expected_exe) -> bool:
    """pid 现在是否还是「原来那个进程」（防止 pid 复用导致误杀）。

    expected_exe 为 None 时只检查进程是否还活着。
    """
    try:
        import psutil
        p = psutil.Process(pid)
        if not p.is_running():
            return False
        if expected_exe:
            cur = p.exe() or ""
            if not _norm(cur) == _norm(expected_exe):
                return False
        return True
    except Exception:  # noqa: BLE001
        return False


def hub_running() -> bool:
    """本机是否正有 mio-taskhub 实例在跑（按进程名粗判，不依赖安装目录）。"""
    try:
        import psutil
    except ImportError:
        return False
    for info in psutil.process_iter(["name"]):
        try:
            if (info.info.get("name") or "").lower() in ("TaskHub.exe",):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False
