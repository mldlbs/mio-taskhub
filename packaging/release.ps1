# Release mio-taskhub to GitHub Releases using the REST API (no gh CLI required).
#
# Usage:
#   $env:GH_TOKEN = "<classic PAT, scope: public_repo>"
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging/release.ps1 -Version 0.4.0 -Notes "..."
#
#   -Prerelease   mark the release as prerelease (channel=prerelease in latest.json)
#   -DryRun       build is skipped unless -SkipBuild is omitted; prints what would happen, makes no API calls
#   -SkipBuild    reuse the existing dist/mio-taskhub-win64.zip instead of rebuilding
#   -GateReport   可选素材门禁报告（传了才校验；素材质量与发布包内容无关，默认不查）
#
# Requires: dist/mio-taskhub-win64.zip (produced by build.ps1) and GH_TOKEN for real runs.
param(
  [Parameter(Mandatory=$true)][string]$Version,
  [string]$Notes = "",
  [switch]$Prerelease,
  [switch]$DryRun,
  [switch]$SkipBuild,
  [string]$GateReport = ""
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 素材门禁报告：可选（2026-11-13 解绑）。发布包是纯软件产物，不含素材数据
# （zip 已实证 0 个 material/db 文件），素材质量与发布内容无关；素材线已于
# 2026-10-08 暂停。仍传 -GateReport 时会照常校验（绑定 commit 与 DB sha）。
if (-not $DryRun -and $GateReport) {
  if (-not (Test-Path $GateReport)) {
    throw "Gate report not found: $GateReport"
  }
  & "$root\.venv\Scripts\python.exe" "$root\scripts\verify_gate_report.py" --report $GateReport
  if ($LASTEXITCODE -ne 0) {
    throw 'Gate report verification failed; release blocked.'
  }
} elseif (-not $DryRun) {
  Write-Warning '-GateReport 未提供：跳过素材门禁校验（素材质量与发布包内容无关）。'
}

$Owner     = 'mldlbs'
$Repo      = 'mio-taskhub'
$AssetName = 'mio-taskhub-win64.zip'
$tag       = "v$Version"

# 资产清单以 mio_taskhub/release_assets.py 为准（唯一事实源）
# 用 python 读，避免 ps1 里再抄一份清单（抄两份就会漂移）。
$assetListJson = & "$root\.venv\Scripts\python.exe" -c @"
import json, sys
sys.path.insert(0, r'$root')
from mio_taskhub.release_assets import all_release_assets
print(json.dumps(list(all_release_assets())))
"@
if ($LASTEXITCODE -ne 0) { throw '读取发布资产清单失败' }
$installerAssets = $assetListJson | ConvertFrom-Json

$token = $env:GH_TOKEN
if (-not $token) { $token = $env:GITHUB_TOKEN }
if (-not $token) {
  # Fallback: reuse the credential git already stores for github.com
  # (Windows Credential Manager target: git:https://github.com).
  try {
    $raw = "protocol=https`nhost=github.com`n`n" | git credential fill 2>$null
    $m = ($raw | Select-String -Pattern '^password=(.*)$').Matches
    if ($m.Count -gt 0) { $token = $m[0].Groups[1].Value }
  } catch { $token = $null }
}
if (-not $token -and -not $DryRun) {
  throw 'No GitHub token: set GH_TOKEN, or ensure a git credential exists for https://github.com'
}

$headers = @{
  Authorization          = "Bearer $token"
  Accept                 = 'application/vnd.github+json'
  'User-Agent'           = 'mio-taskhub-release'
  'X-GitHub-Api-Version' = '2022-11-28'
}
$api = "https://api.github.com/repos/$Owner/$Repo"

if (-not $SkipBuild) {
  Write-Host "[1/5] Build $Version ..."
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'build.ps1') -Version $Version
  if ($LASTEXITCODE -ne 0) { throw 'build failed' }
} else {
  Write-Host "[1/5] skip build (-SkipBuild)"
}

$zip = Join-Path $root "dist\$AssetName"
if (-not (Test-Path $zip)) { throw "missing artifact: $zip (run build.ps1 first)" }

Write-Host "[2/5] sha256 / size ..."
$hash = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
$size = (Get-Item $zip).Length

Write-Host "[3/5] latest.json ..."
$url = "https://github.com/$Owner/$Repo/releases/download/$tag/$AssetName"
$manifest = [ordered]@{
  schema        = 1
  version       = $Version
  channel       = $(if ($Prerelease) { 'prerelease' } else { 'stable' })
  released_at   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
  min_supported = "0.3.0"
  mandatory     = $false
  notes         = $Notes
  assets        = @([ordered]@{
    os = "windows"; arch = "x64"; url = $url
    sha256 = $hash; size = $size; format = "zip"
  })
}
$jsonPath = Join-Path $root 'dist\latest.json'
$json = $manifest | ConvertTo-Json -Depth 6
# NOTE: PS 5.1 'Set-Content -Encoding UTF8' writes a BOM, which breaks the client's
# strict utf-8 json.loads (it would silently fall back to weak verification). Write no-BOM.
[System.IO.File]::WriteAllText($jsonPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  sha256=$hash size=$size"

if ($DryRun) {
  Write-Host "[dry-run] tag=$tag prerelease=$([bool]$Prerelease)"
  # 资产名从单一事实源取（不硬编码，避免与实际上传清单不一致）；
  # 去重 —— payload zip 既是顶层资产、也在安装包目录里。
  $allNames = @($AssetName) + @($installerAssets | ForEach-Object { Split-Path -Leaf $_ }) + @('latest.json')
  $uploadNames = $allNames | Select-Object -Unique
  Write-Host "[dry-run] would POST $api/releases then upload: $($uploadNames -join ', ')"
  Write-Host "[dry-run] latest.json:"
  Get-Content $jsonPath
  return
}

Write-Host "[4/5] create release $tag ..."
$releaseBody = @{
  tag_name   = $tag
  name       = $tag
  body       = $(if ($Notes) { $Notes } else { "Release $Version" })
  draft      = $false
  prerelease = [bool]$Prerelease
} | ConvertTo-Json
# PS 5.1 把字符串 body 按默认（ASCII 系）编码发送，中文会变 '?'。
# 必须显式 UTF-8 字节，与 latest.json 的 no-BOM 处理同理。
$releaseBodyBytes = [System.Text.Encoding]::UTF8.GetBytes($releaseBody)
try {
  $rel = Invoke-RestMethod -Method Post -Uri "$api/releases" -Headers $headers -ContentType 'application/json' -Body $releaseBodyBytes
} catch {
  throw "create release failed: $($_.Exception.Message) $($_.ErrorDetails.Message)"
}
$releaseId = $rel.id
$uploadUrl = "$($rel.upload_url)".Trim()
if (-not $uploadUrl -or $uploadUrl -notlike 'https://*') {
  # 防御：API 未给 upload_url 时按 uploads.github.com 稳定形态构造。
  $uploadUrl = "https://uploads.github.com/repos/$Owner/$Repo/releases/$releaseId/assets"
}
$uploadUrl = $uploadUrl -replace '\{\?name,label\}', ''
Write-Host "  release id=$releaseId"

function Upload-Asset([string]$filePath) {
  $name = Split-Path -Leaf $filePath
  $encoded = [System.Uri]::EscapeDataString($name)
  $bytes = [System.IO.File]::ReadAllBytes($filePath)
  $h = @{ Authorization = "Bearer $token"; 'User-Agent' = 'mio-taskhub-release' }
  # PowerShell 变量名允许 '?'：直接写 "$uploadUrl?name=..." 会被解析成变量
  # `uploadUrl?name`（空值），URL 只剩 "?name=..." → 「未能分析主机名」。
  # v0.7.0 发布即踩此坑，必须用 ${uploadUrl} 界定变量边界。
  Invoke-RestMethod -Method Post -Uri "${uploadUrl}?name=$encoded" -Headers $h `
    -ContentType 'application/octet-stream' -Body $bytes | Out-Null
  Write-Host "  uploaded $name"
}

Write-Host "[5/5] upload assets ..."
Upload-Asset $zip
Upload-Asset $jsonPath
# 独立安装包（给人手动安装用）：setup exe + payload 一起上传
foreach ($rel in $installerAssets) {
    $p = Join-Path $root $rel
    if (Test-Path $p) {
        Upload-Asset $p
    } else {
        Write-Host "  [skip] 缺少安装包资产：$rel" -ForegroundColor Yellow
    }
}

Write-Host "[done] https://github.com/$Owner/$Repo/releases/tag/$tag"
