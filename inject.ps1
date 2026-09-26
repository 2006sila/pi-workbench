[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('pideck', 'dsh')]
    [string]$Target,

    [string]$SourcePrompt,
    [string]$SkillsSource,
    [string]$AgentDir,

    [switch]$Uninstall,
    [switch]$Check,
    [switch]$NoSkills,
    [switch]$SkillsOnly,
    [string]$RemoveAddons,

    # 技能呈现模式：
    #   full（默认）= 65 个模块全部进系统提示词，AI 按描述自选
    #   menu         = 只留一个菜单技能进提示词，模块加 disable-model-invocation，按需 read
    [ValidateSet('full', 'menu', 'auto')]
    [string]$SkillMode = 'auto',

    # menu 模式下仍要保持「进提示词」的技能（分号分隔）。
    # 用于附属模板这类**行为纪律型**技能：它们靠描述自动触发才有意义，
    # 藏进菜单后就只能「被想起来才用」。
    [string]$MenuKeepAdvertised
)

Set-StrictMode -Off
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$TOOL_TAG  = 'pi-workbench'
$TOOL_VER  = '1.0.0'
$MARK_BEG  = '<!-- BEGIN ' + $TOOL_TAG + ' v4 -->'
$MARK_END  = '<!-- END '   + $TOOL_TAG + ' v4 -->'

# DSH 的 home 级 patch 层用 YAML 注释标记（YAML 不支持 HTML 注释）
$PATCH_BEG = '# BEGIN ' + $TOOL_TAG + ' v4'
$PATCH_END = '# END '   + $TOOL_TAG + ' v4'

# ---------------------------------------------------------------- 路径与环境

function Get-HomeDir {
    if ($env:USERPROFILE) { return $env:USERPROFILE }
    return $HOME
}

$HomeDir = Get-HomeDir

# DSH 的配置根：$DSH_HOME 优先，否则 ~/.dsh（与 dsh-agent-instructions 的 dshHome 解析一致）
$DshHomeDir = if ([string]::IsNullOrWhiteSpace($env:DSH_HOME)) { Join-Path $HomeDir '.dsh' } else { $env:DSH_HOME }

$TARGETS = @{
    'pideck' = @{
        Label       = 'PiDeck'
        AgentDir    = Join-Path $HomeDir '.pi\agent'
        PromptName  = 'APPEND_SYSTEM.md'
        ProcNames   = @('PiDeck', 'pi-desktop')
        ProbeFiles  = @(
            (Join-Path $HomeDir '.pi\agent\settings.json'),
            (Join-Path $HomeDir '.pi\agent\models.json')
        )
        ExeHints    = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\PiDeck\PiDeck.exe'),
            (Join-Path $env:ProgramFiles 'PiDeck\PiDeck.exe')
        )
    }
    'dsh' = @{
        # DeepSeek Harness：用户全局指令插件的 dshHome 按 $DSH_HOME 再 ~/.dsh 解析，
        # 它把 $DSH_HOME/AGENTS.md 作为持久 user 消息（<system-reminder>）注入提示词；
        # 技能走 dsh-skill-filesystem 的 user-dsh 根（rank 400）= $DSH_HOME/skills，跳过 .system。
        Label       = 'DeepSeek Harness'
        AgentDir    = $DshHomeDir
        PromptName  = 'AGENTS.md'
        ProcNames   = @('DeepSeek Harness', 'DeepSeekHarness', 'deepseek-harness', 'dsh')
        ProbeFiles  = @(
            (Join-Path $HomeDir '.dsh\settings.yaml'),
            (Join-Path $HomeDir '.dsh\cordis.patch.yml')
        )
        ExeHints    = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\DeepSeek Harness\DeepSeek Harness.exe'),
            (Join-Path $env:ProgramFiles 'DeepSeek Harness\DeepSeek Harness.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\dsh\dsh.exe')
        )
    }
}

$T = $TARGETS[$Target]
if (-not [string]::IsNullOrWhiteSpace($AgentDir)) {
    $T.AgentDir = [System.IO.Path]::GetFullPath($AgentDir)
} else {
    $T.AgentDir = [System.IO.Path]::GetFullPath($T.AgentDir)
}

$Base       = $PSScriptRoot
$WorkRoot   = Join-Path $env:LOCALAPPDATA $TOOL_TAG
$StateDir   = Join-Path $WorkRoot 'state'
$BackupRoot = Join-Path $WorkRoot 'backup'
$LogDir     = Join-Path $WorkRoot 'logs'
$StatePath  = Join-Path $StateDir ($Target + '.json')
$ResultPath = Join-Path $WorkRoot ('last-run-' + $Target + '.json')
$LogPath    = Join-Path $LogDir ($Target + '.log')

foreach ($d in @($WorkRoot, $StateDir, $BackupRoot, $LogDir)) {
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

$Script:Report = New-Object System.Collections.ArrayList
$Script:PatchWarn = $null

function Say([string]$Level, [string]$Message) {
    $line = '[' + $Level + '] ' + $Message
    Write-Output $line
    [void]$Script:Report.Add($line)
}

function Fail([string]$Message) { Say 'ERROR' $Message; Finish 'FAIL'; exit 1 }

function Finish([string]$Status) {
    $payload = [ordered]@{
        status    = $Status
        target    = $Target
        toolVersion = $TOOL_VER
        agentDir  = $T.AgentDir
        lines     = @($Script:Report)
        finishedAt = (Get-Date).ToString('o')
    }
    try {
        [System.IO.File]::WriteAllText($ResultPath, ($payload | ConvertTo-Json -Depth 4), $Utf8NoBom)
    } catch { }
    try {
        Add-Content -LiteralPath $LogPath -Value ((Get-Date).ToString('s') + ' ' + $Status + "`n" + ($Script:Report -join "`n")) -Encoding UTF8
    } catch { }
    Write-Output ('RESULT: ' + $Status)
}

function Read-Utf8([string]$Path) {
    return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    [System.IO.File]::WriteAllText($Path, $Text, $Utf8NoBom)
}

function Copy-Tree([string]$Src, [string]$Dest) {
    robocopy $Src $Dest /E /MT:16 /R:1 /W:1 /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw ("robocopy failed rc=" + $LASTEXITCODE + " for " + $Src) }
}

function Strip-MarkerBlock([string]$Text) {
    $pattern = '(?s)' + [regex]::Escape($MARK_BEG) + '.*?' + [regex]::Escape($MARK_END) + '\s*'
    return [regex]::Replace($Text, $pattern, '')
}

function Get-MarkerBlock([string]$PromptPath) {
    if (-not (Test-Path -LiteralPath $PromptPath)) { return $null }
    $txt = Read-Utf8 $PromptPath
    $m = [regex]::Match($txt, '(?s)' + [regex]::Escape($MARK_BEG) + '.*?' + [regex]::Escape($MARK_END))
    if ($m.Success) { return $m.Value }
    return $null
}

function Strip-PatchBlock([string]$Text) {
    $pattern = '(?ms)^[ \t]*' + [regex]::Escape($PATCH_BEG) + '.*?^[ \t]*' + [regex]::Escape($PATCH_END) + '[ \t]*\r?\n?'
    return [regex]::Replace($Text, $pattern, '')
}

function Get-PatchBody([int]$Budget) {
    # 注：数组字面量里逗号比 + 结合更紧，拼接项必须加括号，
    # 否则 'x: ' + $Budget 会被当成两个独立元素。
    return @(
        $PATCH_BEG,
        '# 由 pi用学习工作台 维护；摘除本段即回到 dsh 默认值。',
        '- id: agent-instructions',
        '  config:',
        ('    maxBytes: ' + $Budget),
        $PATCH_END
    ) -join "`r`n"
}

function Get-DshBudget {
    <#
      返回 (预算字节数, 来源, 是否用户显式设置)。解析顺序：
        1. 用户自己层里显式的 maxBytes（摘除本工具块后仍能找到）→ 尊重用户意图，不自动改。
        2. 本工具上次写在我们标记块里的 maxBytes → 保持粘性，重注入不重置用户手改的值。
        3. dsh-base 的默认值 65536。
      注意：这里不能把整个 patch 文本摘块后去找，否则第 2 步永远读不到，
      用户把 maxBytes 调大后一次重注入就被打回默认值。
    #>
    $sources = @($PatchFile, (Join-Path $T.AgentDir 'profiles\desktop\cordis.patch.yml'))
    foreach ($p in $sources) {
        if (-not (Test-Path -LiteralPath $p)) { continue }
        $txt = Strip-PatchBlock (Read-Utf8 $p)
        $m = [regex]::Match($txt, '(?m)^\s*maxBytes\s*:\s*(\d+)\s*$')
        if ($m.Success) {
            $v = 0
            if ([int]::TryParse($m.Groups[1].Value, [ref]$v) -and $v -gt 0) {
                return @($v, ([System.IO.Path]::GetFileName($p)), $true)
            }
        }
    }
    if (Test-Path -LiteralPath $PatchFile) {
        $m2 = [regex]::Match((Read-Utf8 $PatchFile), '(?m)^\s*maxBytes\s*:\s*(\d+)\s*$')
        if ($m2.Success) {
            $v2 = 0
            if ([int]::TryParse($m2.Groups[1].Value, [ref]$v2) -and $v2 -gt 0) {
                return @($v2, '本工具上次写入', $false)
            }
        }
    }
    return @($DshDefaultBudget, 'dsh 默认值', $false)
}

function Get-SkillDirs([string]$Root) {
    if (-not (Test-Path -LiteralPath $Root)) { return @() }
    # 传单个技能包目录（自身含 SKILL.md）时直接返回它
    if (Test-Path -LiteralPath (Join-Path $Root 'SKILL.md')) {
        return @(Get-Item -LiteralPath $Root)
    }
    return @(Get-ChildItem -LiteralPath $Root -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName 'SKILL.md') })
}

# ---------------------------------------------------------------- 极简模式（菜单路由）
# 机制：给每个模块技能的 frontmatter 加一行 disable-model-invocation: true，
# Pi 的 formatSkillsForSystemPrompt 会把它们整个滤除（不进系统提示词、不占每轮开销），
# 只留一个菜单技能进提示词；agent 按菜单里的模块 id 直接 read 对应 SKILL.md。
# 同一个技能仍可用 /skill:<名字> 手动强制加载，作为兜底。

function Read-TextKeepBom([string]$Path) {
    # 读文本并告诉调用方原文件是否有 BOM（写回时保持一致）
    $raw = [System.IO.File]::ReadAllBytes($Path)
    $hasBom = ($raw.Length -ge 3 -and $raw[0] -eq 0xEF -and $raw[1] -eq 0xBB -and $raw[2] -eq 0xBF)
    $text = [System.Text.Encoding]::UTF8.GetString($raw)
    if ($hasBom -and $text.Length -gt 0 -and [int]$text[0] -eq 0xFEFF) { $text = $text.Substring(1) }
    return @($text, $hasBom)
}

function Get-FrontField([string]$Text, [string]$Key) {
    # 读 SKILL.md frontmatter 字段值。支持四种写法：行内文本 / 双引号 / 单引号 /
    # YAML 块标量（| 或 >）—— 本技能库有 8 个模块用块标量写 description，
    # 裸正则会把它读成「| 开头的正文」。
    $m = [regex]::Match($Text, '(?s)^---\r?\n(.*?)\r?\n---')
    if (-not $m.Success) { return '' }
    $fm = $m.Groups[1].Value
    $mm = [regex]::Match($fm, '(?m)^' + [regex]::Escape($Key) + ':[ \t]*(.*)$')
    if (-not $mm.Success) { return '' }
    $first = $mm.Groups[1].Value.Trim()
    if ($first -eq '|' -or $first -eq '>' -or $first -eq '|-' -or $first -eq '>-' -or $first -eq '|+' -or $first -eq '>+') {
        $rest = $fm.Substring($mm.Index + $mm.Length)
        $parts = @()
        foreach ($ln in ($rest -split "\r?\n")) {
            if ([string]::IsNullOrWhiteSpace($ln)) { continue }
            if ($ln -match '^[ \t]+') { $parts += $ln.Trim() } else { break }
        }
        return ($parts -join ' ')
    }
    if ($first.Length -ge 2) {
        $a = $first[0]; $b = $first[$first.Length - 1]
        if (($a -eq '"' -or $a -eq "'") -and $a -eq $b) { return $first.Substring(1, $first.Length - 2) }
    }
    return $first
}

function Add-DisableModelInvocation([string]$Path) {
    # 向 frontmatter 末尾插入 disable-model-invocation: true。
    # 保原行尾（CRLF/LF）与 BOM —— 否则一注入就整文件重写，库文件全变成 LF。
    # 返回 $true 表示已就位（本次插入或未已存在）。
    $p = Read-TextKeepBom $Path
    $text = $p[0]; $hasBom = $p[1]
    $m = [regex]::Match($text, '(?s)^(---\r?\n)(.*?)(\r?\n---)')
    if (-not $m.Success) { return $false }
    if ($m.Groups[2].Value -match '(?m)^disable-model-invocation[ \t]*:') { return $true }
    $nl = if ($m.Groups[1].Value.Contains("`r`n")) { "`r`n" } else { "`n" }
    $new = $m.Groups[1].Value + $m.Groups[2].Value + $nl + 'disable-model-invocation: true' + $m.Groups[3].Value + $text.Substring($m.Index + $m.Length)
    $enc = if ($hasBom) { New-Object System.Text.UTF8Encoding($true) } else { $Utf8NoBom }
    [System.IO.File]::WriteAllText($Path, $new, $enc)
    return $true
}

function Test-DisableModelInvocation([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $t = (Read-TextKeepBom $Path)[0]
    return [bool]([regex]::IsMatch($t, '(?m)^disable-model-invocation[ \t]*:[ \t]*true'))
}

function Remove-DisableModelInvocation([string]$Path) {
    # 去掉之前注入的那一行（幂等：反复调用安全）。保行尾与 BOM。
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $p = Read-TextKeepBom $Path
    $text = $p[0]; $hasBom = $p[1]
    if (-not [regex]::IsMatch($text, '(?m)^disable-model-invocation[ \t]*:')) { return $false }
    $new = [regex]::Replace($text, '(?m)^disable-model-invocation[ \t]*:[^\r\n]*\r?\n', '')
    $enc = if ($hasBom) { New-Object System.Text.UTF8Encoding($true) } else { $Utf8NoBom }
    [System.IO.File]::WriteAllText($Path, $new, $enc)
    return $true
}

function Shorten-Desc([string]$Desc) {
    # 描述压成一行：去掉「触发词：」尾缀，取首句，过长在词边界截断
    $d = ($Desc -replace '\s+', ' ').Trim()
    $d = ($d -split '\s*(?:触发词|触发器)[:：]')[0].Trim()
    $parts = [regex]::Split($d, '(?<=[。；;])')
    if ($parts.Count -gt 0 -and $parts[0].Length -le 72) { $d = $parts[0] }
    if ($d.Length -gt 72) {
        $cut = $d.Substring(0, 72)
        foreach ($sep in @(' ', '，', '、')) {
            $i = $cut.LastIndexOf($sep)
            if ($i -gt 36) { $cut = $cut.Substring(0, $i); break }
        }
        $d = $cut.TrimEnd() + '…'
    }
    return $d
}

function New-SkillMenu {
    # 生成菜单技能：只列「有哪些模块 + 何时用」+ 取用纪律，正文按需读。
    # 以类目表（skill-categories.json）分类；未登记的模块归入「其他」。
    param([string]$Target, [string[]]$ModuleIds, [string]$CatFile, [string]$MenuName)
    $cats = @()
    if (Test-Path -LiteralPath $CatFile) {
        try { $cats = @((Read-Utf8 $CatFile | ConvertFrom-Json).categories) } catch { $cats = @() }
    }
    $one = @{}
    $declared = @{}          # 模块 id -> frontmatter 里的真名（用于 /skill: 强制加载）
    foreach ($id in $ModuleIds) {
        $p = Join-Path (Join-Path $Target $id) 'SKILL.md'
        if (-not (Test-Path -LiteralPath $p)) { continue }
        $fm = (Read-TextKeepBom $p)[0]
        $one[$id] = Shorten-Desc (Get-FrontField $fm 'description')
        $dn = Get-FrontField $fm 'name'
        if ($dn -and $dn -ne $id) { $declared[$id] = $dn }
    }
    $placed = @{}
    $lines = New-Object System.Collections.ArrayList
    foreach ($c in $cats) {
        $hit = @()
        foreach ($m in $c.modules) { if ($one.ContainsKey([string]$m)) { $hit += [string]$m } }
        if ($hit.Count -eq 0) { continue }
        foreach ($h in $hit) { $placed[$h] = $true }
        [void]$lines.Add('')
        [void]$lines.Add('### ' + [string]$c.name)
        if ($c.when) { [void]$lines.Add('> 何时进这类：' + [string]$c.when) }
        [void]$lines.Add('')
        [void]$lines.Add('| 模块 | 何时用 |')
        [void]$lines.Add('|---|---|')
        foreach ($h in $hit) {
            $cell = '`' + $h + '`'
            if ($declared.ContainsKey($h)) { $cell += '（/skill:' + $declared[$h] + '）' }
            [void]$lines.Add('| ' + $cell + ' | ' + $one[$h] + ' |')
        }
    }
    $rest = @($ModuleIds | Where-Object { -not $placed.ContainsKey($_) })
    if ($rest.Count -gt 0) {
        [void]$lines.Add('')
        [void]$lines.Add('### 其他')
        [void]$lines.Add('> 何时进这类：不在上述类目里，但名字对得上任务')
        [void]$lines.Add('')
        [void]$lines.Add('| 模块 | 何时用 |')
        [void]$lines.Add('|---|---|')
        foreach ($h in $rest) {
            $cell = '`' + $h + '`'
            if ($declared.ContainsKey($h)) { $cell += '（/skill:' + $declared[$h] + '）' }
            [void]$lines.Add('| ' + $cell + ' | ' + $one[$h] + ' |')
        }
    }
    $domains = @()
    foreach ($c in $cats) { if ($c.name) { $domains += [string]$c.name } }
    $domText = if ($domains.Count -gt 0) { ($domains -join '、') } else { '专业技能' }
    # 描述里的触发词必须是**领域词**（逆向/渗透/游戏…），不能是「需要技能」这种元意图词：
    # 模型要先认出「这属于某领域」才会来读菜单。领域列表从类目表现取，增删技能自动同步。
    $desc = '技能菜单与路由（' + $one.Count + ' 个专业模块的入口）。用于：' + $domText
    $desc += ' 等任务。接到这类任务时先读本文件，按类目定位到模块 id，再读该模块的 SKILL.md 全文后执行。'
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append("---`r`n")
    [void]$sb.Append('name: ' + $MenuName + "`r`n")
    [void]$sb.Append('description: ' + $desc + "`r`n")
    [void]$sb.Append("---`r`n`r`n")
    [void]$sb.Append('# 技能菜单 · ' + $one.Count + " 个模块`r`n`r`n")
    [void]$sb.Append("本目录下每个模块都是一个技能目录：``<本技能根>/<模块 id>/SKILL.md``。`r`n")
    [void]$sb.Append("括号里是 frontmatter 里的真名（与目录名不同时才有）；用 ``/skill:<真名>`` 可强制加载。`r`n")
    [void]$sb.Append("本文件只给「有哪些模块 + 何时用」，正文按需读。`r`n`r`n")
    [void]$sb.Append("## 取用纪律（硬性）`r`n`r`n")
    [void]$sb.Append("1. **先选类目再选模块**：按任务选 1 个类目，类目内按「何时用」取 **1 个**最匹配的模块，读完 ``SKILL.md`` 再动手。`r`n")
    [void]$sb.Append("2. **上限**：一个阶段最多加载 4 个模块正文；确需跳类目时才取第二个类目。`r`n")
    [void]$sb.Append("3. **报名（硬性）**：选定 / 换用 / 补充任何模块的当下，先向用户说一行 ``参考模块: <模块id>（<用途>）``。`r`n")
    [void]$sb.Append("   禁止只执行不报名，禁止事后补报。`r`n")
    [void]$sb.Append("4. **取不到就直说**：读不到模块正文时如实报告，**不得声称已按该模块执行**。`r`n")
    [void]$sb.Append("5. **已读复用**：同一任务已读过的模块直接复用，不重复读。`r`n")
    [void]$sb.Append("6. **三不要**：不要为了解全部能力而读完所有模块；不要只为比较而读无关类目；`r`n")
    [void]$sb.Append("   找不到匹配模块就用自己的知识继续，不要凑数。`r`n`r`n")
    [void]$sb.Append("## 模块清单`r`n")
    [void]$sb.Append(($lines -join "`r`n"))
    [void]$sb.Append("`r`n")
    $menuDir = Join-Path $Target $MenuName
    New-Item -ItemType Directory -Force -Path $menuDir | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $menuDir 'SKILL.md'), $sb.ToString(), $Utf8NoBom)
    return $one.Count
}

function Get-ClientProcess {
    $found = @()
    foreach ($n in $T.ProcNames) {
        $p = @(Get-Process -Name $n -ErrorAction SilentlyContinue)
        if ($p.Count -gt 0) { $found += $p }
    }
    return $found
}

function Find-ClientExe {
    foreach ($p in $T.ExeHints) {
        if ($p -and (Test-Path -LiteralPath $p)) { return $p }
    }
    return $null
}

# ---------------------------------------------------------------- 参数默认值

if ([string]::IsNullOrWhiteSpace($SourcePrompt)) {
    # 命令行默认：优先用 GUI 拼好的 V5（推荐版），还没生成则回落到随包的头部+正文。
    # GUI 路径总是显式传 -SourcePrompt，所以这里只影响纯命令行使用。
    $v5 = Join-Path $env:LOCALAPPDATA "$TOOL_TAG\prompts\v5-1b.md"
    if (-not (Test-Path -LiteralPath $v5 -PathType Leaf)) {
        $v5 = Join-Path $env:LOCALAPPDATA "$TOOL_TAG\prompts\v5-docs.md"
    }
    if (-not (Test-Path -LiteralPath $v5 -PathType Leaf)) {
        # 成品还没生成（例如首次使用 CLI、未跑过 GUI）——用随包的 V5.1b 头部+正文现场拼
        $h = Join-Path $Base 'prompts\_v51b-header.md'
        $b = Join-Path $Base 'prompts\v5-body.md'
        if ((Test-Path -LiteralPath $h -PathType Leaf) -and (Test-Path -LiteralPath $b -PathType Leaf)) {
            $v5 = Join-Path $env:LOCALAPPDATA "$TOOL_TAG\prompts\v5-1b.md"
            $dir = Split-Path -Parent $v5
            if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
            $hdr = [System.IO.File]::ReadAllText($h, [System.Text.Encoding]::UTF8).TrimEnd()
            $body = [System.IO.File]::ReadAllText($b, [System.Text.Encoding]::UTF8)
            $enc = New-Object System.Text.UTF8Encoding($false)
            [System.IO.File]::WriteAllText($v5, ($hdr + "`r`n`r`n" + $body), $enc)
            Say 'INFO' '已现场生成 V5.1b 指令集（CLI 首次运行）'
        }
    }
    if (Test-Path -LiteralPath $v5 -PathType Leaf) {
        $SourcePrompt = $v5
    } else {
        $SourcePrompt = Join-Path $Base 'prompts\_v51b-header.md'
    }
}
if (-not (Test-Path -LiteralPath $SourcePrompt -PathType Leaf)) {
    Fail ('找不到指令集文件: ' + $SourcePrompt)
}

# 版本元数据：由传入的指令集文件名识别，供 GUI 回显与「重新注入」复用
$PromptLeaf = [System.IO.Path]::GetFileNameWithoutExtension($SourcePrompt)
$VersionMap = @{
    'v5-1b'               = @('v51b',      'V5.1b')
    'v5-1b-header'        = @('v51b',      'V5.1b')
    'v5-docs'             = @('v5docs',    'V5 文档引擎')
    'v5-2c'               = @('v52c',      'V5.2c')
    'v5-2c-ext'           = @('v52cx',     'V5.2c 增强版')
    '_gpt6-astra-header'  = @('gptastra',  'GPT-6 Astra')
    '_gpt56sol-header'    = @('gpt56sol',  'GPT-5.6 Sol')
    '_glm-neutral-header' = @('glmneutral','GLM 中性化')
    '_glm53f-kovak'       = @('glm53f',    'GLM5.3f Kovak')
    # 旧成品文件名（历史状态清单兼容，不再生成）
}
if ($VersionMap.ContainsKey($PromptLeaf)) {
    $VersionKey   = $VersionMap[$PromptLeaf][0]
    $VersionLabel = $VersionMap[$PromptLeaf][1]
} else {
    $VersionKey   = $PromptLeaf
    $VersionLabel = $PromptLeaf
}
if ([string]::IsNullOrWhiteSpace($SkillsSource)) {
    $SkillsSource = Join-Path $Base 'skills-v4'
} else {
    # 分号分隔多源：**先切分再逐段补基准目录**。
    # 旧写法对整串做 Join-Path，只有第 1 段拿到 $Base，第 2+ 段会退化成
    # 裸相对名、按进程 CWD 解析 → GUI 传 'skills-v4;code-quality-gate' 时
    # 附加包会被静默跳过（只留一条 WARN，退出码仍是 0）。
    $srcParts = @()
    foreach ($p in ($SkillsSource -split '[;]')) {
        $p = $p.Trim()
        if ([string]::IsNullOrWhiteSpace($p)) { continue }
        if (-not [System.IO.Path]::IsPathRooted($p)) { $p = Join-Path $Base $p }
        $srcParts += $p
    }
    if ($srcParts.Count -eq 0) { $srcParts = @(Join-Path $Base 'skills-v4') }
    $SkillsSource = ($srcParts -join ';')
}

$PromptTarget = Join-Path $T.AgentDir $T.PromptName
$SkillsTarget = Join-Path $T.AgentDir 'skills'
# DSH 的 home 级 patch 层（提高 agent-instructions 的 maxBytes 预算）
$PatchFile = Join-Path $T.AgentDir 'cordis.patch.yml'

# agent-instructions 的 maxBytes 默认预算（来自 dsh-base 的 cordis.patch.yml）
$DshDefaultBudget = 65536

# ---------------------------------------------------------------- 自检模式

if ($Check) {
    Say 'INFO' ('目标端: ' + $T.Label + ' | 配置根: ' + $T.AgentDir)

    # L1 需要两侧同时就位：指令集里的标记块 + 技能库。
    # 旧写法两侧各自置 $l1ok = $true，于是一侧缺失（例如指令集被清掉、
    # 只剩技能目录）也会报「自检通过」。
    $promptOk = $false
    $block = Get-MarkerBlock $PromptTarget
    if ($block) {
        Say 'L1' ($T.PromptName + ' 已注入，标记块 ' + $block.Length + ' 字符')
        $promptOk = $true
    } elseif (Test-Path -LiteralPath $PromptTarget) {
        Say 'L1' ($T.PromptName + ' 存在，但未发现注入标记块')
    } else {
        Say 'L1' ($T.PromptName + ' 不存在')
    }

    # DSH 专用：确认 AGENTS.md 能被 agent-instructions 读到（预算足够）
    if ($Target -eq 'dsh' -and $block) {
        $bud = Get-DshBudget
        $need = [System.Text.Encoding]::UTF8.GetByteCount($block)
        $frame = 700  # <system-reminder> 框架 + 标题行的字节余量
        Say 'L1' ('agent-instructions 预算 maxBytes=' + $bud[0] + '（来源 ' + $bud[1] + '），标记块 ' + $need + ' 字节')
        if ($bud[0] -lt ($need + $frame)) {
            Say 'WARN' ('预算偏紧：AGENTS.md 可能在渲染时被截断，建议 maxBytes 至少 ' + ($need + $frame))
        } else {
            Say 'L1' '预算足够，AGENTS.md 可完整注入'
        }
        if (Test-Path -LiteralPath $PatchFile) {
            $pm = [regex]::Match((Read-Utf8 $PatchFile), '(?m)^\s*maxBytes\s*:\s*(\d+)\s*$')
            if ($pm.Success) {
                $srcNote = if ($bud[2]) { '；用户显式设置，本工具不自动调整' } else { '；本工具层，重注入保持该值' }
                Say 'L2' ('home 级 patch 已就位（agent-instructions maxBytes=' + $pm.Groups[1].Value + $srcNote + '）')
            } else {
                Say 'L2' ('home 级 patch 存在但未发现 maxBytes 行：' + (Split-Path -Leaf $PatchFile))
            }
        } else {
            Say 'L2' ('未发现 home 级 patch（' + (Split-Path -Leaf $PatchFile) + '），使用 dsh 默认预算 ' + $DshDefaultBudget)
        }
    }

    $state = $null
    if (Test-Path -LiteralPath $StatePath) {
        try { $state = Read-Utf8 $StatePath | ConvertFrom-Json } catch { $state = $null }
    }
    $skillsOk = $false
    if ($state -and $state.installedSkills) {
        $present = 0
        foreach ($n in $state.installedSkills) {
            if (Test-Path -LiteralPath (Join-Path $SkillsTarget ($n + '\SKILL.md'))) { $present++ }
        }
        Say 'L1' ('技能库 ' + $present + '/' + @($state.installedSkills).Count + ' 就位')
        $skillsOk = ($present -eq @($state.installedSkills).Count)
    } else {
        $cnt = (Get-SkillDirs $SkillsTarget).Count
        if ($cnt -gt 0) {
            Say 'L1' ('无安装记录；技能目录现有 ' + $cnt + ' 个技能（未与清单核对）')
            $skillsOk = $true
        } else {
            Say 'L1' '无安装记录，技能目录为空'
        }
    }
    $l1ok = ($promptOk -and $skillsOk)

    # 极简模式额外校验（仅当状态清单记录了 skillMode=menu）
    if ($state -and $state.skillMode -eq 'menu') {
        $mName = if ($state.menuSkill) { [string]$state.menuSkill } else { 'pi-workbench-menu' }
        $mPath = Join-Path $SkillsTarget ($mName + '\SKILL.md')
        if (Test-Path -LiteralPath $mPath) {
            Say 'L1' ('极简模式：菜单技能就位 ' + $mName)
        } else {
            Say 'WARN' ('极简模式：菜单技能缺失 ' + $mName + '（AI 将看不到任何技能）')
            $l1ok = $false
        }
        $unflagged = @()
        $keptAdvertised = @()
        # 例外清单优先从状态清单读（那是上次部署的真实口径），参数只是兼底
        $keepSrc = $MenuKeepAdvertised
        if ($state -and $state.menuKeepAdvertised) { $keepSrc = [string]$state.menuKeepAdvertised }
        foreach ($k in ($keepSrc -split '[;]')) {
            $k = $k.Trim()
            if ($k) { $keptAdvertised += $k }
        }
        $checkedMods = 0
        foreach ($n in @($state.installedSkills)) {
            if ($n -eq $mName) { continue }
            $p = Join-Path $SkillsTarget ($n + '\SKILL.md')
            if (-not (Test-Path -LiteralPath $p)) { continue }
            $checkedMods++
            if ($keptAdvertised -contains $n) { continue }   # 例外项本来就不该有标记
            if (-not (Test-DisableModelInvocation $p)) { $unflagged += $n }
        }
        if ($unflagged.Count -eq 0) {
            $hiddenN = $checkedMods - $keptAdvertised.Count
            $keepNote = if ($keptAdvertised.Count -gt 0) { '（其中 ' + $keptAdvertised.Count + ' 个纪律型技能保持进提示词）' } else { '' }
            Say 'L1' ('极简模式：' + $hiddenN + ' 个模块不进系统提示词' + $keepNote)
        } else {
            Say 'WARN' ('极简模式：' + $unflagged.Count + ' 个模块未标记，仍会进系统提示词: ' + (($unflagged | Select-Object -First 5) -join ', '))
            $l1ok = $false
        }
    }

    if (-not $l1ok) {
        Say 'L1' ('L1 未通过：' + $T.PromptName + ' 标记块与技能库需同时就位')
    }

    $l2ok = $false
    if ($Target -eq 'dsh') {
        # Harness 用 settings.yaml（不是 settings.json）
        $settingsPath = Join-Path $T.AgentDir 'settings.yaml'
        if (Test-Path -LiteralPath $settingsPath) {
            Say 'L2' ('settings.yaml 存在（' + (Get-Item -LiteralPath $settingsPath).Length + ' 字节，YAML 不做解析校验）')
        } else {
            Say 'L2' 'settings.yaml 不存在（客户端未初始化过，属正常）'
        }
        $l2ok = $true
    } else {
        $settingsPath = Join-Path $T.AgentDir 'settings.json'
        if (Test-Path -LiteralPath $settingsPath) {
            try {
                $null = (Read-Utf8 $settingsPath | ConvertFrom-Json)
                Say 'L2' 'settings.json 解析通过'
                $l2ok = $true
            } catch {
                Say 'L2' ('settings.json 解析失败: ' + $_.Exception.Message)
            }
        } else {
            Say 'L2' 'settings.json 不存在（客户端未初始化过，属正常）'
            $l2ok = $true
        }
    }
    if (Test-Path -LiteralPath $T.AgentDir) { Say 'L2' ('配置根存在: ' + $T.AgentDir) } else { Say 'L2' ('配置根不存在: ' + $T.AgentDir) }
    foreach ($pf in $T.ProbeFiles) {
        if ($pf -and (Test-Path -LiteralPath $pf)) { Say 'L2' ('探测到 Harness 文件: ' + $pf) }
    }

    $l3ok = $false
    $proc = Get-ClientProcess
    if ($proc.Count -gt 0) {
        Say 'L3' ('客户端进程运行中: ' + (($proc | Select-Object -ExpandProperty ProcessName -Unique) -join ', '))
        $l3ok = $true
    } else {
        $exe = Find-ClientExe
        if ($exe) { Say 'L3' ('客户端已安装（未运行）: ' + $exe); $l3ok = $true }
        else { Say 'L3' ('未检测到 ' + $T.Label + ' 的进程或安装路径，可在客户端内自行确认') }
    }

    Say 'L4' '会话层需人工验证：在客户端新开会话，直接给一个技术任务，看是否第一行就给交付物'
    $ok = ($l1ok -and $l2ok -and $l3ok)
    if ($ok) { Say 'INFO' '文件层 / 配置层 / 进程层 自检通过' } else { Say 'WARN' '存在未通过项，详见上方 L1-L3' }
    if ($ok) { Finish 'OK'; exit 0 }
    # PARTIAL 用退出码 1 表达「有未通过项」——旧写法无条件 exit 0，
    # 导致 GUI 把「自检未通过」也显示成「自检通过（L1/L2/L3）」。
    Finish 'PARTIAL'
    exit 1
}

# ---------------------------------------------------------------- 附加包移除（RemoveAddons）

# 只移除指定的附加技能包（不动指令集、不动状态清单里的其它技能）。
# 用法：inject.ps1 -Target pideck -RemoveAddons 'code-quality-gate;task-boundary'
if ($RemoveAddons) {
    $removed = @()
    $notFound = @()
    $removeNames = @()
    foreach ($name in ($RemoveAddons -split '[;]')) {
        if ([string]::IsNullOrWhiteSpace($name)) { continue }
        $name = $name.Trim()
        $removeNames += $name
        $dest = Join-Path $SkillsTarget $name
        if (Test-Path -LiteralPath $dest) {
            Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue
            $removed += $name
        } else {
            $notFound += $name
        }
    }
    foreach ($n in $removed)    { Say 'INFO' ('已移除附加包: ' + $n) }
    foreach ($n in $notFound)   { Say 'WARN'  ('附加包不存在（跳过）: ' + $n) }
    if (Test-Path -LiteralPath $StatePath -ErrorAction SilentlyContinue) {
        # 从状态清单的 installedSkills 里同步移除，保持卸载口径一致
        try {
            $st = Read-Utf8 $StatePath | ConvertFrom-Json
            if ($st.installedSkills) {
                # 精确成员判断：旧写法用 -notmatch 对整个 $RemoveAddons 串做正则匹配，
                # 清单里名字恰好是附加包名子串的技能（如 gate）会被一并剔除。
                $keep = @($st.installedSkills | Where-Object { $removeNames -notcontains $_ })
                $st.installedSkills = $keep
                Write-Utf8NoBom $StatePath (($st | ConvertTo-Json -Depth 5) + "`r`n")
                Say 'INFO' '状态清单已同步更新'
            }
        } catch { Say 'WARN' '状态清单更新失败（不影响移除结果）' }
    }
    if (Test-Path -LiteralPath $SkillsTarget) {
        $left = @(Get-ChildItem -LiteralPath $SkillsTarget -Force -ErrorAction SilentlyContinue)
        if ($left.Count -eq 0) {
            # 与 NoSkills / 卸载路径口径一致：原本就存在的空目录要留着
            $hadSkillsDir = $false
            if (Test-Path -LiteralPath $StatePath -ErrorAction SilentlyContinue) {
                try { $hadSkillsDir = [bool]((Read-Utf8 $StatePath | ConvertFrom-Json).hadSkillsDir) } catch { }
            }
            if ($hadSkillsDir) {
                Say 'INFO' 'skills 目录原本就存在，保留空目录'
            } else {
                Remove-Item -LiteralPath $SkillsTarget -Force -ErrorAction SilentlyContinue
                Say 'INFO' 'skills 目录由本工具创建且已空，一并移除'
            }
        }
    }
    Say 'INFO' ('附加包移除完成：' + $removed.Count + ' 个，请重启客户端使改动生效')
    Finish 'OK'
    exit 0
}

# ---------------------------------------------------------------- 卸载

if ($Uninstall) {
    $state = $null
    if (Test-Path -LiteralPath $StatePath) {
        try { $state = Read-Utf8 $StatePath | ConvertFrom-Json } catch { $state = $null }
    }

    # 1) 恢复注入的指令文件
    if (Test-Path -LiteralPath $PromptTarget) {
        $txt = Read-Utf8 $PromptTarget
        $stripped = Strip-MarkerBlock $txt
        $stripped = $stripped -replace '(\r?\n){3,}', "`r`n`r`n"

        $restored = $false
        if ($state -and $state.promptBackup -and (Test-Path -LiteralPath $state.promptBackup)) {
            $orig = Read-Utf8 $state.promptBackup
            Write-Utf8NoBom $PromptTarget $orig
            Say 'INFO' ($T.PromptName + ' 已还原为安装前内容')
            $restored = $true
        }
        if (-not $restored) {
            if ($state -and $state.hadPromptFile -eq $false) {
                if ([string]::IsNullOrWhiteSpace($stripped)) {
                    Remove-Item -LiteralPath $PromptTarget -Force -ErrorAction SilentlyContinue
                    Say 'INFO' ($T.PromptName + ' 由本工具创建，已删除')
                } else {
                    Write-Utf8NoBom $PromptTarget $stripped.TrimEnd()
                    Say 'INFO' '已摘除注入标记块'
                }
            } else {
                if ([string]::IsNullOrWhiteSpace($stripped)) {
                    Remove-Item -LiteralPath $PromptTarget -Force -ErrorAction SilentlyContinue
                    Say 'WARN' '原文件无有效内容，已删除'
                } else {
                    Write-Utf8NoBom $PromptTarget $stripped.TrimEnd()
                    Say 'INFO' '已摘除注入标记块'
                }
            }
        }
    } else {
        Say 'INFO' ($T.PromptName + ' 不存在，跳过')
    }

    # 1b) DSH：摘除 home 级 patch 里本工具写的预算层
    if ($Target -eq 'dsh' -and (Test-Path -LiteralPath $PatchFile)) {
        $ptxt = Read-Utf8 $PatchFile
        $pstrip = Strip-PatchBlock $ptxt
        $pstrip = $pstrip -replace '(\r?\n){3,}', "`r`n`r`n"
        if ($state -and $state.patchBackup -and (Test-Path -LiteralPath $state.patchBackup)) {
            Write-Utf8NoBom $PatchFile (Read-Utf8 $state.patchBackup)
            Say 'INFO' 'home 级 patch 已还原为安装前内容'
        } elseif ([string]::IsNullOrWhiteSpace($pstrip)) {
            Remove-Item -LiteralPath $PatchFile -Force -ErrorAction SilentlyContinue
            Say 'INFO' 'home 级 patch 由本工具创建，已删除'
        } else {
            Write-Utf8NoBom $PatchFile $pstrip.TrimEnd()
            Say 'INFO' '已从 home 级 patch 摘除本工具层'
        }
    }

    # 2) 移除清单内的技能，并还原被覆盖的同名技能
    $removed = 0
    if ($state -and $state.installedSkills) {
        foreach ($n in $state.installedSkills) {
            $dest = Join-Path $SkillsTarget $n
            $overwrittenBackup = $null
            if ($state.overwrittenSkills -and $state.overwrittenSkills.$n) {
                $overwrittenBackup = [string]$state.overwrittenSkills.$n
            }
            if ($overwrittenBackup -and (Test-Path -LiteralPath $overwrittenBackup)) {
                if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue }
                $parent = Split-Path -Parent $dest
                if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
                Copy-Tree $overwrittenBackup $dest
                Say 'INFO' ('已还原被覆盖的原有技能: ' + $n)
            } else {
                if (Test-Path -LiteralPath $dest) {
                    Remove-Item -LiteralPath $dest -Recurse -Force -ErrorAction SilentlyContinue
                    $removed++
                }
            }
        }
        Say 'INFO' ('已移除注入技能 ' + $removed + ' 个；' + $T.Label + ' 自带技能未被触碰')
    } elseif ($state) {
        Say 'INFO' '状态清单存在，本次安装未部署技能库，无需移除技能'
    } else {
        Say 'WARN' ('未找到状态清单，仅完成 ' + $T.PromptName + ' 摘除')
    }

    if (Test-Path -LiteralPath $StatePath) { Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue }

    # 3) 若 skills 目录被清空且原本不存在，一并移除，避免留下空壳
    if (Test-Path -LiteralPath $SkillsTarget) {
        $left = @(Get-ChildItem -LiteralPath $SkillsTarget -Force -ErrorAction SilentlyContinue)
        if ($left.Count -eq 0) {
            if (-not ($state -and $state.hadSkillsDir -eq $true)) {
                Remove-Item -LiteralPath $SkillsTarget -Force -ErrorAction SilentlyContinue
                Say 'INFO' '空的 skills 目录已清除'
            }
        }
    }

    Say 'INFO' ('卸载完成：' + $T.Label + '，请重启客户端使改动生效')
    Finish 'OK'
    exit 0
}

# ---------------------------------------------------------------- 安装

Say 'INFO' ('目标端: ' + $T.Label + ' | 配置根: ' + $T.AgentDir)

if (-not (Test-Path -LiteralPath $T.AgentDir)) {
    New-Item -ItemType Directory -Force -Path $T.AgentDir | Out-Null
    Say 'INFO' ('配置根不存在，已创建: ' + $T.AgentDir)
}

$stamp      = Get-Date -Format 'yyyyMMdd-HHmmss'
$BackupDir  = Join-Path (Join-Path $BackupRoot $Target) $stamp
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

# 读取现有状态（用于幂等重注入）
$prevState = $null
if (Test-Path -LiteralPath $StatePath) {
    try { $prevState = Read-Utf8 $StatePath | ConvertFrom-Json } catch { $prevState = $null }
}

# 技能呈现模式：未显式指定（auto）时沿用上次记录。
# 必须在这里就定下来——指令集写入比技能部署靠前，晚了就来不及往标记块里加取用纪律。
if ($SkillMode -eq 'auto') {
    if ($prevState -and $prevState.skillMode -and (@('full', 'menu') -contains [string]$prevState.skillMode)) {
        $SkillMode = [string]$prevState.skillMode
    } else {
        $SkillMode = 'full'
    }
}

if ($SkillsOnly) {
    Say 'INFO' 'SkillsOnly 模式：只部署技能库，不改动指令集'
}

# 1) 注入指令文件
$hadPromptFile = Test-Path -LiteralPath $PromptTarget
# 「本工具是否从未创建过它」要跨次注入传递，否则重注入会把初次的值覆盖掉，
# 导致卸载时无法回到真正的初始状态（例如留下一个空 skills 目录）。
# 注：这些初值必须在下面覆盖之前赋值。
$origPromptFile = $hadPromptFile
$origSkillsDir  = Test-Path -LiteralPath $SkillsTarget
if ($prevState) {
    if ($null -ne $prevState.hadSkillsDir) { $origSkillsDir = [bool]$prevState.hadSkillsDir }
    if ($null -ne $prevState.hadPromptFile) { $origPromptFile = [bool]$prevState.hadPromptFile }
}
$promptBackup  = $null
$patchBackup   = $null
$patchFileRel  = $null
$budgetVal     = $null
$budgetSrcVal  = $null
$currentText   = ''
if ($hadPromptFile) { $currentText = Read-Utf8 $PromptTarget }

if ($prevState -and $prevState.promptBackup -and (Test-Path -LiteralPath $prevState.promptBackup)) {
    # 重复注入：沿用第一次的原始备份，不覆盖
    $promptBackup = [string]$prevState.promptBackup
    Say 'INFO' '检测到既有安装记录，沿用原始备份'
} elseif ($hadPromptFile -and $currentText.Trim().Length -gt 0) {
    # 备份「摘除本工具标记块之后」的用户原始内容；
    # 避免状态清单丢失后用当前（含旧标记块）的全量文件当原始备份，卸载时还原出旧注入。
    $origText = (Strip-MarkerBlock $currentText).TrimEnd()
    if ($origText.Length -gt 0) {
        $promptBackup = Join-Path $BackupDir $T.PromptName
        Write-Utf8NoBom $promptBackup ($origText + "`r`n")
        Say 'INFO' ('已备份原有 ' + $T.PromptName + '（不含本工具段落）-> ' + $promptBackup)
    } else {
        Say 'INFO' ('原有 ' + $T.PromptName + ' 仅含本工具段落，无用户内容需要备份')
    }
}

$promptBody = ''
if ($SkillsOnly) {
    Say 'INFO' ('跳过指令集写入（SkillsOnly）。PromptTarget 保持现状: ' + $PromptTarget)
    if (-not (Test-Path -LiteralPath $PromptTarget)) {
        # 连指令集文件都没有（从未注入过模板）——SkillsOnly 无意义，报错退出
        Fail ('SkillsOnly 需要先注入一次指令集模板（未找到 ' + $PromptTarget + '）')
    }
} else {
$baseText    = Strip-MarkerBlock $currentText
$promptBody  = Read-Utf8 $SourcePrompt
# 极简模式：模块技能不进系统提示词，全靠菜单技能带路。
# 只在菜单描述里写领域词还不够（那只是一条可选的技能描述），这里在
# APPEND_SYSTEM.md（系统级、每轮都在、优先级高于技能描述）里再硬性说一句。
$routeNote = ''
if ($SkillMode -eq 'menu') {
    $menuPath = Join-Path (Join-Path $SkillsTarget 'pi-workbench-menu') 'SKILL.md'
    $routeNote = "`r`n`r`n## 技能取用（本工作台部署）`r`n`r`n" +
        '需要专业技能的任务，先读技能菜单定位模块，再按需读该模块正文：' + "`r`n`r`n" +
        '- 菜单：`' + $menuPath + '`' + "`r`n" +
        '- 初始只取 1 个最匹配的模块；一个阶段最多加载 4 个模块正文。' + "`r`n" +
        '- 选定 / 换用模块时先报一行：`参考模块: <模块id>（<用途>）`。' + "`r`n" +
        '- 读不到模块正文就如实说，不得声称已按该模块执行。' + "`r`n" +
        '- 找不到匹配模块就用自身知识继续，不要为凑数读无关模块。' + "`r`n"
}
$block       = $MARK_BEG + "`r`n" + $promptBody.TrimEnd() + $routeNote + "`r`n" + $MARK_END
$newText     = ($baseText.TrimEnd() + "`r`n`r`n" + $block + "`r`n").TrimStart()
Write-Utf8NoBom $PromptTarget $newText
Say 'INFO' ('已写入指令集: ' + $PromptTarget + '（' + $promptBody.Length + ' 字符，版本 ' + $VersionLabel + '，模式 ' + $SkillMode + '）')
}

# 1b) DSH：home 级 patch 层提高 agent-instructions 的 maxBytes 预算。
# 预算取「现有配置或 dsh 默认值」，不按指令集大小自适应，
# 避免用户故意把预算调小时被本工具静默改大。
if (($Target -eq 'dsh') -and (-not $SkillsOnly)) {
    $hadPatchFile = Test-Path -LiteralPath $PatchFile
    $patchText = ''
    if ($hadPatchFile) { $patchText = Read-Utf8 $PatchFile }

    if ($prevState -and $prevState.patchBackup -and (Test-Path -LiteralPath $prevState.patchBackup)) {
        $patchBackup = [string]$prevState.patchBackup
    } elseif ($hadPatchFile -and $patchText.Trim().Length -gt 0) {
        $origPatch = (Strip-PatchBlock $patchText).TrimEnd()
        if ($origPatch.Length -gt 0) {
            $patchBackup = Join-Path $BackupDir 'cordis.patch.yml'
            Write-Utf8NoBom $patchBackup ($origPatch + "`r`n")
            Say 'INFO' ('已备份原有 cordis.patch.yml（不含本工具层）-> ' + $patchBackup)
        }
    }

    $patchBase = Strip-PatchBlock $patchText
    $blockBytes = [System.Text.Encoding]::UTF8.GetByteCount($block)
    $budgetInfo = Get-DshBudget
    $budget = [int]$budgetInfo[0]
    $budgetSource = [string]$budgetInfo[1]
    $frame = 700
    if ($budget -lt ($blockBytes + $frame)) {
        $Script:PatchWarn = '指令集 ' + $blockBytes + ' 字节 > 当前预算 ' + $budget + '，dsh 渲染时可能截断；建议把 maxBytes 调到 ' + ($blockBytes + $frame)
        Say 'WARN' $Script:PatchWarn
    }
    $patchLayer = Get-PatchBody $budget
    $newPatch = ($patchBase.TrimEnd() + "`r`n`r`n" + $patchLayer + "`r`n").TrimStart()
    Write-Utf8NoBom $PatchFile $newPatch
    $patchFileRel = $PatchFile
    $budgetVal    = $budget
    $budgetSrcVal = $budgetSource
    Say 'INFO' ('已写入 home 级 patch: ' + $PatchFile + '（agent-instructions maxBytes=' + $budget + '，来源 ' + $budgetSource + '）')
}

# 2) 技能库
$installedSkills  = @()
$overwrittenSkills = @{}
$hadSkillsDir = Test-Path -LiteralPath $SkillsTarget
if ($NoSkills) {
    Say 'INFO' '按参数跳过技能库部署'
    # 从完整版切到精简版：摘除上次由本工具部署的技能，
    # 否则状态清单会被覆盖，这些目录就变成卸载也清不掉的孤儿。
    if ($prevState -and $prevState.installedSkills) {
        $cleaned = 0
        foreach ($n in $prevState.installedSkills) {
            $oldDest = Join-Path $SkillsTarget $n
            $prevBk = $null
            if ($prevState.overwrittenSkills -and $prevState.overwrittenSkills.$n) {
                $prevBk = [string]$prevState.overwrittenSkills.$n
            }
            if ($prevBk -and (Test-Path -LiteralPath $prevBk)) {
                if (Test-Path -LiteralPath $oldDest) { Remove-Item -LiteralPath $oldDest -Recurse -Force -ErrorAction SilentlyContinue }
                $parentOld = Split-Path -Parent $oldDest
                if (-not (Test-Path -LiteralPath $parentOld)) { New-Item -ItemType Directory -Force -Path $parentOld | Out-Null }
                Copy-Tree $prevBk $oldDest
                Say 'INFO' ('已还原被覆盖的原有技能: ' + $n)
            } elseif (Test-Path -LiteralPath $oldDest) {
                Remove-Item -LiteralPath $oldDest -Recurse -Force -ErrorAction SilentlyContinue
                $cleaned++
            }
        }
        if ($cleaned -gt 0) { Say 'INFO' ('精简版：已移除上次部署的技能 ' + $cleaned + ' 个') }
        if (Test-Path -LiteralPath $SkillsTarget) {
            $leftAfter = @(Get-ChildItem -LiteralPath $SkillsTarget -Force -ErrorAction SilentlyContinue)
            if ($leftAfter.Count -eq 0 -and $prevState.hadSkillsDir -ne $true) {
                Remove-Item -LiteralPath $SkillsTarget -Force -ErrorAction SilentlyContinue
                Say 'INFO' '空的 skills 目录已清除'
            }
        }
    }
} else {
    $dirs = @()
    foreach ($srcPart in $SkillsSource -split '[;]') {
        if ([string]::IsNullOrWhiteSpace($srcPart)) { continue }
        $dirs += @(Get-SkillDirs $srcPart)
    }
    if ($dirs.Count -eq 0) {
        Say 'WARN' ('技能源目录为空: ' + $SkillsSource)
    } else {
        $seen = @{}
        $dirs = @($dirs | Where-Object { if ($seen.ContainsKey($_.Name)) { $false } else { $seen[$_.Name] = $true; $true } })
        if (-not (Test-Path -LiteralPath $SkillsTarget)) { New-Item -ItemType Directory -Force -Path $SkillsTarget | Out-Null }
        foreach ($d in $dirs) {
            $dest = Join-Path $SkillsTarget $d.Name
            if (Test-Path -LiteralPath $dest) {
                $alreadyOurs = $false
                if ($prevState -and $prevState.installedSkills -and ($prevState.installedSkills -contains $d.Name)) { $alreadyOurs = $true }
                if (-not $alreadyOurs) {
                    $bk = Join-Path (Join-Path $BackupDir 'skills') $d.Name
                    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bk) | Out-Null
                    Copy-Tree $dest $bk
                    $overwrittenSkills[$d.Name] = $bk
                    Say 'WARN' ('同名技能已存在，已备份原目录: ' + $d.Name)
                }
                if ($prevState -and $prevState.overwrittenSkills -and $prevState.overwrittenSkills.$($d.Name)) {
                    $overwrittenSkills[$d.Name] = [string]$prevState.overwrittenSkills.$($d.Name)
                }
            }
            Copy-Tree $d.FullName $dest
            $installedSkills += $d.Name
        }
        Say 'INFO' ('已部署技能 ' + $installedSkills.Count + ' 个 -> ' + $SkillsTarget)
    }
}

# 3) 状态清单

# SkillsOnly 只动技能库：版本元数据、指令集备份、技能清单都必须从上次记录合并，
# 否则会把「上次模板」冲成默认 V5.1b，并把 installedSkills 清空——
# 那 65 个已部署的技能就成了卸载也清不掉的孤儿（自检也会失去口径）。
$SkillsOnlyFlag = [bool]$SkillsOnly
if ($SkillsOnly -and $prevState) {
    if ($prevState.versionKey)   { $VersionKey   = $prevState.versionKey }
    if ($prevState.versionLabel) { $VersionLabel = $prevState.versionLabel }
    if ($null -ne $prevState.skillsOnly) { $SkillsOnlyFlag = [bool]$prevState.skillsOnly }
    if ($prevState.promptBackup) { $promptBackup = [string]$prevState.promptBackup }
    if ($null -ne $prevState.hadPromptFile) { $origPromptFile = [bool]$prevState.hadPromptFile }
    if ($null -ne $prevState.hadSkillsDir)  { $origSkillsDir  = [bool]$prevState.hadSkillsDir }
    if ($prevState.installedSkills) {
        $newCount = @($installedSkills).Count
        $merged = @($prevState.installedSkills)
        foreach ($n in $installedSkills) { if ($merged -notcontains $n) { $merged += $n } }
        $installedSkills = $merged
        Say 'INFO' ('技能清单合并：本次新增 ' + $newCount + ' 项，合并后共 ' + $merged.Count + ' 项')
    }
    if ($prevState.overwrittenSkills) {
        foreach ($prop in $prevState.overwrittenSkills.PSObject.Properties) {
            if (-not $overwrittenSkills.ContainsKey($prop.Name)) {
                $overwrittenSkills[$prop.Name] = [string]$prop.Value
            }
        }
    }
}

# 技能呈现模式：极简模式给模块加 disable-model-invocation（不进系统提示词）
# + 生成一个菜单技能进提示词；agent 按菜单里的模块 id 直接 read 对应 SKILL.md。
$MenuSkillName = 'pi-workbench-menu'
$MenuModules   = 0
if (-not $NoSkills) {
    if ($SkillMode -eq 'menu') {
        $menuable = @($installedSkills | Where-Object { $_ -ne $MenuSkillName })
        $keep = @()
        foreach ($k in ($MenuKeepAdvertised -split '[;]')) {
            $k = $k.Trim()
            if ($k) { $keep += $k }
        }
        $flagged = 0
        $unflagged = 0
        foreach ($n in $menuable) {
            $p = Join-Path (Join-Path $SkillsTarget $n) 'SKILL.md'
            if (-not (Test-Path -LiteralPath $p)) { continue }
            if ($keep -contains $n) {
                # 例外：去掉可能残留的标记，保持进提示词
                if (Remove-DisableModelInvocation $p) { $unflagged++ }
                continue
            }
            if (Add-DisableModelInvocation $p) { $flagged++ }
            else { Say 'WARN' ('frontmatter 异常，未能注入标记: ' + $n) }
        }
        # 菜单技能若已存在且不是上次我们部署的，先备份
        $menuDest = Join-Path $SkillsTarget $MenuSkillName
        if (Test-Path -LiteralPath $menuDest) {
            $menuWasOurs = ($prevState -and $prevState.installedSkills -and ($prevState.installedSkills -contains $MenuSkillName))
            if (-not $menuWasOurs) {
                $bk = Join-Path (Join-Path $BackupDir 'skills') $MenuSkillName
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bk) | Out-Null
                Copy-Tree $menuDest $bk
                $overwrittenSkills[$MenuSkillName] = $bk
                Say 'WARN' ('同名技能已存在，已备份原目录: ' + $MenuSkillName)
            }
            Remove-Item -LiteralPath $menuDest -Recurse -Force -ErrorAction SilentlyContinue
        }
        $MenuModules = New-SkillMenu -Target $SkillsTarget -ModuleIds $menuable -CatFile (Join-Path $Base 'skill-categories.json') -MenuName $MenuSkillName
        if ($MenuModules -gt 0) {
            if ($installedSkills -notcontains $MenuSkillName) { $installedSkills += $MenuSkillName }
            $keepNote = if ($keep.Count -gt 0) { '，' + $keep.Count + ' 个保持进提示词' } else { '' }
            Say 'INFO' ('极简模式：已生成菜单技能 ' + $MenuSkillName + '（列 ' + $MenuModules + ' 个模块），' + $flagged + ' 个模块已标记不进提示词' + $keepNote)
        } else {
            Say 'WARN' '极简模式：没有可列出的模块，菜单未生成，本次按完整模式呈现'
            $SkillMode = 'full'
        }
    } else {
        # 完整模式：清掉上次极简模式留下的菜单技能（否则它会成孤儿，卸载清不掉）
        $menuDest = Join-Path $SkillsTarget $MenuSkillName
        if (Test-Path -LiteralPath $menuDest) {
            $menuWasOurs = ($prevState -and $prevState.installedSkills -and ($prevState.installedSkills -contains $MenuSkillName))
            if ($menuWasOurs) {
                Remove-Item -LiteralPath $menuDest -Recurse -Force -ErrorAction SilentlyContinue
                $installedSkills = @($installedSkills | Where-Object { $_ -ne $MenuSkillName })
                Say 'INFO' ('已移除上次极简模式留下的菜单技能: ' + $MenuSkillName)
            }
        }
    }
}
$sourcePromptLeaf = (Split-Path -Leaf $SourcePrompt)
if ($SkillsOnly -and $prevState -and $prevState.sourcePrompt) { $sourcePromptLeaf = [string]$prevState.sourcePrompt }
$state = [ordered]@{
    version             = $TOOL_VER
    target              = $Target
    label               = $T.Label
    agentDir            = $T.AgentDir
    installedAt         = (Get-Date).ToString('o')
    promptFile          = $PromptTarget
    sourcePrompt        = $sourcePromptLeaf
    versionKey          = $VersionKey
    versionLabel        = $VersionLabel
    hadPromptFile       = $origPromptFile
    promptBackup        = $promptBackup
    installedSkills     = $installedSkills
    overwrittenSkills   = $overwrittenSkills
    skillsSource        = $SkillsSource
    skillsOnly          = $SkillsOnlyFlag
    skillMode           = $SkillMode
    menuSkill           = $(if ($SkillMode -eq 'menu') { $MenuSkillName } else { $null })
    menuKeepAdvertised  = $(if ($SkillMode -eq 'menu') { ($keep -join ';') } else { $null })
    skillsTarget        = $SkillsTarget
    hadSkillsDir        = $origSkillsDir
    patchFile           = $patchFileRel
    patchBackup         = $patchBackup
    budget              = $budgetVal
    budgetSource        = $budgetSrcVal
}
Write-Utf8NoBom $StatePath (($state | ConvertTo-Json -Depth 5) + "`r`n")
Say 'INFO' ('状态清单: ' + $StatePath)

$proc = Get-ClientProcess
if ($proc.Count -gt 0) {
    Say 'WARN' ($T.Label + ' 正在运行，需重启后生效')
} else {
    Say 'INFO' ($T.Label + ' 未运行，下次启动即生效')
}
if ($Target -eq 'dsh') {
    Say 'INFO' ('DSH 注入点：' + $PromptTarget + ' （agent-instructions 读作持久 user 消息）')
    if ($Script:PatchWarn) { Say 'WARN' $Script:PatchWarn }
}Say 'INFO' '注入完成。规范常驻在 addendum 段，重启客户端后直接使用即可'
Finish 'OK'
exit 0
