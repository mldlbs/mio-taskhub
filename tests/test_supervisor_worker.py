# -*- coding: utf-8 -*-
"""hub supervisor/worker 双进程结构静态回归（task 516bd0c7，FR-1~FR-6）。

spec 测试计划 7 用例：源码文本 + importlib 加载断言，不启动真进程。
"""
import importlib.util
import inspect
import re
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_PY = ROOT / "packaging" / "run.py"
RUN_HUB = ROOT / "packaging" / "run_hub.py"

_MOD = None


@pytest.fixture(autouse=True)
def _no_runtime_log(monkeypatch):
    """替身跑真实 supervisor/worker 函数时 _log 不得写 ~/.mio_taskhub/runtime.log。

    2026-11-10：早期版本把 [supervisor] health probe failed / restart backoff
    这类测试日志混进真实运行日志，排障时会误判成线上事件。
    """
    monkeypatch.setattr(_load_run_hub(), "_log", lambda *a, **k: None)


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


# ── 2026-11-10：worker 停摆修复（线上 48620 父进程在、端口没人听）────────────


class _FakeK32:
    """替身：WaitForSingleObject 按给定序列返回，记录 TerminateProcess。"""

    def __init__(self, results):
        self.results = list(results)
        self.terminated = []

    def WaitForSingleObject(self, handle, ms):
        return self.results.pop(0) if self.results else 0

    def TerminateProcess(self, handle, code):
        self.terminated.append(code)
        return True


class _FakeController:
    def __init__(self, expected=False, stopping=False):
        self.port = 48620
        self.expected_exit = expected
        self.stop = threading.Event()
        if stopping:
            self.stop.set()


def _clock(step=5.0):
    state = {"t": 0.0}

    def _now():
        state["t"] += step
        return state["t"]

    return _now


def test_force_worker_exit_uses_nonzero_code():
    """意外结束不能是 0，否则 supervisor 按 FR-2 收敛、永久停摆。"""
    mod = _load_run_hub()
    codes = []
    mod._force_worker_exit(mod.EXIT_START_FAILURE, "port busy",
                           exit_fn=codes.append, log_fn=lambda *a, **k: None)
    assert codes == [mod.EXIT_START_FAILURE]
    assert codes[0] != 0
    assert mod.EXIT_START_FAILURE != 0 and mod.EXIT_UNEXPECTED_RETURN != 0
    assert mod.EXIT_HEALTH_FAILED != 0


def test_worker_main_unexpected_returns_are_forced_nonzero():
    """守卫循环：启动失败 / 无退出请求却返回 → _force_worker_exit；预期退出仍 break。"""
    body = inspect.getsource(_load_run_hub()._worker_main)
    assert "_force_worker_exit(" in body
    assert "EXIT_START_FAILURE" in body
    assert "EXIT_UNEXPECTED_RETURN" in body
    assert 'if current["exit"]:' in body


class _LoopK32:
    """_supervisor_loop 替身：按序列给出退出码。"""

    def __init__(self, codes):
        self.codes = list(codes)
        self.closed = []

    def WaitForSingleObject(self, handle, ms):
        return 0

    def GetExitCodeProcess(self, handle, ptr):
        ptr._obj.value = self.codes.pop(0) if self.codes else 0
        return True

    def CloseHandle(self, handle):
        self.closed.append(handle)
        return True


class _LoopController:
    def __init__(self):
        self.port = 48620
        self.expected_exit = False
        self.stop = threading.Event()
        self.hlock = threading.Lock()
        self._worker = None
        self.waits = []

    def set_worker(self, handle):
        self._worker = handle

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.stop.is_set()


def _run_loop(monkeypatch, codes):
    """用替身跑真实 _supervisor_loop，返回 (spawn 次数, 退避等待序列)。"""
    mod = _load_run_hub()
    k32 = _LoopK32(codes)
    handles = iter(range(101, 120))
    spawned = []
    ctl = _LoopController()

    def spawn(_job):
        h = next(handles)
        spawned.append(h)
        return h, 9000 + h

    monkeypatch.setattr(mod, "_k32", lambda: k32)
    monkeypatch.setattr(mod, "_spawn_worker", spawn)
    monkeypatch.setattr(mod, "_wait_for_worker", lambda *a, **k: None)
    mod._supervisor_loop(ctl, None, None)
    return len(spawned), ctl.waits


def test_supervisor_loop_restarts_on_nonzero_exit(monkeypatch):
    """worker 以 3/7/9 退出 → 必须再 spawn 一次（这正是停摆修复的核心）。"""
    for code in (3, 7, 9):
        n, waits = _run_loop(monkeypatch, [code, 0])
        assert n == 2, "code=%d 未触发重启" % code
        assert waits and waits[0] == 0


def test_supervisor_loop_still_converges_on_zero(monkeypatch):
    """FR-2 契约不变：退出码 0 仍收敛，不重启（更新/托盘退出依赖它）。"""
    n, waits = _run_loop(monkeypatch, [0])
    assert n == 1
    assert waits == []


def test_watchdog_terminates_hung_worker():
    """worker 活着但 healthz 连续 3 次不通 → 以非 0 码终止，交给退避重启。"""
    mod = _load_run_hub()
    k32 = _FakeK32([mod.WAIT_TIMEOUT] * 8)
    probes = []

    def probe(_url):
        probes.append(1)
        return False

    mod._wait_for_worker(k32, _FakeController(), 1234, "http://127.0.0.1:48620",
                         probe=probe, monotonic=_clock(), grace_s=0)
    assert len(probes) == mod.WATCHDOG_FAIL_STREAK
    assert k32.terminated == [mod.EXIT_HEALTH_FAILED]


def test_watchdog_skips_probe_during_grace():
    mod = _load_run_hub()
    k32 = _FakeK32([mod.WAIT_TIMEOUT, mod.WAIT_OBJECT_0])
    probes = []
    mod._wait_for_worker(k32, _FakeController(), 1234, "u",
                         probe=lambda _u: probes.append(1) or False,
                         monotonic=_clock(), grace_s=100)
    assert probes == []
    assert k32.terminated == []


def test_watchdog_successful_probe_resets_streak():
    """中间一次探活成功要清零，不能累计到触发。"""
    mod = _load_run_hub()
    k32 = _FakeK32([mod.WAIT_TIMEOUT] * 12)
    seq = [False, False, True, False, False, False]
    calls = []

    def probe(_url):
        calls.append(1)
        return seq.pop(0) if seq else False

    mod._wait_for_worker(k32, _FakeController(), 1234, "u",
                         probe=probe, monotonic=_clock(), grace_s=0)
    assert len(calls) == 6
    assert k32.terminated == [mod.EXIT_HEALTH_FAILED]


def test_watchdog_ignores_probe_during_exit_and_after_worker_exit():
    mod = _load_run_hub()
    calls = []
    probe = lambda _u: calls.append(1) or False  # noqa: E731

    k32 = _FakeK32([mod.WAIT_TIMEOUT, mod.WAIT_OBJECT_0])
    mod._wait_for_worker(k32, _FakeController(expected=True), 1, "u",
                         probe=probe, monotonic=_clock(), grace_s=0)
    assert calls == [] and k32.terminated == []

    k32 = _FakeK32([mod.WAIT_OBJECT_0])
    mod._wait_for_worker(k32, _FakeController(), 1, "u",
                         probe=probe, monotonic=_clock(), grace_s=0)
    assert calls == [] and k32.terminated == []


def test_supervisor_loop_uses_health_watchdog():
    """FR-2 收敛判定不变（code==0 仍不重启），等待改走探活看门狗。"""
    src = _src()
    assert "_wait_for_worker(k32, controller, handle" in src
    assert "controller.expected_exit or code == 0" in src
    assert "_probe_health" in src
    assert "/healthz" in src
