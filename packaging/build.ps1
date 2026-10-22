# mio-taskhub 一键打包（自动杀进程 + 验证依赖 + 重启）
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1 -Quick   (跳过 zip，只打包+重启)
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1 -Quick -Version 0.4.0  (注入版本号)
param([switch]$Quick, [string]$Version = "")
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---------- 0) 杀掉所有 mio-taskhub 进程 ----------
Write-Host ''
Write-Host '[0/6] 杀掉所有 mio-taskhub 进程 ...'
Get-WmiObject Win32_Process | Where-Object {
    $_.Name -eq 'mio-taskhub.exe'
} | ForEach-Object {
    Write-Host "  killing PID $($_.ProcessId): $($_.ProcessName)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

# ---------- 0.5) 注入版本号 ----------
if ($Version) {
    Write-Host "[0.5/6] 注入版本号 $Version ..."
    $verFile = Join-Path $root 'mio_taskhub\version.py'
    $content = @"
# mio_taskhub/version.py
# -*- coding: utf-8 -*-
"""(自动生成) 单一版本源。"""
import sys
from pathlib import Path

__version__ = "$Version"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
"@
    Set-Content -Path $verFile -Value $content -Encoding UTF8
}

# ---------- 1) 前端构建 ----------
Write-Host '[1/6] 构建前端 ...'
Push-Location web
if (-not (Test-Path 'node_modules')) {
    & npm install
    if ($LASTEXITCODE -ne 0) { throw 'npm install 失败' }
}
& npm run build
if ($LASTEXITCODE -ne 0) { throw 'npm run build 失败' }
Pop-Location

# ---------- 2) 清理旧产物 ----------
Write-Host '[2/6] 清理旧 build/dist ...'
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# ---------- 3) PyInstaller 打包 ----------
Write-Host '[3/6] PyInstaller 打包 ...'
& "E:\work\code\agent-dev\mio-taskhub\.venv\Scripts\python.exe" -m PyInstaller mio-taskhub.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 失败' }

$distDir = Join-Path $root 'dist\mio-taskhub-v3'
$internalDir = Join-Path $distDir '_internal'

# ---------- 4) 验证关键依赖 ----------
Write-Host '[4/6] 验证打包产物 ...'
$checks = @(
    @{ Path = "$internalDir\PIL";               Label = 'PIL/Pillow' },
    @{ Path = "$internalDir\PIL\_imaging*.pyd";  Label = '_imaging native' },
    @{ Path = "$internalDir\web\dist\index.html"; Label = 'web/dist/index.html' },
    @{ Path = "$internalDir\web\dist\assets";    Label = 'web/dist/assets' },
    @{ Path = "$distDir\mio-taskhub.exe";        Label = 'mio-taskhub.exe' }
)
$failed = $false
foreach ($c in $checks) {
    if (Test-Path $c.Path) {
        Write-Host "  OK: $($c.Label)" -ForegroundColor Green
    } else {
        Write-Host "  MISSING: $($c.Label) ($($c.Path))" -ForegroundColor Red
        $failed = $true
    }
}
if ($failed) { throw '关键依赖缺失，打包可能不完整' }

# ---------- 4.5) 构建安装器 / 卸载器 ----------
Write-Host '[4.5/6] 构建安装器 / 卸载器 ...'
$installerDist = Join-Path $root 'dist_installer'
Remove-Item -Recurse -Force $installerDist -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path $root 'build_installer') -ErrorAction SilentlyContinue
& "$root\.venv\Scripts\python.exe" -m PyInstaller (Join-Path $root 'mio-taskhub-installer.spec') `
    --noconfirm --clean --distpath $installerDist --workpath (Join-Path $root 'build_installer')
if ($LASTEXITCODE -ne 0) { throw '安装器打包失败' }
$setupExe = Join-Path $installerDist 'mio-taskhub-setup.exe'
$uninstallExe = Join-Path $installerDist 'uninstall.exe'
foreach ($p in @($setupExe, $uninstallExe)) {
    if (-not (Test-Path $p)) { throw "缺少安装器产物：$p" }
}
# **只把 uninstall.exe 放进 payload（程序本体）**。
# 不放 setup.exe —— 它是「安装器」，属于分发包那一层，不属于程序本体：
#   ① 合并后的 setup.exe 有 66 MB，放进 payload 会让绿色版 zip 白涨 10 MB；
#   ② 放进 payload 的那份是**未合并版**（尾部没有 payload），双击必然
#      报「找不到安装包」—— 是个坏入口，不如没有；
#   ③ 自动更新是「整体替换安装目录」，不需要目录里有安装器。
Copy-Item -Force $uninstallExe $distDir
Write-Host '  OK: uninstall.exe（payload 内的纯卸载器）' -ForegroundColor Green

# ---------- 5) 复制分发文件 ----------
Write-Host '[5/6] 复制分发文件 ...'
$distFiles = @(
    'setup-agent.bat', 'setup-agent.ps1',
    'setup-opencode.bat', 'setup-opencode.ps1',
    '使用说明.txt', 'mio-taskhub-widget.bat',
    'agent_wrapper.py', 'idle_worker.py'
)
foreach ($f in $distFiles) {
    $src = Join-Path $root "packaging\$f"
    if (Test-Path $src) { Copy-Item -Force $src $distDir }
}
if (Test-Path (Join-Path $root 'packaging\workbuddy')) {
    Copy-Item -Recurse -Force (Join-Path $root 'packaging\workbuddy') $distDir
}

# ---------- 6) 压缩 + 重启 ----------
if (-not $Quick) {
    Write-Host '[6/6] 生成 zip ...'
$zip = Join-Path $root 'dist\mio-taskhub-win64.zip'
Remove-Item -Force $zip -ErrorAction SilentlyContinue
# 用 scripts/make_zip.py 而非 Compress-Archive：
#   ① base_library.zip 是只读的 → Compress-Archive 直接抛 UnauthorizedAccessError
#   ② Compress-Archive 的输出布局不稳定（实测发布资产顶层带一层 mio-taskhub-v3/）
#      而更新器按平铺校验 → 自动更新装不上
#   ③ 成员顺序/时间戳不稳定 → 不可复现
& "$root\.venv\Scripts\python.exe" (Join-Path $root 'scripts\make_zip.py') $distDir $zip
if ($LASTEXITCODE -ne 0) { throw '生成 zip 失败' }
$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host "  zip: $zip ($size MB)" -ForegroundColor Green

# 独立安装包：**单文件** setup.exe（payload 已合并进去）
# 用户只下一个文件、双击、装完就走 —— 跟 NSIS/Inno 的安装器一样。
#
# **目录里只有这一个 exe**：payload（含 uninstall.exe）已经合并进去了，
# 不再单独发一份 uninstall.exe —— 那只是同一份内容的副本，白占 10 MB
# 还要让用户纠结「装的时候用哪个」。
$bundle = Join-Path $root 'dist\mio-taskhub-installer'
Remove-Item -Recurse -Force $bundle -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
# 把 payload zip 追加到 setup.exe 尾部 → 单文件安装包
& "$root\.venv\Scripts\python.exe" (Join-Path $root 'scripts\make_setup.py') `
    --setup $setupExe --payload $zip `
    --out (Join-Path $bundle 'mio-taskhub-setup.exe') --verify
if ($LASTEXITCODE -ne 0) { throw '合并单文件安装包失败' }
Copy-Item -Force (Join-Path $root 'packaging\安装说明.txt') $bundle
Write-Host "  安装包目录: $bundle（单文件）" -ForegroundColor Green

# 一致性校验：安装器 exe ↔ payload zip 里的 uninstall.exe ↔ 独立安装包目录
# 必须同源。实测踩过：重建了 uninstall.exe 却没重打 zip → 用户装到的是
# 旧卸载器 → 静默卸载异常退出（rc=23、目录还在）。
& "$root\.venv\Scripts\python.exe" (Join-Path $root 'scripts\check_installer_bundle.py')
if ($LASTEXITCODE -ne 0) { throw '安装器分发产物不一致' }
} else {
    Write-Host '[6/6] Quick 模式，跳过 zip ...'
}

# ---------- 启动 hub 模式 ----------
Write-Host ''
Write-Host '启动 hub 模式 ...'
Start-Process -FilePath (Join-Path $distDir 'mio-taskhub.exe') -ArgumentList 'hub'
Start-Sleep -Seconds 4

# 验证
$proc = Get-Process -Name "mio-taskhub" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($proc) {
    $logTail = Get-Content "C:\Users\admin\.mio_taskhub\runtime.log" -Tail 2 -ErrorAction SilentlyContinue
    Write-Host "  PID: $($proc.Id)" -ForegroundColor Green
    Write-Host "  Log: $logTail" -ForegroundColor Green
} else {
    Write-Host "  WARNING: 进程未启动，请检查日志" -ForegroundColor Yellow
}

Write-Host ''
Write-Host "[完成] $distDir" -ForegroundColor Green
