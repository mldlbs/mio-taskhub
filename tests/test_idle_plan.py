# -*- coding: utf-8 -*-
"""空闲计划（原夜间计划）：项目范围配置 + {project} 占位符 + idle_worker 提示词拼装。

task 7a10b3bb。不真拉进程、不真跑 agent。
"""
import importlib.util
import os
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
    res = runner._spawn({"agent": "w1",
                         "command": "python -c pass {project} {url}"})
    assert res["ok"] is True and res["pid"] == 4242
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
    res = runner._spawn({"agent": "w2", "command": "python -c pass {project}"})
    assert res["ok"] is True
    assert captured["cmd"].strip().endswith("pass")        # 无范围 → {project} 替换为空串


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


def test_build_prompt_includes_required_reads_and_order():
    mod = _load_idle_worker()
    p = mod.build_prompt({"id": "t1", "title": "T"}, ["spec", "requirement"])
    assert "required_reads" in p
    assert "spec" in p and "requirement" in p
    assert "taskhub_read_document" in p
    assert "422" in p  # 明确未读会被拒


def test_build_prompt_no_reads_no_read_section():
    mod = _load_idle_worker()
    p = mod.build_prompt({"id": "t1", "title": "T"}, [])
    assert "required_reads" not in p
    assert "taskhub_read_document" not in p


def test_build_prompt_backward_compatible():
    """不传 required_reads 时仍可用（旧调用保持）。"""
    mod = _load_idle_worker()
    p = mod.build_prompt({"id": "t1", "title": "T", "description": "d"})
    assert "T" in p and "d" in p


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
    # project 是领取上下文；范围过滤用 project_scope（修复 0fe91462）
    assert "project_scope=agent-dev" in seen["path"]


# ---------- --cli-prefix 免引号模式（task 86e7cda0）----------

def test_wire_argv_prefix_mode():
    mod = _load_idle_worker()
    argv, shell = mod.wire_argv("PROMPT-BODY", "", "codex exec --skip-git-repo-check")
    # Windows 上 launcher 可能被解析为 codex.cmd（避免 .ps1 无法被 subprocess 执行）
    assert os.path.basename(argv[0]).lower().startswith("codex")
    assert argv[1:] == ["exec", "--skip-git-repo-check", "PROMPT-BODY"]
    assert shell is False
    argv2, shell2 = mod.wire_argv("P", "", "hermes -z")
    assert os.path.basename(argv2[0]).lower().startswith("hermes")
    assert argv2[1:] == ["-z", "P"] and shell2 is False


def test_wire_argv_template_mode():
    mod = _load_idle_worker()
    cmd, shell = mod.wire_argv("P", "echo hi", "")
    assert cmd == "echo hi" and shell is True


def test_wire_argv_neither_returns_none():
    mod = _load_idle_worker()
    spec, shell = mod.wire_argv("P", "", "")
    assert spec is None and shell is False


def test_resolve_windows_launcher_prefers_cmd_over_ps1(monkeypatch):
    """Windows：.ps1 应被校正为 .cmd/.exe（subprocess shell=False 不能执行 .ps1）。"""
    mod = _load_idle_worker()
    if os.name != "nt":
        import pytest as _pytest
        _pytest.skip("Windows-only behavior")
    import shutil as _sh
    monkeypatch.setattr(_sh, "which", lambda name: r"C:\fake\codex.cmd" if name.lower().endswith("codex.cmd") else None)
    out = mod._resolve_windows_launcher(r"C:\fake\codex.ps1")
    assert out.lower().endswith(".cmd")


def test_resolve_windows_launcher_noop_on_non_ps1():
    mod = _load_idle_worker()
    if os.name != "nt":
        import pytest as _pytest
        _pytest.skip("Windows-only behavior")
    # hermes.exe 原样返回
    assert mod._resolve_windows_launcher("hermes.exe") == "hermes.exe"


# ---------- 试跑诊断（task dd48a647）----------

def test_diagnose_missing_executable():
    hint = nr._diagnose_command("no_such_cli_xyz --flag")
    assert hint and "not found" not in hint  # 中文提示
    assert "no_such_cli_xyz" in hint


def test_diagnose_missing_python_script(tmp_path):
    hint = nr._diagnose_command("python idle_worker.py w1", cwd=str(tmp_path))
    assert hint and "idle_worker.py" in hint
    assert "cwd" in hint or "\u5b89\u88c5" in hint  # 提示设置 cwd


def test_diagnose_ok_for_existing_command():
    # python 一定在 PATH；且无脚本参数 → 看不出问题
    assert nr._diagnose_command("python --version") is None


def test_spawn_precheck_blocks_and_reports_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(nr, "CONFIG_PATH", tmp_path / "night_runner.json")
    runner = nr.NightRunner()
    res = runner._spawn({"agent": "bad", "command": "no_such_cli_xyz run"})
    assert res["ok"] is False
    assert res["error"] == "precheck failed"
    assert "no_such_cli_xyz" in res["hint"]
