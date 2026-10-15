# -*- coding: utf-8 -*-
"""可复现构建校验（T4.2 / G1·M3）。

用途：
  1. 生成构建产物的 SHA256 清单（供 Release 校验）；
  2. 对比两次构建产物，报告逐文件一致/差异（可复现性取证）；
  3. 对非逐字节一致的文件做**内容归一化**对比（剥离 PE 时间戳等），区分
     "真差异（源码/依赖变了）" vs "可解释差异（嵌入时间戳）"。

用法：
  python scripts/repro_build.py manifest dist/mio-taskhub-v3            # 生成清单
  python scripts/repro_build.py compare dist_b1/mio-taskhub-v3 dist_b2/mio-taskhub-v3
  python scripts/repro_build.py compare-zip dist/a.zip dist/b.zip
"""
from __future__ import annotations
import hashlib
import json
import sys
import zipfile
from pathlib import Path


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _normalized_hash(path: Path) -> str:
    """归一化哈希：剥离已知非确定性元数据。

    - PE exe（.exe）：把 PE 头 offset+8 的 4 字节 TimeDateStamp 清零后哈希。
    - zip（.zip）：忽略 zip 成员 mtime/顺序，对「成员名:成员内容哈希」排序后哈希。
    - 其他：普通 sha256。
    """
    suffix = path.suffix.lower()
    if suffix == ".exe":
        data = bytearray(path.read_bytes())
        try:
            pe_off = int.from_bytes(data[0x3C:0x40], "little")
            # 1) PE TimeDateStamp（pe_off+8，4B）——构建时刻
            ts = bytes(data[pe_off + 8:pe_off + 12])
            data[pe_off + 8:pe_off + 12] = b"\x00\x00\x00\x00"
            # 2) PE CheckSum（pe_off+0x58，4B）——随 timestamp 变
            data[pe_off + 0x58:pe_off + 0x5C] = b"\x00\x00\x00\x00"
            # 3) 文件内其他位置嵌入的同一 timestamp 副本（如 PyInstaller 归档头）
            if ts != b"\x00\x00\x00\x00":
                i = data.find(ts)
                while i != -1:
                    data[i:i + 4] = b"\x00\x00\x00\x00"
                    i = data.find(ts, i + 4)
        except Exception:
            pass
        return hashlib.sha256(bytes(data)).hexdigest()
    if suffix == ".zip":
        h = hashlib.sha256()
        with zipfile.ZipFile(path) as zf:
            entries = []
            for info in zf.infolist():
                if info.is_dir():
                    continue
                entries.append((info.filename, hashlib.sha256(zf.read(info.filename)).hexdigest()))
        for name, digest in sorted(entries):
            h.update(name.encode("utf-8"))
            h.update(b"\x00")
            h.update(digest.encode("ascii"))
            h.update(b"\n")
        return h.hexdigest()
    return sha256_file(path)


def manifest(root: Path, normalized: bool = False) -> dict:
    """递归生成 {相对路径: sha256} 清单。normalized=True 时剥离非确定性元数据。"""
    fn = _normalized_hash if normalized else sha256_file
    if root.is_file():
        return {root.name: fn(root)}
    out = {}
    for f in sorted(root.rglob("*")):
        if f.is_file():
            out[str(f.relative_to(root)).replace("\\", "/")] = fn(f)
    return out


def compare(a: Path, b: Path, normalized: bool = False) -> dict:
    """对比两个目录/文件，返回一致/仅A/仅B/差异清单。"""
    ma, mb = manifest(a, normalized), manifest(b, normalized)
    ka, kb = set(ma), set(mb)
    same = [k for k in (ka & kb) if ma[k] == mb[k]]
    differ = [k for k in (ka & kb) if ma[k] != mb[k]]
    only_a = sorted(ka - kb)
    only_b = sorted(kb - ka)
    return {
        "a": str(a), "b": str(b), "normalized": normalized,
        "total_a": len(ma), "total_b": len(mb),
        "identical": len(same),
        "differ": differ,
        "only_in_a": only_a,
        "only_in_b": only_b,
        "reproducible": (not differ and not only_a and not only_b),
    }


def compare_zip(za: Path, zb: Path) -> dict:
    """对比两个 zip 的成员文件内容（忽略压缩元数据）。"""
    def members(z):
        out = {}
        with zipfile.ZipFile(z) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                out[info.filename] = hashlib.sha256(zf.read(info.filename)).hexdigest()
        return out
    ma, mb = members(za), members(zb)
    ka, kb = set(ma), set(mb)
    same = [k for k in (ka & kb) if ma[k] == mb[k]]
    differ = [k for k in (ka & kb) if ma[k] != mb[k]]
    return {
        "a": str(za), "b": str(zb),
        "total_a": len(ma), "total_b": len(mb),
        "identical": len(same), "differ": differ,
        "only_in_a": sorted(ka - kb), "only_in_b": sorted(kb - ka),
        "reproducible": (not differ and ka == kb),
    }


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "manifest":
        m = manifest(Path(argv[1]))
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0
    if cmd == "manifest-norm":
        m = manifest(Path(argv[1]), normalized=True)
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0
    if cmd == "compare":
        r = compare(Path(argv[1]), Path(argv[2]))
    elif cmd == "compare-norm":
        r = compare(Path(argv[1]), Path(argv[2]), normalized=True)
    elif cmd == "compare-zip":
        r = compare_zip(Path(argv[1]), Path(argv[2]))
    else:
        print(f"unknown command: {cmd}")
        return 1
    print(json.dumps(r, ensure_ascii=False, indent=2))
    # 退出码：一致=0，不一致=2（供 CI）
    return 0 if r["reproducible"] else 2


if __name__ == "__main__":
    sys.exit(main())
