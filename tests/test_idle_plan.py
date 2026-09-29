# -*- coding: utf-8 -*-
"""空闲计划（原夜间计划）：项目范围配置 + {project} 占位符 + idle_worker 提示词拼装。

task 7a10b3bb。不真拉进程、不真跑 agent。
"""
import importlib.util
from pathlib import Path

import pytest

from mio_taskhub.scheduling import night_runner as nr


def _load_idle_worker():
    p = Path(__file__).resolve().parents[1] / "packaging" / "idle_worker.py"
    spec = importlib.util.spec_from_file_location("idle_worker_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- 配置：projects 范围 ----------

def test_save_config_persists_and_cleans_projects(tmp_path, monkeypatch):
    cfg_path = tmp_path / "night_runner.json"
    monkeypatch.setattr(nr, "CONFIG_PATH", cfg_path)
    clean = nr.save_config({
        "enabled": True, "window_start": "21:30", "window_end": "06:30",
        "agents": [{"agent": "w1", "command": "echo hi"}, {"agent": "bad"}],
        "projects": ["agent-dev", "  csps  ", "", None],
    })
    assert clean["projects"] == ["agent-dev", "csps"]      # 去空白、去空值
    assert [a["agent"] for a in clean["agents"]] == ["w1"]  # 无 command 的被剔除
    import json
    on_disk = json.loads(cfg_path.read_text(encoding="utf-8"))
    assert on_disk["projects"] == ["agent-dev", "csps"]
    assert on_disk["window_start"] == "21:30"


def test_load_config_defaults_projects_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "CONFIG_PATH", tmp_path / "nope.json")
    cfg = nr.load_config()
    assert cfg["projects"] == []
    assert cfg["window_start"] == nr.DEFAULT_CONFIG["window_start"]


# ---------- 占位符替换 ----------

def test_spawn_substitutes_project_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "CONFIG_PATH", tmp_path / "night_runner.json")
    nr.save_config({"enabled": True, "projects": ["agent-dev", "csps"],
                    "agents": [{"agent": "w1", "command": "x"}]})
    captured = {}

    class _P:
        pid = 4242

        def poll(self):
            return None

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["kwargs"] = kw
        return _P()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    runner = nr.NightRunner()
    ok = runner._spawn({"agent": "w1", "command": "idle_worker.py w1 --project {project} --cli {url}"})
    assert ok is True
    assert "agent-dev,csps" in captured["cmd"]
    assert "{project}" not in captured["cmd"]
    assert "{url}" not in captured["cmd"]          # {url} 仍被替换
    assert captured["kwargs"].get("creationflags") is not None


def test_spawn_project_empty_when_no_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "CONFIG_PATH", tmp_path / "night_runner.json")
    captured = {}

    class _P:
        pid = 1

        def poll(self):
            return None

    monkeypatch.setattr("subprocess.Popen",
                        lambda cmd, **kw: (captured.update(cmd=cmd), _P())[1])
    runner = nr.NightRunner()
    runner._spawn({"agent": "w2", "command": "worker --project {project}"})
    assert captured["cmd"].strip().endswith("--project")   # 无范围 → 空串


# ---------- idle_worker ----------

def test_idle_worker_build_prompt_contains_task_facts():
    mod = _load_idle_worker()
    prompt = mod.build_prompt({
        "id": "abc12345", "title": "把 X 做完",
        "description": "细节描述 D", "acceptance_criteria": "验收 A",
    })
    assert "把 X 做完" in prompt
    assert "abc12345" in prompt
    assert "细节描述 D" in prompt
    assert "验收 A" in prompt


def test_idle_worker_claim_path_includes_project(monkeypatch):
    mod = _load_idle_worker()
    seen = {}

    def fake_req(method, path, body=None, timeout=20):
        seen["method"], seen["path"] = method, path
        return None

    monkeypatch.setattr(mod, "req", fake_req)
    mod.claim_once("worker-1", "agent-dev")
    assert seen["method"] == "POST"
    assert "/tasks/claim?agent=worker-1" in seen["path"]
    assert "project=agent-dev" in seen["path"]
