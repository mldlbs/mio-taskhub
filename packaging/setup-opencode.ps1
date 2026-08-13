# one-click configure opencode for mio-taskhub
$ErrorActionPreference = 'Stop'
$mcpExe = Join-Path $PSScriptRoot 'mio-taskhub-mcp.exe'

if (-not (Test-Path $mcpExe)) {
    Write-Host '[Error] cannot find mio-taskhub-mcp.exe in this folder.'
    Read-Host 'Press Enter to exit'
    exit 1
}

$configDir = Join-Path $env:USERPROFILE '.config\opencode'
$config = Join-Path $configDir 'opencode.jsonc'
New-Item -ItemType Directory -Force -Path $configDir | Out-Null

$exeEscaped = $mcpExe.Replace('\', '\\')
$entry = @"
  "mcp": {
    "mio-taskhub": {
      "type": "local",
      "command": ["$exeEscaped"],
      "enabled": true
    }
  }
"@

if (-not (Test-Path $config)) {
    @"
{
$entry
}
"@ | Set-Content -Path $config -Encoding UTF8
    Write-Host ''
    Write-Host '[Done] created:'
    Write-Host "       $config"
    Write-Host ''
    Write-Host 'Next: restart opencode, then ask it to claim tasks via mio-taskhub.'
    Read-Host 'Press Enter to exit'
    exit 0
}

$text = [IO.File]::ReadAllText($config, [Text.Encoding]::UTF8)
if ($text -match 'mio-taskhub') {
    Write-Host ''
    Write-Host '[Note] mio-taskhub already configured. No change needed.'
    Write-Host '       If it did not work before, check for duplicate "mcp" blocks or restart opencode.'
    Read-Host 'Press Enter to exit'
    exit 0
}

$trimmed = $text.TrimEnd()
if ($trimmed.EndsWith('}')) {
    $tail = $trimmed.Substring(0, $trimmed.Length - 1).TrimEnd()
    $newText = if ($tail.EndsWith('{')) {
        $tail + "`r`n" + $entry + "`r`n}"
    } else {
        $tail + ",`r`n" + $entry + "`r`n}"
    }
    [IO.File]::WriteAllText($config, $newText, [Text.Encoding]::UTF8)
    Write-Host ''
    Write-Host '[Done] wrote mio-taskhub config:'
    Write-Host "       $config"
    Write-Host ''
    Write-Host 'Next: restart opencode, then ask it to claim tasks via mio-taskhub.'
    Read-Host 'Press Enter to exit'
    exit 0
}

Write-Host '[Warning] could not auto-edit config. Please manually add to opencode.jsonc:'
Write-Host ''
Write-Host $entry
Read-Host 'Press Enter to exit'
exit 1
