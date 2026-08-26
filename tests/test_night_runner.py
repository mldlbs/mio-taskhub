import json
from unittest.mock import patch

from mio_taskhub import night_runner as nr


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
    ok = runner._spawn({"agent": "echo-test", "command": "cmd /c exit 0"})
    assert ok is True
    import time as _t
    _t.sleep(0.3)
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
