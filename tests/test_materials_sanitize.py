# -*- coding: utf-8 -*-
"""素材库 P1：等长脱敏与指纹。"""
from mio_taskhub.materials.common import content_fingerprint
from mio_taskhub.materials.sanitize import (
    SANITIZER_CONFIG_HASH, mask_text, sanitize_payload, scan_text,
)


def test_mask_preserves_length_and_newlines():
    src = (
        "token=sk-abcdefghijklmnop\n"
        "Authorization: Bearer abcdefghijklmnopqrst\n"
        "password=supersecret\n"
        "-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----"
    )
    out = mask_text(src)
    assert len(out) == len(src)
    assert out.count("\n") == src.count("\n")
    assert "sk-abcdefghijklmnop" not in out
    assert "abcdefghijklmnopqrst" not in out
    assert "supersecret" not in out
    assert "AAAA" not in out


def test_scan_finds_original_but_not_masked():
    src = "password=supersecret and token=sk-abcdefghijklmnop"
    assert scan_text(src)
    masked = mask_text(src)
    assert scan_text(masked) == []


def test_sanitize_payload_hashes_stable():
    payload = {"content": "hello password=supersecret world"}
    a = sanitize_payload(payload)
    b = sanitize_payload(payload)
    assert a.source_hash == b.source_hash
    assert a.sanitized_hash == b.sanitized_hash
    assert a.config_hash == SANITIZER_CONFIG_HASH
    assert "supersecret" not in a.payload["content"]
    assert len(a.payload["content"]) == len(payload["content"])


def test_fingerprint_folds_mask_runs():
    a = content_fingerprint("server error password=****** restart")
    b = content_fingerprint("server error password=*** restart")
    assert a == b
