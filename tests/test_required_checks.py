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
