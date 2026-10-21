# -*- coding: utf-8 -*-
"""T4.3 release_checklist.py 单测。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))


def test_script_imports():
    import release_checklist as rc
    for fn in ("check_ci", "check_no_red", "check_license", "check_readme",
               "check_first_run", "check_release", "check_known_limits",
               "check_installer_bundle"):
        assert hasattr(rc, fn)


def test_installer_bundle_check_is_green():
    """第 ⑧ 项：安装器分发产物一致性。

    回归：新增这项时 release.ps1 里的资产清单是**手抄**的，
    抄两份就会漂移（漏掉 uninstall.exe → 用户拿不到独立卸载器）。
    现在清单的唯一事实源是 mio_taskhub/release_assets.py。
    """
    import release_checklist as rc
    r = rc.check_installer_bundle()
    assert r["status"] == "是", r["detail"]


def test_release_assets_include_uninstaller():
    """**回归**：发布资产必须包含独立的 uninstall.exe。

    用户可能只想要「卸载程序」这一份文件（从「应用和功能」点卸载、
    或把安装目录里的 uninstall.exe 拷走用）。不单独发就拿不到。
    """
    sys.path.insert(0, str(REPO))
    from mio_taskhub import release_assets

    assets = release_assets.all_release_assets()
    # 清单里存的是相对 dist/ 的完整路径
    assert any(a.endswith(release_assets.UNINSTALL_EXE)
               for a in release_assets.INSTALLER_BUNDLE_ASSETS)
    assert any(a.endswith(release_assets.UNINSTALL_EXE) for a in assets)
    # 绿色版 zip 也必须在（自动更新要用）
    assert release_assets.PAYLOAD_ASSET in assets


def test_release_asset_upload_names_are_unique():
    """**回归**：上传用的文件名不能撞车。

    实测踩过：安装包目录里也有一份 `mio-taskhub-win64.zip`（和顶层那份
    **是同一个文件**，build.ps1 拷过去的）。上传用 `Split-Path -Leaf`，
    同名资产在同一个 Release 里传两次会被 GitHub 拒（already_exists）。
    对策：payload zip 只作为**顶层**资产上传一次。
    """
    sys.path.insert(0, str(REPO))
    from mio_taskhub import release_assets

    names = [Path(a).name for a in release_assets.all_release_assets()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, "发布资产文件名撞车：%s" % dupes


def test_release_ps1_reads_asset_list_from_single_source():
    """**回归**：release.ps1 不许再手抄一份资产清单。

    必须从 mio_taskhub/release_assets.py 读 —— 两份清单必然漂移。
    """
    ps1 = (REPO / "packaging" / "release.ps1").read_text(encoding="utf-8")
    assert "release_assets" in ps1, (
        "release.ps1 应从 mio_taskhub.release_assets 读资产清单")
    # 不许再有硬编码的安装包资产列表
    assert "mio-taskhub-setup.exe" not in ps1, (
        "release.ps1 里不要硬编码资产名（应由 release_assets.py 提供）")


def test_license_check_green():
    import release_checklist as rc
    r = rc.check_license()
    assert r["status"] == "是"


def test_readme_check_green():
    import release_checklist as rc
    r = rc.check_readme()
    assert r["status"] == "是", r["detail"]


def test_known_limits_green():
    import release_checklist as rc
    r = rc.check_known_limits()
    assert r["status"] == "是", r["detail"]


def test_release_check_green():
    import release_checklist as rc
    r = rc.check_release()
    assert r["status"] == "是", r["detail"]


def test_first_run_partial_or_green():
    """首跑：本机 PASS；外部未取证时为「部分」。"""
    import release_checklist as rc
    r = rc.check_first_run()
    assert r["status"] in ("是", "部分"), r["detail"]


def test_report_doc_exists():
    doc = REPO / "docs" / "roadmap" / "t43-release-checklist-report.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert "7" in text or "七项" in text
    assert "CI" in text
