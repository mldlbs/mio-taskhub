# mio-taskhub 一键打包（自动杀进程 + 验证依赖 + 重启）
# 用法:
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging/build.ps1 -Quick   (跳过 zip，只打包+重启)
param([switch]$Quick)
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# ---------- 0) 杀掉所有 mio-taskhub 进程 ----------
Write-Host ''
Write-Host '[0/6] 杀掉所有 mio-taskhub 进程 ...'
Get-WmiObject Win32_Process | Where-Object {
    $_.CommandLine -like '*mio-taskhub*' -and $_.CommandLine -notlike '*mcp*'
} | ForEach-Object {
    Write-Host "  killing PID $($_.ProcessId): $($_.ProcessName)"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 2

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
& python -m PyInstaller mio-taskhub.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 失败' }

$distDir = Join-Path $root 'dist\mio-taskhub'
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

# ---------- 5) 复制分发文件 ----------
Write-Host '[5/6] 复制分发文件 ...'
$distFiles = @(
    'setup-agent.bat', 'setup-agent.ps1',
    'setup-opencode.bat', 'setup-opencode.ps1',
    '使用说明.txt', 'mio-taskhub-widget.bat'
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
    $zip = Join-Path $root 'dist\mio-taskhub-绿色版.zip'
    Remove-Item -Force $zip -ErrorAction SilentlyContinue
    Compress-Archive -Path (Join-Path $distDir '*') -DestinationPath $zip
    $size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
    Write-Host "  zip: $zip ($size MB)" -ForegroundColor Green
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
