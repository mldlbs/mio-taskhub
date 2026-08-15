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

# ---------- 5) 引导注入（AGENTS.md / CLAUDE.md，让 agent 在对话里会用 taskhub） ----------
$guide = @'
## mio-taskhub 使用规范

mio-taskhub 是本地跨 agent 任务中心（服务 http://127.0.0.1:48620）。用户通过对话使用，全程无需离开聊天窗。

### 触发时机
- 用户提到：任务、看板、派活、进度、待办、活干完没、安排 等词时，主动调用 taskhub_status。

### 看板渲染
- 把 taskhub_status 结果渲染成 markdown 表格（阶段 | 数量 | 任务列表），不要贴原始 JSON。
- 只读询问（看进度/看板/详情）时只调 taskhub_status / taskhub_get_task / taskhub_list_tasks，不创建不修改。

### 建任务
- 用 taskhub_create_task 创建，写清 description 与 acceptance_criteria。提交前先向用户复述标题+描述+验收标准确认。

### 推进阶段
- taskhub_advance_stage：→design 需 spec_path，→planning 需 plan_path，→done 需 review_result；产出物缺失先向用户要。

### 执行与汇报
- taskhub_register → taskhub_claim → 执行中 taskhub_heartbeat → 完成后 taskhub_submit_result，并向用户一句话汇报结果。

### 想法与讨论（需求发酵）
- 用户说「记个想法 / 有需求 / 有个点子」时，用 taskhub_add_idea 记录，提示用户可在界面「想法」查看/推进。
- 用户说「开会 / 讨论一下」时：taskhub_open_discussion（绑定 idea 或 task）→ taskhub_discussion_messages 读取 → taskhub_reply_discussion 回复；需要决策用 role=ask 提问。
- 讨论有结论用 taskhub_close_discussion 写结论，可据此把想法推进为 formed / broken_down。

### 嵌入视图
- 用户要看可视化流程图时，打开 http://127.0.0.1:48620/#/embed（workbuddy 可用产物面板嵌入）。
'@

function Add-TaskhubGuide {
    param([string]$Path, [string]$Label)
    try {
        if (-not (Test-Path $Path)) {
            New-Item -ItemType Directory -Force -Path (Split-Path $Path) | Out-Null
            [IO.File]::WriteAllText($Path, $guide + "`r`n", (New-Object System.Text.UTF8Encoding($true)))
            Write-Host "[OK] $Label     → 已创建并注入使用规范"
        }
        else {
            $t = [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8)
            if ($t -match 'mio-taskhub 使用规范') {
                Write-Host "[OK] $Label     → 已存在使用规范"
            }
            else {
                [IO.File]::WriteAllText($Path, $t.TrimEnd() + "`r`n`r`n" + $guide + "`r`n", (New-Object System.Text.UTF8Encoding($true)))
                Write-Host "[OK] $Label     → 已追加使用规范"
            }
        }
        return $true
    }
    catch {
        Write-Host "[跳过] $Label 引导注入失败（不影响其他配置）"
        return $false
    }
}

Add-TaskhubGuide (Join-Path $env:USERPROFILE '.config\opencode\AGENTS.md') 'opencode   '
Add-TaskhubGuide (Join-Path $env:USERPROFILE '.codex\AGENTS.md')           'codex      '
Add-TaskhubGuide (Join-Path $env:USERPROFILE '.claude\CLAUDE.md')          'claude code'
Add-TaskhubGuide (Join-Path $env:USERPROFILE '.agents\AGENTS.md')          'hermes     '

# ---------- 6) workbuddy taskhub 助手技能 ----------
$skillSrc = Join-Path $PSScriptRoot 'workbuddy\taskhub-skill'
$wbSkills = Join-Path $env:USERPROFILE '.workbuddy\skills'
if (-not (Test-Path $skillSrc)) {
    Write-Host '[跳过] workbuddy 技能源目录缺失（重新解压安装包即可）'
}
elseif (-not (Test-Path $wbSkills)) {
    Write-Host '[跳过] workbuddy 技能目录不存在，请先安装 workbuddy'
}
else {
    $target = Join-Path $wbSkills 'taskhub-assistant'
    if (Test-Path $target) {
        Write-Host '[OK] workbuddy   → taskhub 助手技能已存在'
    }
    else {
        try {
            Copy-Item -Recurse -Force $skillSrc $target
            Write-Host '[OK] workbuddy   → 已安装 taskhub 助手技能'
            $done = $true
        }
        catch {
            Write-Host '[跳过] workbuddy 技能安装失败，请手动复制 packaging/workbuddy/taskhub-skill'
        }
    }
}

# ---------- 汇总 ----------
Write-Host ''
if ($done) {
    Write-Host '全部完成！重启对应的 agent，然后在对话里说：'
    Write-Host '  「看看现在有什么任务」或「帮我把 XX 记成一个任务」'
    Write-Host '  workbuddy 还会自动安装「taskhub 助手」技能，可在技能栏查看。'
}
else {
    Write-Host '没有配置任何 agent。请确认你用的 agent 是哪一种。'
}
Write-Host ''
Read-Host '按回车退出'
