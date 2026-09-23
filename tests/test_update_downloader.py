import hashlib
import pytest

from mio_taskhub.update.downloader import download, sha256_file, DownloadError

PAYLOAD = b"hello-zip-bytes" * 100
GOOD_SHA = hashlib.sha256(PAYLOAD).hexdigest()


def _opener_ok(url):
    # 返回一个可迭代的 chunk 生成器（模拟流式）
    def gen():
        for i in range(0, len(PAYLOAD), 64):
            yield PAYLOAD[i:i + 64]
    return gen()


def test_download_ok_writes_and_verifies(tmp_path):
    dest = tmp_path / "pkg.zip"
    progress = []
    out = download("https://github.com/x/y.zip", dest, sha256=GOOD_SHA,
                   opener=_opener_ok, progress=lambda d, t: progress.append((d, t)))
    assert out == dest
    assert dest.read_bytes() == PAYLOAD
    assert not (tmp_path / "pkg.zip.part").exists()
    assert progress and progress[-1][0] == len(PAYLOAD)


def test_download_sha_mismatch_raises_and_cleans(tmp_path):
    dest = tmp_path / "pkg.zip"
    with pytest.raises(DownloadError):
        download("https://github.com/x/y.zip", dest, sha256="0" * 64,
                 opener=_opener_ok, retries=2)
    assert not dest.exists()
    assert not (tmp_path / "pkg.zip.part").exists()


def test_download_retries_then_succeeds(tmp_path):
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("net down")
        return _opener_ok(url)

    dest = tmp_path / "pkg.zip"
    download("https://github.com/x/y.zip", dest, sha256=GOOD_SHA, opener=flaky, retries=3)
    assert dest.read_bytes() == PAYLOAD
    assert calls["n"] == 2


def test_sha256_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(PAYLOAD)
    assert sha256_file(f) == GOOD_SHA


def test_download_size_mismatch_retries_and_cleans(tmp_path):
    calls = {"n": 0}

    def short(url):
        calls["n"] += 1
        return _opener_ok(url)  # 实际字节 < size → 触发 size mismatch

    dest = tmp_path / "pkg.zip"
    with pytest.raises(DownloadError):
        download("https://github.com/x/y.zip", dest, sha256=GOOD_SHA,
                 size=len(PAYLOAD) + 999, opener=short, retries=2)
    assert calls["n"] == 2
    assert not dest.exists()
    assert not (tmp_path / "pkg.zip.part").exists()


def test_download_opener_closed_even_on_error(tmp_path):
    """流式响应必须被关闭（防 socket 泄漏），即使读取中途抛错。"""
    opened = {"closed": False}

    class FakeResp:
        def read(self, n):
            raise OSError("mid-stream boom")

        def close(self):
            opened["closed"] = True

    def opener_with_close(url):
        resp = FakeResp()

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

    dest = tmp_path / "pkg.zip"
    with pytest.raises(DownloadError):
        download("https://github.com/x/y.zip", dest, opener=opener_with_close, retries=1)
    assert opened["closed"] is True
