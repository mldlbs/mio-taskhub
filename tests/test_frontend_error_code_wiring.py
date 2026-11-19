# -*- coding: utf-8 -*-
"""窄门禁（d468e633 FR-8）前端错误码透传的回归护栏。

背景：后端两套业务码约定并存 ——
  · `mio_taskhub/api/runs.py` 等用 `detail.code`（如 `run.empty_diff`）
  · `mio_taskhub/policy_guard.py` 用 `detail.error`（如 `policy_risk_high`）

前端 `ApiError` 原先**只读 `detail.error`** → 后端绝大多数业务码前端根本拿不到，
`run.empty_diff` 的 detail 会被 `JSON.stringify` 压成一行乱码，界面上看不到人话。

本用例做两层护栏：
  ① 静态断言（源码结构，防重构回退）
  ② 行为断言（node 跑 web/scripts/verify-error-code.mjs，测真实模块）

行为层的断言脚本见 web/scripts/verify-error-code.mjs（从 api.js 源码原样抽取 ApiError
类后用 new Function 构造再断言 —— **零依赖**，不需 npm install，CI 可直接跑）。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
API_JS = REPO / "web" / "src" / "api.js"
VERIFY_MJS = REPO / "web" / "scripts" / "verify-error-code.mjs"


def _src():
    return API_JS.read_text(encoding="utf-8")


def test_api_error_reads_detail_code():
    """① ApiError 必须读 detail.code（后端统一错误码表的主约定）。"""
    src = _src()
    assert "if (obj && obj.code) this.error_code = obj.code" in src, \
        "ApiError 未读取 detail.code —— 后端 run.empty_diff 等业务码前端拿不到"


def test_api_error_keeps_detail_error_fallback():
    """② detail.error 约定必须保留 —— destructive 门控（policy_risk_high）走它。

    gated() 依赖 `e.error_code === 'policy_risk_high'` 做确认重试；
    若这里改成只认 code，policy_guard 返回的 error 键会被丢掉，链路失效。
    """
    src = _src()
    assert "else if (obj && obj.error) this.error_code = obj.error" in src, \
        "ApiError 丢了 detail.error 约定 —— policy_risk_high 确认重试链路会失效"


def test_user_message_prefers_backend_message():
    """③ userMessage 优先取 detail.message；通用文案仅作兜底。

    api.md 明确：`run.empty_diff` 不复用通用 422 文案「数据校验失败，请检查必填项」。
    """
    src = _src()
    assert "this.userMessage = (obj && obj.message) || ERROR_MESSAGES[status]" in src, \
        "userMessage 未优先取后端 detail.message"
    # 通用文案表必须仍在（无 message 时兜底）
    assert "422: '数据校验失败，请检查必填项'" in src, "422 通用兜底文案被删掉了"


def test_req_message_not_jsonified():
    """④ req() 构造的报文优先取 detail.message，不得把整个 detail JSON 化。"""
    src = _src()
    assert "detail?.message || detail?.detail || detail?.error" in src, \
        "req() 未优先取 detail.message —— 后端 detail 会被 JSON.stringify 成一行乱码"
    # JSON.stringify 只能作为最后兜底，必须排在 message 之后
    assert src.index("detail?.message") < src.index("JSON.stringify(detail)"), \
        "JSON.stringify 兜底排在了 detail.message 之前"


def test_hint_transported():
    """⑤ hint 继续透传（ErrorBar 渲染下一步提示）。"""
    src = _src()
    assert "if (obj && obj.hint) this.hint = obj.hint" in src, "hint 未透传"


def test_behavior_verification_passes():
    """⑥ 行为层取证：node 跑 verify-error-code.mjs，对真实 api.js 断言。"""
    node = shutil.which("node")
    if not node:
        pytest.skip("未找到 node，跳过前端行为层断言")
    if not VERIFY_MJS.exists():
        pytest.skip("缺少 web/scripts/verify-error-code.mjs")
    proc = subprocess.run(
        [node, str(VERIFY_MJS)],
        cwd=str(REPO / "web"),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",   # Windows 默认 cp1252 解不动 node 的中文错误输出
        timeout=180,
    )
    assert proc.returncode == 0, (
        "verify-error-code.mjs 断言失败：\nstdout=%s\nstderr=%s"
        % (proc.stdout, proc.stderr)
    )
    assert "全部断言通过" in proc.stdout, proc.stdout
