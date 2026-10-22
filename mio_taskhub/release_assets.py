# -*- coding: utf-8 -*-
"""发布脚本的资产清单（发版时该上传哪些文件、latest.json 怎么写）。

**单一事实源**：`packaging/release.ps1` 与 `scripts/release_checklist.py`
都从这里取，避免「打包产了但没上传」「latest.json 指向不存在的资产」。

资产分两类：
- `payload`：程序本体（绿色版 zip）—— 客户端自动更新用它
- `installer`：独立安装包（setup exe + payload）—— 给人手动安装用
"""
from __future__ import annotations

#: 客户端自动更新用的程序 zip（latest.json 的 assets 里指向它）
PAYLOAD_ASSET = "mio-taskhub-win64.zip"

#: 独立安装包目录名（build.ps1 产出：dist/mio-taskhub-installer/）
INSTALLER_BUNDLE_DIR = "mio-taskhub-installer"

#: 独立安装包里的安装器 exe（用户双击它）
INSTALLER_EXE = "mio-taskhub-setup.exe"

#: 独立安装包里的卸载器 exe
#:
#: **必须单独上传**：用户可能只想要「卸载程序」这一份文件
#: （比如从「应用和功能」里点卸载、或者把安装目录里的 uninstall.exe
#: 拷到别处用）。不单独发的话，用户拿不到独立可执行的卸载器。
UNINSTALL_EXE = "uninstall.exe"

#: 安装包目录内要上传的资产（相对 dist/）
#:
#: **单文件安装包**：`mio-taskhub-setup.exe` 已经把 payload zip 合并进去了
#: （self-extracting），用户只下一个文件就能装 → **不需要**再单独发一份
#: payload zip 给安装器用。
#:
#: 但 payload zip 仍要作为**顶层资产**上传 —— 客户端自动更新用它。
INSTALLER_BUNDLE_ASSETS = (
    "%s/%s" % (INSTALLER_BUNDLE_DIR, INSTALLER_EXE),
    "%s/%s" % (INSTALLER_BUNDLE_DIR, UNINSTALL_EXE),
)

#: latest.json 里 assets[].format 的取值
PAYLOAD_FORMAT = "zip"

#: 自更新支持的平台（与 update/manifest.py 的 asset_for 保持一致）
PAYLOAD_OS = "windows"
PAYLOAD_ARCH = "x64"


def all_release_assets() -> tuple:
    """所有需要上传的资产（相对 dist/ 目录）。"""
    return (PAYLOAD_ASSET,) + INSTALLER_BUNDLE_ASSETS
