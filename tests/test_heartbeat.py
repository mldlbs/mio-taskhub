# tests/test_heartbeat.py
import time as _time
from mio_taskhub.heartbeat import HeartbeatSweep, RunInfo
from mio_taskhub.models import RunState

def test_sweep_marks_timeout_runs():
    swept = []
    sweep = HeartbeatSweep(
        timeout_seconds=120,
        poll_interval=0.1,
        get_runs=lambda: [
            RunInfo(run_id="r1", task_id="t1", agent_name="a1",
                    state=RunState.RUNNING, last_heartbeat=_time.time() - 180,
                    attempt=1, max_retries=3),
        ],
        on_timeout=lambda rid, tid: swept.append((rid, tid)),
        on_alive=lambda rid: None,
    )
    sweep.start()
    _time.sleep(0.3)
    sweep.stop()
    assert ("r1", "t1") in swept


def test_on_alive_called_within_timeout():
    alive = []
    sweep = HeartbeatSweep(
        timeout_seconds=120,
        poll_interval=0.1,
        get_runs=lambda: [
            RunInfo(run_id="r1", task_id="t1", agent_name="a1",
                    state=RunState.RUNNING, last_heartbeat=_time.time(),
                    attempt=1, max_retries=3),
        ],
        on_timeout=lambda rid, tid: None,
        on_alive=lambda rid: alive.append(rid),
    )
    sweep.start()
    _time.sleep(0.3)
    sweep.stop()
    assert "r1" in alive


def test_terminal_state_skipped():
    swept = []
    alive = []
    sweep = HeartbeatSweep(
        timeout_seconds=120,
        poll_interval=0.1,
        get_runs=lambda: [
            RunInfo(run_id="r1", task_id="t1", agent_name="a1",
                    state=RunState.FINISHED, last_heartbeat=_time.time() - 180,
                    attempt=1, max_retries=3),
        ],
        on_timeout=lambda rid, tid: swept.append((rid, tid)),
        on_alive=lambda rid: alive.append(rid),
    )
    sweep.start()
    _time.sleep(0.3)
    sweep.stop()
    assert ("r1", "t1") not in swept
    assert "r1" not in alive
