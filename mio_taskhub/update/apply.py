# -*- coding: utf-8 -*-
"""更新事务：同卷 rename + 可恢复替换，失败回滚。

放到独立进程执行（mio-taskhub.exe --apply-update ...），因此不持有 hub 的文件句柄。
"""
import os
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

REQUIRED_ENTRIES = (
    "mio-taskhub.exe",
    os.path.join("_internal", "web", "dist", "index.html"),
)


@dataclass
class ReplaceResult:
    ok: bool
    backup: str = ""
    error: str = ""


def _rename_retry(src, dst, timeout: float = 30.0, interval: float = 0.5) -> None:
    """同卷 rename，遇文件锁退避重试，超时抛 OSError。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.rename(str(src), str(dst))
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(interval)


def verify_staging(staging) -> Tuple[bool, str]:
    """校验解压出的程序目录含关键项。"""
    staging = Path(staging)
    for rel in REQUIRED_ENTRIES:
        if not (staging / rel).exists():
            return False, "staging 缺少关键项：%s" % rel
    return True, "ok"


def _prune_backups(install: Path, keep: int = 2) -> None:
    """仅保留最新的 keep 个 <install>.bak-* 备份（按 mtime）。"""
    import shutil
    try:
        cands = list(install.parent.glob(install.name + ".bak-*"))
    except OSError:
        return
    if len(cands) <= keep:
        return
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old in cands[keep:]:
        shutil.rmtree(old, ignore_errors=True)


def execute_replace(install, staging, backup, retry_timeout: float = 30.0) -> ReplaceResult:
    """install→backup，staging→install；第二步失败则回滚。

    调用方保证 backup 命名含来源版本（app.bak-<from_version>），
    以便"同名备份可安全覆盖、异版本备份不被破坏"。
    """
    import shutil
    install, staging, backup = Path(install), Path(staging), Path(backup)

    if backup.exists():
        shutil.rmtree(backup, ignore_errors=True)
    try:
        _rename_retry(install, backup, timeout=retry_timeout)
    except OSError as e:
        return ReplaceResult(False, error="install→backup rename 失败：%s" % e)

    try:
        _rename_retry(staging, install, timeout=retry_timeout)
    except OSError as e:
        try:
            _rename_retry(backup, install, timeout=retry_timeout)
        except OSError:
            return ReplaceResult(False, str(backup),
                                 "staging→install 失败且回滚失败：%s" % e)
        return ReplaceResult(False, str(backup), "staging→install rename 失败（已回滚）：%s" % e)

    _prune_backups(install, keep=2)
    return ReplaceResult(True, backup=str(backup), error="")


# ---- Task 7: 进程等待 + 健康确认 + 残留恢复 ----

RUNTIME_REL = os.path.join(".mio_taskhub", "update", "runtime.json")


def default_runtime_path() -> Path:
    return Path.home() / RUNTIME_REL


def pid_alive(pid: int) -> bool:
    """跨平台判断进程是否存活。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = ctypes.windll.kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def wait_pid_exit(pid: int, timeout: float = 60.0, poll: float = 0.5) -> bool:
    """等进程退出；超时返回 False。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(poll)
    return not pid_alive(pid)


def read_runtime(path=None) -> dict:
    p = Path(path) if path else default_runtime_path()
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def wait_healthy(target_version: str, after_ts: float, runtime_path=None,
                 timeout: float = 90.0, poll: float = 1.0) -> bool:
    """轮询 sentinel：version == 目标 且 started_at > after_ts。

    任何读取/解析异常都视为"尚未就绪"，继续轮询直到超时（fail-safe）。
    """
    deadline = time.monotonic() + timeout
    p = Path(runtime_path) if runtime_path else default_runtime_path()
    while time.monotonic() < deadline:
        try:
            st = read_runtime(p)
            ok = (str(st.get("version")) == str(target_version)
                  and float(st.get("started_at") or 0) > float(after_ts))
        except Exception:  # noqa: BLE001 —— 畸形数据按未就绪处理
            ok = False
        if ok:
            return True
        time.sleep(poll)
    return False


def _looks_complete(dir_path) -> bool:
    ok, _ = verify_staging(dir_path)
    return ok


def recover_residual(install_dir, log: Callable[[str], None] = None) -> list:
    """hub 启动时调用：清理 staging、在 install 缺失时从 backup 恢复。"""
    install = Path(install_dir)
    parent = install.parent
    name = install.name
    actions = []

    def _log(m):
        actions.append(m)
        if log:
            log(m)

    for staging in sorted(parent.glob(name + ".staging-*")):
        import shutil
        shutil.rmtree(staging, ignore_errors=True)
        _log("清理残留 staging：%s" % staging.name)

    if not _looks_complete(install):
        backups = [b for b in parent.glob(name + ".bak-*") if _looks_complete(b)]
        backups.sort(key=lambda b: b.stat().st_mtime, reverse=True)
        for backup in backups:
            if install.exists():
                import shutil
                shutil.rmtree(install, ignore_errors=True)
            try:
                os.rename(str(backup), str(install))
                _log("restore：从 %s 恢复 install" % backup.name)
            except OSError as e:
                _log("restore 失败：%s" % e)
            break
    return actions


# ---- Task 8: apply-update 进程入口 ----

def _apply_log(msg: str) -> None:
    """追加到 ~/.mio_taskhub/update/apply.log（可用 MIO_UPDATE_LOG_PATH 覆盖，供测试隔离）。"""
    override = os.environ.get("MIO_UPDATE_LOG_PATH")
    log_path = Path(override) if override else (Path.home() / ".mio_taskhub" / "update" / "apply.log")
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (ts, msg))
    except OSError:
        pass


def _spawn_hub(target) -> int:
    """以 hub 模式启动新版本；返回 PID（detached）。"""
    import subprocess
    exe = str(Path(target) / "mio-taskhub.exe")
    creation = 0
    if sys.platform == "win32":
        creation = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    p = subprocess.Popen([exe, "hub"], cwd=str(target), creationflags=creation,
                         close_fds=True)
    return p.pid


def _parse_args(argv):
    """极简参数解析，返回 dict 或 None。"""
    out = {}
    i = 0
    keys = {"--zip", "--sha256", "--version", "--pid", "--target", "--runtime-json"}
    while i < len(argv):
        k = argv[i]
        if k not in keys or i + 1 >= len(argv):
            return None
        out[k.lstrip("-").replace("-", "_")] = argv[i + 1]
        i += 2
    if not all(x in out for x in ("zip", "sha256", "version", "pid", "target")):
        return None
    return out


def run_apply_update(argv) -> int:
    """--apply-update 模式入口。返回 0=成功，1=失败，2=参数错误。"""
    args = _parse_args(argv)
    if not args:
        print("usage: --apply-update --zip <z> --sha256 <h> --version <v> "
              "--pid <p> --target <dir> [--runtime-json <path>]")
        return 2

    try:
        pid = int(args["pid"])
    except ValueError:
        print("usage: --pid 必须是整数")
        return 2

    zip_path = Path(args["zip"])
    sha = args["sha256"].lower()
    version = args["version"]
    target = Path(args["target"])
    runtime_json = args.get("runtime_json")

    start_ts = time.time()
    _apply_log("apply 开始：version=%s target=%s pid=%d" % (version, target, pid))

    # 1) 等 hub 退出
    if not wait_pid_exit(pid, timeout=60.0):
        _apply_log("等待 hub(%d) 退出超时，放弃替换" % pid)
        return 1

    # 2) 校验 zip 指纹（在解压前）
    from mio_taskhub.update.downloader import sha256_file
    try:
        got = sha256_file(zip_path)
    except OSError as e:
        _apply_log("读 zip 失败：%s" % e)
        return 1
    if got != sha:
        _apply_log("zip sha256 不符：got=%s want=%s" % (got, sha))
        return 1

    # 3) 解压到 staging
    import shutil
    staging = target.with_name(target.name + ".staging-" + version)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(staging)
    except Exception as e:  # noqa: BLE001
        _apply_log("解压失败：%s" % e)
        shutil.rmtree(staging, ignore_errors=True)
        return 1

    ok, msg = verify_staging(staging)
    if not ok:
        _apply_log("staging 校验失败：%s" % msg)
        shutil.rmtree(staging, ignore_errors=True)
        return 1

    # 4) 事务替换
    from_ver = str(read_runtime(runtime_json).get("version") or "unknown")
    backup = target.with_name(target.name + ".bak-" + from_ver)
    res = execute_replace(target, staging, backup)
    if not res.ok:
        _apply_log("替换失败：%s" % res.error)
        return 1
    _apply_log("替换完成：backup=%s" % backup.name)

    # 5) 启动新版本 + 健康确认
    try:
        _spawn_hub(target)
    except Exception as e:  # noqa: BLE001
        _apply_log("启动新版本失败：%s，回滚" % e)
        _rollback(backup, target)
        _spawn_hub_safe(target)
        return 1

    if wait_healthy(version, after_ts=start_ts, runtime_path=runtime_json):
        _apply_log("健康确认通过，删除备份 %s" % backup.name)
        shutil.rmtree(backup, ignore_errors=True)
        return 0

    _apply_log("健康确认超时，回滚")
    _rollback(backup, target)
    _spawn_hub_safe(target)
    return 1


def _rollback(backup: Path, target: Path) -> None:
    import shutil
    try:
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        _rename_retry(backup, target)
        _apply_log("回滚成功")
    except OSError as e:
        _apply_log("回滚失败：%s（保留 %s 供人工介入）" % (e, backup))


def _spawn_hub_safe(target: Path) -> None:
    try:
        _spawn_hub(target)
    except Exception as e:  # noqa: BLE001
        _apply_log("重启旧版本失败：%s" % e)
