# -*- coding: utf-8 -*-
"""分支保护 required checks 审计。"""
import pytest

from scripts import check_required_checks as crc


def test_all_required_present(monkeypatch):
    monkeypatch.setattr(crc, "fetch_required",
                        lambda t, r, b: {"test", "Materials gate tests", "x"})
    result = crc.audit("token", "o/r", "main",
                       ["test", "Materials gate tests"])
    assert result["ok"] is True
    assert result["missing"] == []


def test_missing_required_is_reported(monkeypatch):
    monkeypatch.setattr(crc, "fetch_required", lambda t, r, b: {"test"})
    result = crc.audit("token", "o/r", "main",
                       ["test", "Materials gate tests"])
    assert result["ok"] is False
    assert result["missing"] == ["Materials gate tests"]


def test_missing_token_is_not_success():
    result = crc.audit(None, "o/r", "main", ["test"])
    assert result["ok"] is False
    assert result["error"] == "token_missing"


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(crc, "_token", lambda: None)
    monkeypatch.setattr(crc.sys, "argv", ["check_required_checks.py"] + argv)
    return crc.main()


def test_main_fails_when_token_missing_by_default(monkeypatch, capsys):
    """缺 token 默认算失败——调用方没显式接受「跳过」时不该静悄悄放行。"""
    rc = _run_main(monkeypatch, ["--repo", "o/r", "--require", "test"])
    assert rc == 1
    assert "token_missing" in capsys.readouterr().out


def test_main_allows_missing_token_when_opted_in(monkeypatch, capsys):
    """定时审计用：缺 secret 是配置问题，不该让流水线天天红，但要留黄色注解。"""
    rc = _run_main(monkeypatch, ["--repo", "o/r", "--require", "test",
                                 "--allow-missing-token"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "::warning" in out
    assert "token_missing" in out
