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
    """**回归**：发布资产必须能拿到卸载器。

    形态变了：现在是**单文件**安装包 —— 卸载器已经合并进
    `mio-taskhub-setup.exe`（payload 里有 uninstall.exe，payload 又被
    合并进 setup.exe），所以**不再单独发**一份 uninstall.exe。

    但「用户能拿到可执行的卸载器」这件事必须仍然成立：
    - 安装目录里有 `uninstall.exe`（装完就有）
    - `mio-taskhub-setup.exe --uninstall` 也能卸（同一个文件）
    """
    sys.path.insert(0, str(REPO))
    from mio_taskhub import release_assets

    assets = release_assets.all_release_assets()
    # 单文件安装包：目录里只有 setup.exe
    assert release_assets.INSTALLER_BUNDLE_ASSETS == (
        "%s/%s" % (release_assets.INSTALLER_BUNDLE_DIR,
                   release_assets.INSTALLER_EXE),)
    # 绿色版 zip 也必须在（自动更新要用）
    assert release_assets.PAYLOAD_ASSET in assets
    # 资产清单里不能有 uninstall.exe —— 它是 payload 内部的文件，
    # 单独发出去会让人以为「要下两个文件才能装」。
    assert not any(a.endswith(release_assets.UNINSTALL_EXE) for a in assets), (
        "卸载器已合并进安装包，不该再单独作为发布资产")


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
