# mio-taskhub 绿色版一键打包脚本
# 用法: powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1
# 产物: dist/mio-taskhub/ （免 Python 绿色版）+ dist/mio-taskhub-绿色版.zip
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---------- 1) 前端构建 ----------
Write-Host ''
Write-Host '[1/5] 构建前端 (web/npm run build) ...'
if (-not (Test-Path 'web\node_modules')) {
    Write-Host '  node_modules 缺失，先 npm install ...'
    Push-Location web
    npm install
    if ($LASTEXITCODE -ne 0) { throw 'npm install 失败' }
    Pop-Location
}
Push-Location web
npm run build
$buildExit = $LASTEXITCODE
Pop-Location
if ($buildExit -ne 0) { throw '前端构建失败（npm run build）' }

# ---------- 2) 清理旧产物 ----------
Write-Host '[2/5] 清理旧 build/dist ...'
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

# ---------- 3) PyInstaller 打包 ----------
Write-Host '[3/5] PyInstaller 打包 (mio-taskhub.spec) ...'
python -m PyInstaller mio-taskhub.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败' }

$distDir = Join-Path $root 'dist\mio-taskhub'
if (-not (Test-Path $distDir)) { throw "未找到打包产物: $distDir" }

# ---------- 4) 复制分发文件 ----------
Write-Host '[4/5] 复制分发文件 (setup 脚本 / 使用说明 / workbuddy skill / widget 入口) ...'
$distFiles = @(
    'setup-agent.bat', 'setup-agent.ps1',
    'setup-opencode.bat', 'setup-opencode.ps1',
    '使用说明.txt', 'mio-taskhub-widget.bat'
)
foreach ($f in $distFiles) {
    $src = Join-Path $root "packaging\$f"
    if (-not (Test-Path $src)) { throw "缺少分发文件: $src" }
    Copy-Item -Force $src $distDir
}
if (Test-Path (Join-Path $root 'packaging\workbuddy')) {
    Copy-Item -Recurse -Force (Join-Path $root 'packaging\workbuddy') $distDir
}

# ---------- 5) 压缩绿色版 ----------
Write-Host '[5/5] 生成 dist/mio-taskhub-绿色版.zip ...'
$zip = Join-Path $root 'dist\mio-taskhub-绿色版.zip'
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $distDir '*') -DestinationPath $zip

$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ''
Write-Host "[完成] 绿色版: $zip ($size MB)"
Write-Host "       免解压目录: $distDir"
