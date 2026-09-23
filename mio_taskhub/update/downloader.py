# -*- coding: utf-8 -*-
"""流式下载 + 边下边算 sha256 + 重试；只在校验通过后 rename 成最终文件。"""
import hashlib
import os
import time
import urllib.request
from pathlib import Path
from typing import Callable


class DownloadError(RuntimeError):
    """下载或校验失败。"""


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_opener(url: str, timeout: float = 60.0):
    """返回 bytes 迭代器；读取结束/异常时确保关闭响应（防 socket 泄漏）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "mio-taskhub-updater"})
    resp = urllib.request.urlopen(req, timeout=timeout)

    def gen():
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    return
                yield chunk
        finally:
            resp.close()

    return gen()


def download(url: str, dest, sha256: str = None, size: int = None,
             opener: Callable = None, retries: int = 3,
             progress: Callable[[int, int], None] = None) -> Path:
    """下载 url → dest。边下边算 sha256，匹配后 rename .part→dest。

    opener(url) 返回 bytes 的迭代器。失败重试 retries 次。
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.parent / (dest.name + ".part")
    opener = opener or _default_opener
    want = (sha256 or "").lower()
    last_err = None

    for attempt in range(1, retries + 1):
        h = hashlib.sha256()
        done = 0
        try:
            if part.exists():
                part.unlink()
            with open(part, "wb") as f:
                for chunk in opener(url):
                    if not chunk:
                        continue
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, size or 0)
            if size and done != size:
                raise DownloadError("size mismatch: got %d want %d" % (done, size))
            if want and h.hexdigest() != want:
                raise DownloadError("sha256 mismatch")
            os.replace(part, dest)          # 同卷 replace，原子
            return dest
        except Exception as e:  # noqa: BLE001
            last_err = e
            try:
                if part.exists():
                    part.unlink()
            except OSError:
                pass
            if attempt < retries:
                time.sleep(0.5 * attempt)
    raise DownloadError("download failed after %d attempts: %s" % (retries, last_err))
