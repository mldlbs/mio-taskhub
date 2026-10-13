# -*- coding: utf-8 -*-
"""hub supervisor/worker 双进程结构静态回归（task 516bd0c7，FR-1~FR-6）。

spec 测试计划 7 用例：源码文本 + importlib 加载断言，不启动真进程。
"""
import importlib.util
import inspect
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_PY = ROOT / "packaging" / "run.py"
RUN_HUB = ROOT / "packaging" / "run_hub.py"

_MOD = None


def _src() -> str:
    return RUN_HUB.read_text(encoding="utf-8")


def _load_run_hub():
    """加载 run_hub 模块（顶层无业务 import，加载即安全，NFR-4 的间接守卫）。"""
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("run_hub_under_test", RUN_HUB)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def test_dispatch_passes_worker_flag():
    """FR-1：run.py 的 hub 分派识别 --worker 并透传给 run_hub.main。"""
    src = RUN_PY.read_text(encoding="utf-8")
    assert '"--worker"' in src
    assert "hub_main(worker=" in src


def test_supervisor_spawn_and_wait():
    """FR-1/FR-2：supervisor 以 CreateProcessW 拉起 `hub --worker`、等待收尸、关句柄。"""
    src = _src()
    assert "CreateProcessW(" in src
    assert '"hub", "--worker"' in src
    assert "WaitForSingleObject(" in src
    assert "GetExitCodeProcess(" in src
    assert "CloseHandle(" in src


def test_backoff_policy():
    """FR-2：退避 [0,2,5,15] 封顶 + 同码 3 次冷却 60s + expected/退出码 0 不重启。"""
    src = _src()
    assert re.search(r"BACKOFF\s*=\s*\[\s*0\s*,\s*2\s*,\s*5\s*,\s*15\s*\]", src)
    assert re.search(r"COOLDOWN_STREAK\s*=\s*3", src)
    assert re.search(r"COOLDOWN_SECONDS\s*=\s*60", src)
    assert "controller.expected_exit or code == 0" in src
    assert re.search(r"streak\s*>=\s*COOLDOWN_STREAK", src)


def test_job_object_usage():
    """FR-3：Job Object KILL_ON_JOB_CLOSE + BREAKAWAY_OK；Assign/SetInformation 失败降级 warn 不阻断。"""
    src = _src()
    assert "CreateJobObjectW(" in src
    assert "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE" in src
    assert "JOB_OBJECT_LIMIT_BREAKAWAY_OK" in src
    assert "AssignProcessToJobObject(" in src
    assert "job object degraded" in src


def test_job_breakaway_ok_set_on_real_job():
    """回归（2026-10-06「下载完安装不了」）：真建 Job 并 Query —— 必须同时含
    KILL_ON_JOB_CLOSE（FR-3 防孤儿）与 BREAKAWAY_OK（updater 脱离，否则 hub 退出
    CloseHandle(job) 时被内核连带杀死）。"""
    import ctypes
    from ctypes import wintypes
    mod = _load_run_hub()
    job = mod._create_job()
    assert job, "CreateJobObjectW failed"
    try:
        k32 = mod._k32()
        # QueryInformationJobObject 未在 _k32() 里绑定签名：显式绑定避免 64 位句柄截断
        k32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        k32.QueryInformationJobObject.restype = wintypes.BOOL
        info = mod._JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        ret = wintypes.DWORD(0)
        ok = k32.QueryInformationJobObject(
            job, mod.JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(ret))
        assert ok, "QueryInformationJobObject err=%d" % ctypes.get_last_error()
        flags = info.BasicLimitInformation.LimitFlags
        assert flags & mod.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, hex(flags)
        assert flags & mod.JOB_OBJECT_LIMIT_BREAKAWAY_OK, hex(flags)
    finally:
        mod._k32().CloseHandle(job)


def test_hub_lock_is_port_scoped():
    """回归（2026-10-06）：单实例锁按端口域化 —— 同端口互斥、异端口可并存
    （使用说明 8081 换端口 workaround + 多端口测试隔离需要）。"""
    mod = _load_run_hub()
    a = mod._single_hub_instance(48911)
    try:
        assert a not in (0, None), "锁创建失败: %r" % a
        dup = mod._single_hub_instance(48911)
        assert dup is None, "同端口重复启动必须被拒绝"
        b = mod._single_hub_instance(48912)
        try:
            assert b not in (0, None), "异端口实例必须放行: %r" % b
        finally:
            mod._release_hub_lock(b)
    finally:
        mod._release_hub_lock(a)


def test_exit_event_name():
    """FR-4：命名事件带端口后缀；drain 3s 超时 TerminateProcess 兜底；worker OpenEventW 监听。"""
    mod = _load_run_hub()
    name = mod._exit_event_name(48620)
    assert name.startswith("Local\\")
    assert "mio-taskhub-worker-exit-48620" in name
    src = _src()
    assert re.search(r"DRAIN_TIMEOUT_S\s*=\s*3", src)
    assert "TerminateProcess(" in src
    assert "OpenEventW(" in src


def test_worker_no_tray_no_lock():
    """FR-1/FR-5：worker 分支不建托盘、不抢单实例锁、不做端口回收。"""
    body = inspect.getsource(_load_run_hub()._worker_main)
    for token in ("_single_hub_instance", "_start_tray", "_reclaim_port"):
        assert token not in body, token


def test_worker_starts_hard_exit_watchdog():
    """更新/退出兜底：worker 必须启动硬退看门狗（防 loop.close 挂死阻塞更新）。"""
    body = inspect.getsource(_load_run_hub()._worker_main)
    assert "_hard_exit_watchdog" in body


def test_hard_exit_watchdog_forces_exit_after_grace():
    """退出请求超 grace 仍存活 → 以 exit code 0 强退（supervisor 收敛不重启）。"""
    mod = _load_run_hub()
    current = {"exit": True}
    codes = []
    mod._hard_exit_watchdog(current, grace=0.01, poll=0.05, exit_fn=codes.append)
    assert codes == [0]


def test_hard_exit_watchdog_idle_without_exit_request():
    """未请求退出时看门狗不得强退（守护循环语义不变）。"""
    mod = _load_run_hub()
    current = {"exit": False}
    codes = []
    fired = threading.Event()

    def _exit_fn(code):
        codes.append(code)
        fired.set()

    t = threading.Thread(target=mod._hard_exit_watchdog,
                         args=(current,), kwargs={"grace": 0.01, "poll": 0.02,
                                                  "exit_fn": _exit_fn})
    t.daemon = True
    t.start()
    t.join(timeout=0.3)
    assert not fired.is_set()
    assert codes == []


def test_no_uvicorn_workers_n():
    """非目标：禁止把 workers= 传给 uvicorn（apscheduler 会跨进程重复执行定时任务）。"""
    src = _src()
    assert "uvicorn.Config(" in src
    assert not re.search(r"(?<![\w_])workers\s*=", src)
