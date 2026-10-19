import json
from unittest.mock import patch

from mio_taskhub.scheduling import night_runner as nr


def test_in_window_overnight():
    # 22:00-07:00 跨夜窗口
    assert nr._in_window(22 * 60, 1320, 420)        # 22:30 在窗内
    assert nr._in_window(3 * 60, 1320, 420)         # 03:00 在窗内
    assert not nr._in_window(12 * 60, 1320, 420)    # 12:00 不在
    assert not nr._in_window(21 * 60 + 59, 1320, 420)


def test_in_window_same_day():
    assert nr._in_window(9 * 60, 540, 18 * 60)      # 09:00-18:00
    assert nr._in_window(17 * 60 + 59, 540, 18 * 60)
    assert not nr._in_window(19 * 60, 540, 18 * 60)


def test_config_roundtrip(tmp_path):
    cfg_path = tmp_path / "night_runner.json"
    with patch.object(nr, "CONFIG_PATH", cfg_path):
        # 默认配置
        c1 = nr.load_config()
        assert c1["enabled"] is False
        assert c1["agents"] == []

        # 保存并读回；非法 agent 项被过滤
        saved = nr.save_config({
            "enabled": True,
            "window_start": "23:00",
            "agents": [
                {"agent": "opencode", "command": "opencode run {url}"},
                {"agent": "broken"},          # 无 command，应被过滤
                "not-a-dict",                  # 应被过滤
            ],
        })
        assert saved["enabled"] is True
        assert len(saved["agents"]) == 1

        c2 = nr.load_config()
        assert c2["window_start"] == "23:00"
        assert c2["agents"][0]["agent"] == "opencode"


def test_spawn_and_reap():
    runner = nr.NightRunner(poll_interval=0.1)
    res = runner._spawn({"agent": "echo-test", "command": "cmd /c exit 0"})
    assert res["ok"] is True
    # 子进程自身退出需要时间，原先固定 sleep(0.3) 在机器有负载时会等不到
    # （历史 flaky 根因：进程还在 _procs 里就被断言）。改为有上限的轮询。
    import time as _t
    deadline = _t.monotonic() + 10
    while "echo-test" in runner._procs and _t.monotonic() < deadline:
        _t.sleep(0.05)
        runner._reap_finished()
    assert "echo-test" not in runner._procs
    runner.stop_agents()


def test_tick_disabled_stops_agents():
    runner = nr.NightRunner(poll_interval=0.1)
    runner._spawn({"agent": "x", "command": "cmd /c timeout /t 30"})
    with patch.object(nr, "load_config", return_value={"enabled": False, "window_start": "22:00",
                                                       "window_end": "07:00", "agents": []}):
        runner.tick()
    assert runner._procs == {}


def test_spawn_passes_api_prefixed_url(monkeypatch):
    """night_runner 传给子进程的 MIO_TASKHUB_URL 必须含 /api/v1（否则 idle_worker claim 405）。"""
    captured = {}

    class _FakeProc:
        pid = 4242

        def poll(self):
            return 0

    def _fake_popen(cmd, **kw):
        captured["env"] = kw.get("env")
        return _FakeProc()

    monkeypatch.delenv("MIO_TASKHUB_URL", raising=False)
    with patch("subprocess.Popen", _fake_popen):
        runner = nr.NightRunner()
        res = runner._spawn({"agent": "u", "command": "cmd /c exit 0"})
    assert res["ok"] is True
    assert captured["env"]["MIO_TASKHUB_URL"].endswith("/api/v1"), captured["env"].get("MIO_TASKHUB_URL")
