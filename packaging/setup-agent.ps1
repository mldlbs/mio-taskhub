# 一键配置各类 agent 连接 mio-taskhub
# 自动检测：opencode / claude code / codex / workbuddy，有则配，无则跳过
$ErrorActionPreference = 'Stop'
$mcpExe = Join-Path $PSScriptRoot 'mio-taskhub-mcp.exe'

if (-not (Test-Path $mcpExe)) {
    Write-Host '[错误] 找不到 mio-taskhub-mcp.exe，请确认它在解压文件夹里。'
    Read-Host '按回车退出'
    exit 1
}

Write-Host ''
Write-Host '========================================'
Write-Host '  mio-taskhub 一键配置 agent'
Write-Host '========================================'
Write-Host ''
$done = $false

# ---------- 1) opencode ----------
$ocDir = Join-Path $env:USERPROFILE '.config\opencode'
$ocCfg = Join-Path $ocDir 'opencode.jsonc'
New-Item -ItemType Directory -Force -Path $ocDir | Out-Null
$exeEscaped = $mcpExe.Replace('\', '\\')
$ocEntry = @"
  "mcp": {
    "mio-taskhub": {
      "type": "local",
      "command": ["$exeEscaped"],
      "enabled": true
    }
  }
"@
if (-not (Test-Path $ocCfg)) {
    @"
{
$ocEntry
}
"@ | Set-Content -Path $ocCfg -Encoding UTF8
    Write-Host '[OK] opencode     → 已创建配置'
    $done = $true
}
else {
    $t = [IO.File]::ReadAllText($ocCfg, [Text.Encoding]::UTF8)
    if ($t -match 'mio-taskhub') {
        Write-Host '[OK] opencode     → 已存在配置'
    }
    elseif ($t.TrimEnd().EndsWith('}')) {
        $tail = $t.TrimEnd().Substring(0, $t.TrimEnd().Length - 1).TrimEnd()
        $newText = if ($tail.EndsWith('{')) {
            $tail + "`r`n" + $ocEntry + "`r`n}"
        } else {
            $tail + ",`r`n" + $ocEntry + "`r`n}"
        }
        [IO.File]::WriteAllText($ocCfg, $newText, [Text.Encoding]::UTF8)
        Write-Host '[OK] opencode     → 已追加配置'
        $done = $true
    }
    else {
        Write-Host '[跳过] opencode 配置结构异常，请手动参考使用说明'
    }
}

# ---------- 2) codex ----------
$cxCfg = Join-Path $env:USERPROFILE '.codex\config.toml'
$tomlSingle = $mcpExe.Replace("'", "''")
$tomlBlock = "`r`n[mcp_servers.mio-taskhub]`r`ncommand = '$tomlSingle'`r`nargs = []`r`n"
if (-not (Test-Path $cxCfg)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $cxCfg) | Out-Null
    "`n$tomlBlock" | Set-Content -Path $cxCfg -Encoding UTF8
    Write-Host '[OK] codex        → 已创建配置'
    $done = $true
}
else {
    $t = [IO.File]::ReadAllText($cxCfg, [Text.Encoding]::UTF8)
    if ($t -match 'mcp_servers.mio-taskhub') {
        Write-Host '[OK] codex        → 已存在配置'
    }
    else {
        [IO.File]::AppendAllText($cxCfg, $tomlBlock, [Text.Encoding]::UTF8)
        Write-Host '[OK] codex        → 已追加配置'
        $done = $true
    }
}

# ---------- 3) claude code ----------
$claudeCmd = Get-Command claude -ErrorAction SilentlyContinue
if ($claudeCmd) {
    try {
        & claude mcp add mio-taskhub -- "$mcpExe" 2>$null | Out-Null
        Write-Host '[OK] claude code  → 已通过 claude mcp add 配置'
        $done = $true
    }
    catch {
        Write-Host '[跳过] claude code 自动配置失败，请手动执行:'
        Write-Host "       claude mcp add mio-taskhub -- `"$mcpExe`""
    }
}
else {
    Write-Host '[跳过] claude code 未安装（没找到 claude 命令）'
}

# ---------- 4) workbuddy ----------
$wbCfg = Join-Path $env:USERPROFILE '.workbuddy\mcp.json'
if (-not (Test-Path $wbCfg)) {
    New-Item -ItemType Directory -Force -Path (Split-Path $wbCfg) | Out-Null
    $wbObj = [ordered]@{ mcpServers = [ordered]@{ 'mio-taskhub' = [ordered]@{ command = $mcpExe; args = @() } } }
    [IO.File]::WriteAllText($wbCfg, ($wbObj | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))
    Write-Host '[OK] workbuddy   → 已创建配置'
    $done = $true
}
else {
    try {
        $wbObj = Get-Content $wbCfg -Raw | ConvertFrom-Json
        $hasWb = $wbObj.mcpServers.PSObject.Properties.Name -contains 'mio-taskhub'
        if ($hasWb) {
            Write-Host '[OK] workbuddy   → 已存在配置'
        }
        else {
            if (-not $wbObj.mcpServers) {
                $wbObj | Add-Member -NotePropertyName 'mcpServers' -NotePropertyValue @{}
            }
            $wbObj.mcpServers | Add-Member -NotePropertyName 'mio-taskhub' -NotePropertyValue ([ordered]@{ command = $mcpExe; args = @() })
            [IO.File]::WriteAllText($wbCfg, ($wbObj | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
            Write-Host '[OK] workbuddy   → 已追加配置'
            $done = $true
        }
    }
    catch {
        Write-Host '[跳过] workbuddy 配置解析失败，请手动把下面内容加入 mcp.json:'
        Write-Host "       `"mio-taskhub`": { `"command`": `"$mcpExe`", `"args`": [] }"
    }
}

# ---------- 汇总 ----------
Write-Host ''
if ($done) {
    Write-Host '全部完成！重启对应的 agent，然后在对话里说：'
    Write-Host '  「使用 mio-taskhub：先注册，然后领取任务并执行」'
}
else {
    Write-Host '没有配置任何 agent。请确认你用的 agent 是哪一种。'
}
Write-Host ''
Read-Host '按回车退出'
