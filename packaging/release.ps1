# Release mio-taskhub to GitHub Releases using the REST API (no gh CLI required).
#
# Usage:
#   $env:GH_TOKEN = "<classic PAT, scope: public_repo>"
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging/release.ps1 -Version 0.4.0 -Notes "..."
#
#   -Prerelease   mark the release as prerelease (channel=prerelease in latest.json)
#   -DryRun       build is skipped unless -SkipBuild is omitted; prints what would happen, makes no API calls
#   -SkipBuild    reuse the existing dist/mio-taskhub-win64.zip instead of rebuilding
#
# Requires: dist/mio-taskhub-win64.zip (produced by build.ps1) and GH_TOKEN for real runs.
param(
  [Parameter(Mandatory=$true)][string]$Version,
  [string]$Notes = "",
  [switch]$Prerelease,
  [switch]$DryRun,
  [switch]$SkipBuild
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$Owner     = 'mldlbs'
$Repo      = 'mio-taskhub'
$AssetName = 'mio-taskhub-win64.zip'
$tag       = "v$Version"

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
  Write-Host "[dry-run] would POST $api/releases then upload: $AssetName, latest.json"
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
try {
  $rel = Invoke-RestMethod -Method Post -Uri "$api/releases" -Headers $headers -ContentType 'application/json' -Body $releaseBody
} catch {
  throw "create release failed: $($_.Exception.Message) $($_.ErrorDetails.Message)"
}
$uploadUrl = $rel.upload_url -replace '\{\?name,label\}', ''
Write-Host "  release id=$($rel.id)"

function Upload-Asset([string]$filePath) {
  $name = Split-Path -Leaf $filePath
  $bytes = [System.IO.File]::ReadAllBytes($filePath)
  $h = @{ Authorization = "Bearer $token"; 'User-Agent' = 'mio-taskhub-release' }
  Invoke-RestMethod -Method Post -Uri "$uploadUrl?name=$name" -Headers $h `
    -ContentType 'application/octet-stream' -Body $bytes | Out-Null
  Write-Host "  uploaded $name"
}

Write-Host "[5/5] upload assets ..."
Upload-Asset $zip
Upload-Asset $jsonPath

Write-Host "[done] https://github.com/$Owner/$Repo/releases/tag/$tag"
