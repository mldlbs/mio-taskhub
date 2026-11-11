import os
import tempfile
import pytest

# Isolate tests from the production DB: point at a throwaway sqlite file
# BEFORE importing mio_taskhub.db (which builds the engine at import time).
_TEST_DB = os.environ.get(
    "MIO_TEST_DB_PATH",
    os.path.join(tempfile.gettempdir(), "mio_taskhub_test.db"),
)
os.environ["MIO_TASKHUB_DB"] = _TEST_DB
if os.path.exists(_TEST_DB):
    os.remove(_TEST_DB)

# 隔离 Mio policy check：默认指向不存在的 MIO_HOME → fail-open、无子进程。
# 需要真实/伪造 Mio 的用例（test_mio_runtime / test_policy_guard）自行覆盖。
os.environ.setdefault(
    "MIO_HOME",
    os.path.join(tempfile.gettempdir(), "mio_home_tests_missing"),
)

# F2 修复（T4.3）：关闭 insight_remediator 后台自动建任务——它会创建 ready 任务，
# 污染 claim/挑选类用例（如 test_claim_only_picks_ready），造成跨用例 flaky。
os.environ.setdefault("MIO_INSIGHT_AUTOTASK", "0")
os.environ.setdefault("MIO_INSIGHT_AUTOTASK_PRECHECK", "0")
# 测试不启动素材采集后台线程（P3），避免读取真实 observer 文件污染临时库
os.environ.setdefault("MIO_MATERIALS_AUTOSTART", "0")

from sqlmodel import select  # noqa: E402
from mio_taskhub.db import engine, init_db  # noqa: E402
from mio_taskhub.models import SQLModel, Task, Run, Agent  # noqa: E402

# F-004：管理面凭据不与测试进程的生产 env 串味。需要「强隔离」语义的用例自己用
# monkeypatch.setenv("MIO_TASKHUB_ADMIN_TOKEN", ...) 覆盖，monkeypatch 用完即还原。
os.environ.pop("MIO_TASKHUB_ADMIN_TOKEN", None)

# ── TestClient 凭据基线（F-004）─────────────────────────────────────────────
# 本仓库没有统一的 client fixture：约 100 个测试模块各自在模块级写
# `client = TestClient(app)`，在 conftest 的 fixture 里改不到这些已构造的实例。
# 因此在 conftest 导入期（早于所有测试模块）统一给 TestClient 注入默认管理员头：
#
#   **TestClient 视为本机可信管理面**：默认带 X-TaskHub-Admin: 1，走 require_admin
#   的「环回 + 显式确认头」降级态。无权限路径另用 anon_client，不要用默认 client
#   假装匿名，否则 403 断言会变成假绿。
#
# 需要验证「无管理员头」的用例一律用 `anon_client` fixture（或 _anon=True）。
from fastapi.testclient import TestClient as _TestClient  # noqa: E402

_ORIG_TESTCLIENT_INIT = _TestClient.__init__


def _admin_plane_init(self, app, *args, **kwargs):
    if kwargs.pop("_anon", False):
        return _ORIG_TESTCLIENT_INIT(self, app, *args, **kwargs)
    headers = dict(kwargs.pop("headers", None) or {})
    headers.setdefault("X-TaskHub-Admin", "1")
    kwargs["headers"] = headers
    return _ORIG_TESTCLIENT_INIT(self, app, *args, **kwargs)


_TestClient.__init__ = _admin_plane_init


@pytest.fixture()
def anon_client():
    """不带任何管理员头的匿名主体；用于验证 403 路径（F-004）。"""
    from mio_taskhub.main import app  # noqa: F401
    return _TestClient(app, _anon=True)


@pytest.fixture(autouse=True)
def _clean_db():
    SQLModel.metadata.drop_all(engine)
    init_db()
    # 文档读取的「已知 workspace 根」有 60s 缓存，drop_all 后必须失效，否则跨用例串味
    from mio_taskhub.api import task_documents as _td
    _td._WS_ROOTS_CACHE.update(at=0.0, roots=[])
    yield


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Clear global rate limiter buckets between tests."""
    from mio_taskhub.middleware import _rate_buckets
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()


@pytest.fixture(autouse=True)
def _reset_role_prompt_cache():
    """P2（FR-19/FR-20）：模块级 prompt/词表缓存随每测试失效，防 drop_all 后读到陈旧数据。"""
    from mio_taskhub import role_prompts as rp
    rp.invalidate()
    yield
    rp.invalidate()
