# one-click configure WorkBuddy's codebuddy CLI as mio-taskhub night worker
$ErrorActionPreference = 'Stop'

$cliCandidates = @(
    "E:\Program Files\WorkBuddy\resources\app.asar.unpacked\cli\bin\codebuddy",
    "C:\Program Files\WorkBuddy\resources\app.asar.unpacked\cli\bin\codebuddy"
)
$codebuddy = $cliCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $codebuddy) {
    Write-Host '[Error] codebuddy CLI not found. Install WorkBuddy desktop first.'
    exit 1
}
Write-Host "[1/3] found CLI: $codebuddy"

$exe = Join-Path $PSScriptRoot 'mio-taskhub.exe'
if (-not (Test-Path $exe)) { Write-Host '[Error] mio-taskhub.exe not found next to this script.'; exit 1 }

# [2/3] write MCP config pointing at local hub
$mioDir = Join-Path $env:USERPROFILE '.mio_taskhub'
New-Item -ItemType Directory -Force -Path $mioDir | Out-Null
$exeEscaped = $exe.Replace('\', '\\')
$mcpCfg = @"
{
  "mcpServers": {
    "mio-taskhub": {
      "command": "$exeEscaped",
      "args": ["mcp"],
      "env": {}
    }
  }
}
"@
$mcpPath = Join-Path $mioDir 'codebuddy-mcp.json'
[System.IO.File]::WriteAllText($mcpPath, $mcpCfg, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "[2/3] wrote $mcpPath"

# [3/3] append night-runner agent entry example
$prompt = '你是 mio-taskhub 夜班执行者（hub: {url}）。工作循环直到 07:00：(1) taskhub_register 注册为 codebuddy 后 taskhub_claim 领任务；连续 3 次无任务则退出。(2) 领到后读 description/acceptance_criteria/spec_path/plan_path 完成。(3) 每 5 分钟 taskhub_heartbeat。(4) 完成即 taskhub_submit_result（失败也提交 success=false 附原因）。不跳过 submit；阻塞超 30 分钟换任务；全程自主决策。'
$agentEntry = @{
    agent = 'codebuddy'; agent_type = 'cli'
    command = "node `"$codebuddy`" -p `"$prompt`" --dangerously-skip-permissions --mcp-config `"$mcpPath`""
    cwd = ''
} 
$examplePath = Join-Path $mioDir 'night_runner.example.json'
$example = if (Test-Path $examplePath) { Get-Content $examplePath -Raw | ConvertFrom-Json } else {
    [pscustomobject]@{ enabled = $false; window_start = '22:00'; window_end = '07:00'; agents = @() }
}
$example.agents += $agentEntry
$example | ConvertTo-Json -Depth 4 | Set-Content -Path $examplePath -Encoding UTF8
Write-Host "[3/3] updated $examplePath"
Write-Host ''
Write-Host '[Done] copy night_runner.example.json -> night_runner.json, set enabled=true to activate.'
