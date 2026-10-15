# -*- coding: utf-8 -*-
"""T4.2 repro_build.py 单测。"""
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import repro_build as rb


def _mkzip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


def test_sha256_file(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello")
    assert rb.sha256_file(f) == rb.sha256_file(f)
    g = tmp_path / "b.txt"
    g.write_bytes(b"world")
    assert rb.sha256_file(f) != rb.sha256_file(g)


def test_manifest_identical_dirs(tmp_path):
    d1 = tmp_path / "d1"; d2 = tmp_path / "d2"
    d1.mkdir(); d2.mkdir()
    (d1 / "x.txt").write_bytes(b"abc")
    (d2 / "x.txt").write_bytes(b"abc")
    r = rb.compare(d1, d2)
    assert r["reproducible"] is True
    assert r["identical"] == 1


def test_compare_detects_differ(tmp_path):
    d1 = tmp_path / "d1"; d2 = tmp_path / "d2"
    d1.mkdir(); d2.mkdir()
    (d1 / "x.txt").write_bytes(b"abc")
    (d2 / "x.txt").write_bytes(b"abd")
    r = rb.compare(d1, d2)
    assert r["reproducible"] is False
    assert "x.txt" in r["differ"]


def test_zip_compare_ignores_mtime(tmp_path):
    """zip 成员内容一致时，即使 mtime 不同也应判可复现。"""
    z1 = tmp_path / "a.zip"; z2 = tmp_path / "b.zip"
    _mkzip(z1, {"m.py": b"print(1)"})
    _mkzip(z2, {"m.py": b"print(1)"})
    r = rb.compare_zip(z1, z2)
    assert r["reproducible"] is True


def test_normalized_hash_exe_zeroes_timestamp(tmp_path):
    """归一化哈希：仅 PE 时间戳不同的两个 exe 应得相同归一化哈希。"""
    # 构造最小 PE：e_lfanew(0x3C)=0x80, PE sig, timestamp at 0x80+8
    def _mkexe(ts: int) -> bytes:
        buf = bytearray(0x200)
        buf[0:2] = b"MZ"
        buf[0x3C:0x40] = (0x80).to_bytes(4, "little")
        buf[0x80:0x84] = b"PE\x00\x00"
        buf[0x88:0x8C] = ts.to_bytes(4, "little")
        return bytes(buf)
    a = tmp_path / "a.exe"; b = tmp_path / "b.exe"
    a.write_bytes(_mkexe(1792036307))
    b.write_bytes(_mkexe(1792036372))
    assert rb._normalized_hash(a) == rb._normalized_hash(b)


def test_report_doc_exists():
    doc = REPO / "docs" / "roadmap" / "t42-repro-build-report.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert "807" in text and "SHA256" in text
