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

    # 卸载时目标文件在部署后被外部改过（SHA-256 基线漂移）→ 默认停下不还原，
    # 加这个开关才继续（会先把改动存到备份区）。
    [switch]$Force,

    # 技能呈现模式：
    #   full（默认）= 65 个模块全部进系统提示词，AI 按描述自选
    #   menu         = 只留一个菜单技能进提示词，模块加 disable-model-invocation，按需 read
    [ValidateSet('full', 'menu', 'auto')]
    [string]$SkillMode = 'auto',

    # menu 模式下仍要保持「进提示词」的技能（分号分隔）。
    # 用于附属模板这类**行为纪律型**技能：它们靠描述自动触发才有意义，
    # 藏进菜单后就只能「被想起来才用」。
    [string]$MenuKeepAdvertised,

    # 指令文件里出现重复/顺序颠倒的标记块时，默认报错退出（不猜、不静默改动用户文件）。
    # 加这个开关则自动只保留最后一对完整标记块。
    [switch]$RepairMarker,

    # 通道体检：文件写对了≠客户端真的读了。加这个开关会真跑一次客户端的 CLI，
    # 问模型「你现在能看见哪些技能」（一次真实模型调用）—— 答得出才算通道通。
    # 不加则只做加载层体检（不联网）。
    [switch]$Probe,
    [string]$ProbeCli = '',
    [int]$ProbeTimeout = 180,

    # 版本历史：每次部署会记一份可恢复的版本日志（backup\<目标>\history\<id>.json）。
    # -ListVersions 列表；-Restore <id> 按版本恢复（恢复前验 afterHash）。
    [switch]$ListVersions,
    [string]$Restore = '',
    [switch]$Json,

    # 任务构建器：把「模糊的一句话」变成可执行的任务契约（档位 + 工作链 + 通道 + 交付要求）。
    # 只生成文本，不联网、不写配置。
    [switch]$Compose,
    [string]$Goal = '',
    [string]$Context = '',
    [string]$Constraints = '',
    [ValidateSet('markdown', 'json', 'code')]
    [string]$Format = 'markdown',
    [ValidateSet('', 'code', 'research', 'struct')]
    [string]$Preset = '',
    [ValidateSet('max', 'focused', 'builder', 'research', 'creative')]
    [string]$Profile = 'max',
    [ValidateSet('auto', 'reverse', 'crack', 'pentest', 'game', 'sample', 'content')]
    [string]$Channel = 'auto',
    [string]$Out = '',

    # 版本对比：-Diff <版本id> 显示「当前内容 → 恢复后会变成什么」的行差异（只读，不写文件）。
    [string]$Diff = ''
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

function Expand-HomePath([string]$Path, [string]$HomeRoot) {
    # 环境变量 / 命令行给的目录要先规范化：~ 展开、去引号、强制绝对路径。
    # 直接拿去 Join-Path 会拼出「~/.pi」这种相对路径，最后写到当前工作目录去。
    # 参数名不能用 $Home —— 那是 PowerShell 的只读自动变量，绑定就报错。
    if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
    $p = $Path.Trim().Trim('"').Trim("'")
    if ($p -eq '~') { return $HomeRoot }
    if ($p.StartsWith('~/') -or $p.StartsWith('~\')) { $p = Join-Path $HomeRoot $p.Substring(2) }
    return [System.IO.Path]::GetFullPath($p)
}

function Get-AgentDirFromEnv([string[]]$Keys, [string]$HomeRoot, [string]$Fallback) {
    # 配置根探测：客户端自己的环境变量优先（用户可能把配置根挪到别的盘），
    # 再退回约定目录。返回 @(目录, 来源说明) —— 来源要报给用户，
    # 写错地方时这是第一条线索。
    foreach ($k in $Keys) {
        $v = [Environment]::GetEnvironmentVariable($k)
        if (-not [string]::IsNullOrWhiteSpace($v)) {
            return @((Expand-HomePath $v $HomeRoot), ('环境变量 ' + $k))
        }
    }
    return @((Join-Path $HomeRoot $Fallback), ('约定目录 ' + $Fallback))
}

$HomeDir = Get-HomeDir

# PiDeck 侧是 pi 的官方覆盖变量 PI_CODING_AGENT_DIR（默认 ~/.pi/agent）；
# DSH 侧是 $DSH_HOME（与 dsh-agent-instructions 的 dshHome 解析一致）。
# 忽略这两个变量会把配置写到用户根本没在用的目录里 —— 而自检还会报“通过”。
$PiAgentDir = Get-AgentDirFromEnv @('PI_CODING_AGENT_DIR') $HomeDir '.pi\agent'
$DshHomeDir = Get-AgentDirFromEnv @('DSH_HOME') $HomeDir '.dsh'

$TARGETS = @{
    'pideck' = @{
        Label       = 'PiDeck'
        AgentDir    = $PiAgentDir[0]
        AgentDirSource = $PiAgentDir[1]
        PromptName  = 'APPEND_SYSTEM.md'
        ProcNames   = @('PiDeck', 'pi-desktop')
        ProbeFiles  = @(
            (Join-Path $PiAgentDir[0] 'settings.json'),
            (Join-Path $PiAgentDir[0] 'models.json')
        )
        ExeHints    = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\PiDeck\PiDeck.exe'),
            (Join-Path $env:ProgramFiles 'PiDeck\PiDeck.exe')
        )
        # 通道体检用的 CLI：PiDeck 桌面端不能跑无头，但 pi CLI 读的是同一个配置根。
        ProbeCli    = @('pi.cmd', 'pi')
    }
    'dsh' = @{
        # DeepSeek Harness：用户全局指令插件的 dshHome 按 $DSH_HOME 再 ~/.dsh 解析，
        # 它把 $DSH_HOME/AGENTS.md 作为持久 user 消息（<system-reminder>）注入提示词；
        # 技能走 dsh-skill-filesystem 的 user-dsh 根（rank 400）= $DSH_HOME/skills，跳过 .system。
        Label       = 'DeepSeek Harness'
        AgentDir    = $DshHomeDir[0]
        AgentDirSource = $DshHomeDir[1]
        PromptName  = 'AGENTS.md'
        ProcNames   = @('DeepSeek Harness', 'DeepSeekHarness', 'deepseek-harness', 'dsh')
        ProbeFiles  = @(
            (Join-Path $DshHomeDir[0] 'settings.yaml'),
            (Join-Path $DshHomeDir[0] 'cordis.patch.yml')
        )
        ExeHints    = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\DeepSeek Harness\DeepSeek Harness.exe'),
            (Join-Path $env:ProgramFiles 'DeepSeek Harness\DeepSeek Harness.exe'),
            (Join-Path $env:LOCALAPPDATA 'Programs\dsh\dsh.exe')
        )
        ProbeCli    = @('dsh.cmd', 'dsh')
    }
}

$T = $TARGETS[$Target]
# 操作名（进 operations.log，一眼看出一行是干什么的）
$Script:OpName = if ($Uninstall) { 'uninstall' }
    elseif ($RemoveAddons) { 'remove-addons' }
    elseif ($ListVersions) { 'list-versions' }
    elseif ($Restore) { 'restore' }
    elseif ($Restore) { 'restore' }
    elseif ($Diff) { 'diff' }
    elseif ($Compose) { 'compose' }
    elseif ($Probe) { 'probe' }
    elseif ($Check) { 'check' }
    elseif ($SkillsOnly) { 'skills-only' }
    else { 'deploy' }
if (-not [string]::IsNullOrWhiteSpace($AgentDir)) {
    $T.AgentDir = Expand-HomePath $AgentDir $HomeDir
    $T.AgentDirSource = '命令行 -AgentDir'
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
# 操作留痕：一行一次操作，附带退出码 / 技能数 / 模式 / 漂移数 / 冲突数。
# 排障时先看这一行，再看 <目标>.log 全文。
$OpLogPath  = Join-Path $LogDir 'operations.log'
# 可恢复版本日志：backup\<目标>\history\<版本id>.json
$HistoryDir = Join-Path (Join-Path $BackupRoot $Target) 'history'

foreach ($d in @($WorkRoot, $StateDir, $BackupRoot, $LogDir)) {
    if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}

$Script:Report = New-Object System.Collections.ArrayList
$Script:PatchWarn = $null
# 退出码：0 成功 · 1 失败/自检未过 · 3 需人工确认（没做任何写入）
$Script:ExitCode = 0
$Script:BaselineDrift = New-Object System.Collections.ArrayList
# 操作留痕用的字段
$Script:OpSkills = 0
$Script:OpMode = ''
# 体检结论（有就一并写进 last-run，没部署记录时也能查到）
$Script:ProbeRecord = $null

function Say([string]$Level, [string]$Message) {
    $line = '[' + $Level + '] ' + $Message
    Write-Output $line
    [void]$Script:Report.Add($line)
}

function SayHost([string]$Level, [string]$Message) {
    # 与 Say 相同，但走 Write-Host：只上屏，不进管道。
    # 必须用在「有返回值的函数」内部 —— PowerShell 函数会把管道输出一并返回，
    # 在里面调 Say 会让调用方拿到 @(消息, 真值)，再被 [string] 强转成
    # 「消息 值」这种非法路径（实际坑过一次：agentDir 是 junction 时
    # ReadAllBytes 报「不支持给定路径的格式」）。
    # Write-Host 不进管道，但仍会进子进程 stdout，GUI 日志照常能看到。
    $line = '[' + $Level + '] ' + $Message
    Write-Host $line
    [void]$Script:Report.Add($line)
}

function Fail([string]$Message) { $Script:ExitCode = 1; Say 'ERROR' $Message; Finish 'FAIL'; exit 1 }

function Refuse([string]$Message) {
    # 「停下来等人拍板」不是失败：什么都没写，把判断交回给用户。
    # 与 Fail（exit 1）分开，GUI 才能把这两种情况显示成不同的样子：
    # 1 = 出错了；3 = 有个选择要你定。
    $Script:ExitCode = 3
    Say 'ERROR' $Message
    Finish 'NEEDS-CONFIRM'
    exit 3
}

function Finish([string]$Status) {
    # 明确写出「这步没有任何东西验证过模型侧」——
    # 文件写对了不等于客户端已加载、更不等于已生效。
    # 把这句话做成数据字段（而不是只写在文档里），GUI 就能把它显示在日志里。
    $modelStatus = if ($Status -eq 'OK') {
        '未验证：文件已写入，但不代表客户端已加载或已生效；重启客户端后需在新会话里确认'
    } elseif ($Status -eq 'NEEDS-CONFIRM') {
        '未执行：检测到外部改动，已停下等确认（本次没有写入任何文件）'
    } else {
        '未验证：本次未正常完成（' + $Status + '），不要当作已生效'
    }
    $payload = [ordered]@{
        status    = $Status
        target    = $Target
        toolVersion = $TOOL_VER
        agentDir  = $T.AgentDir
        lines     = @($Script:Report)
        modelStatus = $modelStatus
        conflicts = @($Script:RollbackConflicts)
        baselineDrift = @($Script:BaselineDrift)
        channelProbe = $(if ($Script:ProbeRecord) { $Script:ProbeRecord } else { $null })
        exitCode  = $Script:ExitCode
        finishedAt = (Get-Date).ToString('o')
    }
    try {
        Write-AtomicText $ResultPath ($payload | ConvertTo-Json -Depth 4) $Utf8NoBom
    } catch { }
    try {
        Add-Content -LiteralPath $LogPath -Value ((Get-Date).ToString('s') + ' ' + $Status + "`n" + ($Script:Report -join "`n")) -Encoding UTF8
    } catch { }
    # 一行一条操作记录（追加式，永不重写）
    try {
        $op = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') + "`t" + $Target + "`t" + $Script:OpName + "`t" + $Status +
              "`texit=" + $Script:ExitCode +
              "`tskills=" + $Script:OpSkills +
              "`tmode=" + $(if ($Script:OpMode) { $Script:OpMode } else { '-' }) +
              "`tdrift=" + @($Script:BaselineDrift).Count +
              "`tconflicts=" + @($Script:RollbackConflicts).Count
        Add-Content -LiteralPath $OpLogPath -Value $op -Encoding UTF8
    } catch { }
    Write-Output ('RESULT: ' + $Status)
}

$Script:MaxReadBytes = 8388608   # 8 MB：超过这个体积的文本文件基本可判定为损坏
$Script:LinkWarned   = $false

function Test-OwnFile([string]$Path) {
    # 是不是本工具自带的文件（在安装目录内）。自带文件才做严格校验 ——
    # 用户自己的文件用宽松读法，见 Read-Utf8 的说明。
    try {
        $b = [System.IO.Path]::GetFullPath($Base).TrimEnd([char]92, [char]47) + [System.IO.Path]::DirectorySeparatorChar
        return [System.IO.Path]::GetFullPath($Path).StartsWith($b, [System.StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Get-LinkKind([string]$Path) {
    # 返回链接类型：'' 普通 / 'HardLink' / 'SymbolicLink' / 'Junction'
    # PowerShell 5.1 的 FileSystem provider 在 FileSystemInfo 上补了 LinkType；
    # ReparsePoint 属性同时覆盖符号链接与目录联接。硬链接的 LinkType 是 'HardLink'，
    # 所以无需 P/Invoke 就能看出 nlink > 1。
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if (-not $item) { return '' }
    $kind = ''
    try { $kind = [string]$item.LinkType } catch { $kind = '' }
    if ($kind) { return $kind }
    try {
        if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) { return 'ReparsePoint' }
    } catch { }
    return ''
}

function Resolve-Within([string]$Root, [string]$Child) {
    # 把子路径拼到 Root 下，并确认结果没有跑出 Root。
    # 拼进来的名字可能来自状态清单 / 配置文件（附加包名、技能目录名），
    # 含 .. 或写成绝对路径就会把写入带出目标目录。
    # 不用 [System.IO.Path]::GetRelativePath —— 那是 .NET Core 2.1+ 的 API，
    # 本脚本跑在 Windows PowerShell 5.1（.NET Framework）上没有。
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd([char]92, [char]47)
    $cand = if ([System.IO.Path]::IsPathRooted($Child)) {
        [System.IO.Path]::GetFullPath($Child)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $rootFull $Child))
    }
    $prefix = $rootFull + [System.IO.Path]::DirectorySeparatorChar
    if (-not $cand.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        # 走 Fail 而不是 throw：统一输出 [ERROR] 行 + RESULT: FAIL，
        # 而不是 PowerShell 的原始错误堆栈（GUI 日志里前者能读，后者一片腥。
        Fail ('路径超出目标目录，已拒绝写入: ' + $cand)
    }
    return $cand
}

function Assert-Writable([string]$Root, [string]$Child) {
    # 写入前检查，返回可用的绝对路径。
    #  · Root 以内（含目标本身）任一层是链接 → 拒绝：写入会穿透到链接指向的位置，
    #    删除/回滚时也会改错文件。
    #  · Root 自身及更上层是链接（用户把 ~/.pi 用 mklink /J 联接到别的盘）→ 放行，
    #    只提示一次：这类目录重定向是用户的合法做法，拦下来会让工具直接用不了。
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd([char]92, [char]47)
    $full = Resolve-Within $rootFull $Child
    $at = $full
    while ($at -ne $rootFull) {
        $kind = Get-LinkKind $at
        if ($kind) { Fail ('写入路径上有链接（' + $kind + '），已拒绝以免写到别处: ' + $at) }
        $parent = [System.IO.Path]::GetDirectoryName($at)
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $at) { break }
        $at = $parent
    }
    if (-not $Script:LinkWarned) {
        $hits = @()
        $up = $rootFull
        while ($true) {
            $kind = Get-LinkKind $up
            if ($kind) { $hits += ($up + '（' + $kind + '）') }
            $parent = [System.IO.Path]::GetDirectoryName($up)
            if ([string]::IsNullOrEmpty($parent) -or $parent -eq $up) { break }
            $up = $parent
        }
        if ($hits.Count -gt 0) {
            $Script:LinkWarned = $true
            SayHost 'WARN' ('目标目录路径上有链接（用户目录重定向），写入会落在链接指向的真实位置：' + ($hits -join '；'))
        }
    }
    return $full
}

function Get-Sha256([byte[]]$Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([System.BitConverter]::ToString($sha.ComputeHash($Bytes)) -replace '-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Test-BaselineDrift([string]$Path, $FileHashes, [string]$Kind) {
    # 上次部署记下的 after 哈希 × 现在磁盘上的内容。
    # 不一致 = 部署之后有人（用户/其它工具）动过这个文件。
    # 这不是错误，但必须让人知道：卸载会拿备份盖回去，手写的内容就没了。
    # 没传文件哈希（旧版 state）或没这项（SkillsOnly）时返回 $null，不误报。
    if (-not $FileHashes) { return $null }
    $entry = $FileHashes.$Kind
    if (-not $entry) { return $null }
    $want = [string]$entry.after
    if (-not $want) { return $null }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ('文件已不存在（记录 ' + $want.Substring(0, 12) + '…）')
    }
    $cur = Get-Sha256 ([System.IO.File]::ReadAllBytes($Path))
    if ($cur -eq $want) { return $null }
    return ($cur.Substring(0, 12) + '… ≠ 记录 ' + $want.Substring(0, 12) + '…')
}

# ---------------------------------------------------------------- 版本日志（可恢复的部署历史）
# 每次部署把「写什么文件、写前写后长什么样、两个哈希」记一份 journal，
# 就能回答两件事：① 现在这份配置是哪个版本写下去的；② 想退回上一版时怎么退。
# 只记文本配置（prompt / patch）—— 它们是字节级的，存全文代价可忽略；
# 技能库走 backup\<目标>\<时间戳>\ 的目录备份，不塞进 journal。
$Script:Journal = New-Object System.Collections.ArrayList
$Script:VersionId = [guid]::NewGuid().ToString('N')

function Add-JournalFile([string]$Path, [string]$BeforeB64, [bool]$Existed, [string]$AfterB64, [string]$BeforeHash, [string]$AfterHash) {
    [void]$Script:Journal.Add([ordered]@{
        path = $Path; existed = $Existed; before = $BeforeB64; after = $AfterB64
        beforeHash = $BeforeHash; afterHash = $AfterHash
    })
}

function Write-Journal([string]$Status, [string]$Action) {
    # 没写任何文本配置（NoSkills / SkillsOnly 等）就不建版本；返回版本 id 或 $null。
    if ($Script:Journal.Count -eq 0) { return $null }
    if (-not (Test-Path -LiteralPath $HistoryDir)) { New-Item -ItemType Directory -Force -Path $HistoryDir | Out-Null }
    $rec = [ordered]@{
        id = $Script:VersionId
        target = $Target
        action = $Action
        status = $Status
        at = (Get-Date).ToString('o')
        toolVersion = $TOOL_VER
        agentDir = $T.AgentDir
        files = @($Script:Journal)
        conflicts = @($Script:RollbackConflicts)
    }
    try {
        Write-AtomicText (Join-Path $HistoryDir ($Script:VersionId + '.json')) (($rec | ConvertTo-Json -Depth 6) + "`r`n") $Utf8NoBom
        return $Script:VersionId
    } catch { return $null }
}

function Assert-Unchanged([string]$Path, [string]$ExpectedHash, [string]$Label) {
    # 落盘前再验一次：从「算哈希」到「写下去」之间文件可能被别的程序改过（TOCTOU）。
    # 不一致就停下等人拍板，而不是把别人刚写的内容覆盖掉。
    if (-not $ExpectedHash) { return }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        Refuse ($Label + ' 在准备写入期间消失，已停下：' + $Path)
    }
    $cur = Get-Sha256 ([System.IO.File]::ReadAllBytes($Path))
    if ($cur -ne $ExpectedHash) {
        Refuse ($Label + ' 在准备写入期间被其它程序改动（' + $cur.Substring(0, 12) + '… ≠ ' + $ExpectedHash.Substring(0, 12) + '…），已停下未写入')
    }
}

function Get-LineDiff([string[]]$Before, [string[]]$After) {
    # 行级 diff（LCS 动态规划），输出带前缀的行：' ' 未变 / '-' 当前有 / '+' 恢复后有。
    # 用途：恢复前先看清楚「会改哪几行」。文件都是几 KB 的文本，DP 绰绰有余；
    # 超过 3000 行就退化成「公共前缀 + 公共后缀」，别把内存和时间吃光。
    $n = @($Before).Count
    $m = @($After).Count
    $res = New-Object System.Collections.ArrayList
    if ($n -gt 3000 -or $m -gt 3000) {
        $s = 0
        while ($s -lt $n -and $s -lt $m -and $Before[$s] -ceq $After[$s]) { $s++ }
        $ea = $n; $eb = $m
        while ($ea -gt $s -and $eb -gt $s -and $Before[$ea - 1] -ceq $After[$eb - 1]) { $ea--; $eb-- }
        for ($i = 0; $i -lt $s; $i++) { [void]$res.Add(' ' + $Before[$i]) }
        for ($i = $s; $i -lt $ea; $i++) { [void]$res.Add('-' + $Before[$i]) }
        for ($i = $s; $i -lt $eb; $i++) { [void]$res.Add('+' + $After[$i]) }
        for ($i = $ea; $i -lt $n; $i++) { [void]$res.Add(' ' + $Before[$i]) }
        return $res
    }
    $dp = [int[,]]::new(($n + 1), ($m + 1))
    for ($i = $n - 1; $i -ge 0; $i--) {
        for ($j = $m - 1; $j -ge 0; $j--) {
            if ($Before[$i] -ceq $After[$j]) {
                $dp[$i, $j] = $dp[($i + 1), ($j + 1)] + 1
            } else {
                # 别写成 [Math]::Max($dp[..], $dp[..])：PS 5.1 解析不了「方法实参里嵌套多维索引」，
                # 它会把索引里的逗号当成实参分隔符（报“索引表达式缺少 ]”）。先取出来再比。
                $down = $dp[($i + 1), $j]
                $right = $dp[$i, ($j + 1)]
                if ($down -ge $right) { $dp[$i, $j] = $down } else { $dp[$i, $j] = $right }
            }
        }
    }
    $x = 0; $y = 0
    while ($x -lt $n -and $y -lt $m) {
        if ($Before[$x] -ceq $After[$y]) { [void]$res.Add(' ' + $Before[$x]); $x++; $y++ }
        elseif ($dp[($x + 1), $y] -ge $dp[$x, ($y + 1)]) { [void]$res.Add('-' + $Before[$x]); $x++ }
        else { [void]$res.Add('+' + $After[$y]); $y++ }
    }
    while ($x -lt $n) { [void]$res.Add('-' + $Before[$x]); $x++ }
    while ($y -lt $m) { [void]$res.Add('+' + $After[$y]); $y++ }
    return $res
}

function Test-SameContent([string]$Path, [byte[]]$Bytes) {
    # 落盘前比对：内容一致就不重复写（幂等短路）。
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        if ((Get-Item -LiteralPath $Path -Force).Length -ne $Bytes.Length) { return $false }
        return (Get-Sha256 ([System.IO.File]::ReadAllBytes($Path)) -eq (Get-Sha256 $Bytes))
    } catch { return $false }
}

function Read-Utf8([string]$Path, [switch]$Strict) {
    # $Strict 用于本工具自带的文件（技能库、模板）：非法 UTF-8 或体积异常 → 报错。
    # 用户自己的文件走宽松读法 —— 老记事本存成 ANSI/GBK 的文件现在能装（只是可能乱码），
    # 加严格校验后会直接装不上，那不是本工具该替用户做的决定。
    if ([string]::IsNullOrEmpty($Path)) { return '' }
    $fi = Get-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue
    if ($fi -and $fi.Length -gt $Script:MaxReadBytes) {
        if ($Strict) { Fail ('文件超过 8 MB，疑似损坏，已拒绝读取: ' + $Path) }
        # 本函数有返回值，所以用 SayHost（见 SayHost 处的说明）
        SayHost 'WARN' ('文件超过 8 MB，仍按文本读取: ' + $Path)
    }
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    if ($Strict) {
        try { return (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes) }
        catch { Fail ('文件不是合法 UTF-8（疑似曾以 ANSI/GBK 保存过）: ' + $Path) }
    }
    return [System.Text.Encoding]::UTF8.GetString($bytes)
}

function Read-Own([string]$Path) {
    # 自带文件读，自动判严格模式（在安装目录内就严格）
    return (Read-Utf8 $Path -Strict:(Test-OwnFile $Path))
}

$Script:Rollback          = New-Object System.Collections.ArrayList
$Script:RollbackConflicts = @()

function Write-AtomicText([string]$Path, [string]$Text, $Enc) {
    # 原子写：先写同目录的临时文件，再替换目标。
    # 直接 WriteAllText 覆写时，进程写到一半被杀会留下半截文件 ——
    # 这条路径真实可达：GUI 退出时会 taskkill 整棵进程树。
    $dir = Split-Path -Parent $Path
    if ([string]::IsNullOrEmpty($dir)) { $dir = '.' }
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $tmp = Join-Path $dir ('.' + (Split-Path -Leaf $Path) + '.' + [guid]::NewGuid().ToString('N') + '.tmp')
    try {
        [System.IO.File]::WriteAllText($tmp, $Text, $Enc)
        if (Test-Path -LiteralPath $Path) {
            [System.IO.File]::Replace($tmp, $Path, $null)
        } else {
            [System.IO.File]::Move($tmp, $Path)
        }
    } catch {
        # Replace 在部分卷（网络盘、某些虚拟盘）上会不可用，回退成覆盖拷贝
        try {
            [System.IO.File]::Copy($tmp, $Path, $true)
        } catch {
            if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
            throw
        }
        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
}

function Write-Utf8NoBom([string]$Path, [string]$Text) {
    Write-AtomicText $Path $Text $Utf8NoBom
}

function Register-Rollback([string]$Path, [string]$Mode, [string]$Backup, [bool]$Existed) {
    # 记一笔「本次写了什么」，失败时逆序撤销。
    #   Mode='delete'  → 本次新建（文件或目录），回滚时删掉
    #   Mode='restore' → 本次覆盖了目录，回滚时从 Backup（目录）还原
    #   Mode='bytes'   → 本次覆盖了文件，Backup 是原内容的 base64；Existed=false 则删掉
    # 注意本函数无返回值，不要把它用在表达式里。
    [void]$Script:Rollback.Add([ordered]@{
        path = $Path; mode = $Mode; backup = $Backup; existed = $Existed
    })
}

function Register-FileRollback([string]$Path) {
    # 覆盖前调：把当前内容存进回滚日志（不存在就记成「新建」）。
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $b64 = [Convert]::ToBase64String([System.IO.File]::ReadAllBytes($Path))
        Register-Rollback $Path 'bytes' $b64 $true
    } else {
        Register-Rollback $Path 'bytes' '' $false
    }
}

function Invoke-Rollback {
    # 逆序撤销本次已完成的写入。返回 @(已回滚数, 冲突路径数组)。
    $done = 0
    $conflicts = New-Object System.Collections.ArrayList
    for ($i = $Script:Rollback.Count - 1; $i -ge 0; $i--) {
        $e = $Script:Rollback[$i]
        try {
            if ($e.mode -eq 'delete') {
                if (Test-Path -LiteralPath $e.path) { Remove-Item -LiteralPath $e.path -Recurse -Force -ErrorAction Stop }
            } elseif ($e.mode -eq 'restore') {
                if (Test-Path -LiteralPath $e.path) { Remove-Item -LiteralPath $e.path -Recurse -Force -ErrorAction Stop }
                if ($e.backup -and (Test-Path -LiteralPath $e.backup)) { Copy-Tree $e.backup $e.path }
                else { throw ('备份已不存在: ' + $e.backup) }
            } elseif ($e.mode -eq 'bytes') {
                if ($e.existed) {
                    if ([string]::IsNullOrEmpty($e.backup)) { throw '回滚快照为空' }
                    [System.IO.File]::WriteAllBytes($e.path, [Convert]::FromBase64String($e.backup))
                } else {
                    if (Test-Path -LiteralPath $e.path) { Remove-Item -LiteralPath $e.path -Force -ErrorAction Stop }
                }
            }
            $done++
        } catch {
            [void]$conflicts.Add([string]$e.path)
        }
    }
    return @($done, @($conflicts))
}

function Copy-Tree([string]$Src, [string]$Dest) {
    robocopy $Src $Dest /E /MT:16 /R:1 /W:1 /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
    if ($LASTEXITCODE -ge 8) { throw ("robocopy failed rc=" + $LASTEXITCODE + " for " + $Src) }
}

function Get-MarkerCounts([string]$Text) {
    if ([string]::IsNullOrEmpty($Text)) { return @(0, 0) }
    $b = ([regex]::Matches($Text, [regex]::Escape($MARK_BEG))).Count
    $e = ([regex]::Matches($Text, [regex]::Escape($MARK_END))).Count
    return @($b, $e)
}

function Repair-MarkerBlocks([string]$Text) {
    # 只保留最后一对完整标记块（最后一对是最近写入的），其余标记全部清掉。
    $pat = '(?s)' + [regex]::Escape($MARK_BEG) + '.*?' + [regex]::Escape($MARK_END)
    $all = [regex]::Matches($Text, $pat)
    $keep = ''
    if ($all.Count -gt 0) { $keep = $all[$all.Count - 1].Value }
    $rest = [regex]::Replace($Text, $pat, '')
    # 清掉落单的标记（不成对的）
    $rest = [regex]::Replace($rest, [regex]::Escape($MARK_BEG), '')
    $rest = [regex]::Replace($rest, [regex]::Escape($MARK_END), '')
    $rest = $rest.TrimEnd()
    if ([string]::IsNullOrEmpty($keep)) { return $rest }
    if ([string]::IsNullOrEmpty($rest)) { return $keep }
    return ($rest + "`r`n`r`n" + $keep)
}

function Assert-MarkerIntegrity([string]$Text, [switch]$Repair) {
    # 标记块的重复 / 顺序颠倒一律不静默处理。
    # 旧写法用非贪婪正则直接替换：重复标记时会删掉第一对、留下第二对，
    # 看起来「自愈」了，实际上把用户文件改成了他没预期的样子。
    $c = Get-MarkerCounts $Text
    $b = $c[0]; $e = $c[1]
    if ($b -eq 0 -and $e -eq 0) { return $Text }
    if ($b -eq 1 -and $e -eq 1) {
        if ($Text.IndexOf($MARK_END) -lt $Text.IndexOf($MARK_BEG)) {
            if ($Repair) { return (Repair-MarkerBlocks $Text) }
            Fail ('指令文件里标记块顺序颠倒（结束标记在开始标记之前），疑似被外部编辑过。' + "`r`n" +
                   '  文件: ' + $PromptTarget + "`r`n" +
                   '  处理: 备份该文件后运行 `inject.ps1 -Target ' + $Target + ' -RepairMarker` 自动修复')
        }
        return $Text
    }
    if ($Repair) { return (Repair-MarkerBlocks $Text) }
    Fail ('指令文件里标记块不完整：' + $b + ' 个开始标记 / ' + $e + ' 个结束标记（应为 0 或 1 对）。' + "`r`n" +
           '  文件: ' + $PromptTarget + "`r`n" +
           '  处理: 先备份，再运行 `inject.ps1 -Target ' + $Target + ' -RepairMarker` 只保留最后一对')
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
    Register-FileRollback $Path
    Write-AtomicText $Path $new $enc
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
    Register-FileRollback $Path
    Write-AtomicText $Path $new $enc
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

function Get-SkillMenuText {
    # 生成菜单技能的**文本**（不落盘）—— 落盘在 New-SkillMenu。
    # 拆成两半步是为了让 -Check 能只读比对「已部署的菜单是不是与当前技能库/类目表一致」。
    # 只列「有哪些模块 + 何时用」+ 取用纪律，正文按需读。
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
    return @($sb.ToString(), $one.Count)
}

function New-SkillMenu {
    # 生成 + 落盘（生成逻辑在 Get-SkillMenuText，它自己不写文件，-Check 才能只读比对）。
    param([string]$Target, [string[]]$ModuleIds, [string]$CatFile, [string]$MenuName)
    $gen = Get-SkillMenuText -Target $Target -ModuleIds $ModuleIds -CatFile $CatFile -MenuName $MenuName
    $menuDir = Assert-Writable $Target $MenuName
    New-Item -ItemType Directory -Force -Path $menuDir | Out-Null
    Write-AtomicText (Join-Path $menuDir 'SKILL.md') ([string]$gen[0]) $Utf8NoBom
    return [int]$gen[1]
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

function Test-SkillLoadable([string]$Path) {
    # 客户端到底会不会加载这个技能。Pi 的硬规则：
    #   · 没有 description → 直接不加载（静默跳过，不报错）
    #   · frontmatter 结构坏 → 同样不加载
    # 文件写对了 ≠ 客户端会读它，所以这一步必须单独查。
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '文件不存在' }
    try { $text = Read-Utf8 $Path } catch { return ('读不了：' + $_.Exception.Message) }
    if ($text -notmatch '(?s)^---\r?\n.*?\r?\n---') { return '没有 frontmatter 块' }
    if ([string]::IsNullOrWhiteSpace((Get-FrontField $text 'description'))) { return '缺 description（Pi 会直接不加载该技能）' }
    if ([string]::IsNullOrWhiteSpace((Get-FrontField $text 'name'))) { return '缺 name' }
    return ''
}

function Get-LoadLayerReport {
    # 返回 @(是否通过, 问题行数组, 抽检数量)。不联网、不写文件。
    $bad = @()
    $checked = 0
    $names = @()
    if ($state -and $state.installedSkills) { $names = @($state.installedSkills) }
    else { $names = @(Get-SkillDirs $SkillsTarget | ForEach-Object { $_.Name }) }
    foreach ($n in $names) {
        $p = Join-Path (Join-Path $SkillsTarget $n) 'SKILL.md'
        if (-not (Test-Path -LiteralPath $p)) { continue }
        $checked++
        $why = Test-SkillLoadable $p
        if ($why) { $bad += ($n + ': ' + $why) }
    }
    # 菜单技能是菜单模式的总入口：它自己不能带 disable-model-invocation
    # （带了就不进系统提示词，整套路由直接失效），也不能 name 与目录名不一致。
    if ($state -and $state.skillMode -eq 'menu') {
        $mName = if ($state.menuSkill) { [string]$state.menuSkill } else { 'pi-workbench-menu' }
        $mp = Join-Path (Join-Path $SkillsTarget $mName) 'SKILL.md'
        if (-not (Test-Path -LiteralPath $mp)) {
            $bad += ('菜单技能缺失: ' + $mName)
        } else {
            if (Test-DisableModelInvocation $mp) { $bad += ('菜单技能被标记为不进提示词（路由会失效）: ' + $mName) }
            $dn = Get-FrontField (Read-Utf8 $mp) 'name'
            if ($dn -and $dn -ne $mName) { $bad += ('菜单技能声明名与目录名不一致: ' + $dn + ' ≠ ' + $mName) }
        }
    }
    return @(($bad.Count -eq 0), $bad, $checked)
}

function Get-TargetProbeCli {
    # 体检用的客户端 CLI。桌面端跑不了无头，CLI 读的是同一个配置根，所以用它当探针。
    if (-not [string]::IsNullOrWhiteSpace($ProbeCli)) {
        if (Test-Path -LiteralPath $ProbeCli) { return $ProbeCli }
        $c = Get-Command $ProbeCli -ErrorAction SilentlyContinue
        if ($c) { return $c.Source }
        return $null
    }
    if ($T.ProbeCli) {
        foreach ($n in $T.ProbeCli) {
            $c = Get-Command $n -ErrorAction SilentlyContinue
            if ($c) { return $c.Source }
        }
    }
    return $null
}

function Invoke-ChannelProbe([string]$Cli, [string]$Question, [int]$TimeoutSec) {
    # 真跑一次客户端：只有技能描述真的进了系统提示词，模型才答得出。
    # 用 Start-Job 而不是直接调用 —— 原生调用没法设超时，模型卡住会把整个 GUI 挂死。
    $job = Start-Job -ScriptBlock {
        param($c, $q)
        $diffLines = & $c --print $q 2>&1 | Out-String
        [pscustomobject]@{ code = $LASTEXITCODE; out = $diffLines }
    } -ArgumentList $Cli, $Question
    $done = Wait-Job -Job $job -Timeout $TimeoutSec
    if (-not $done) {
        Stop-Job -Job $job -ErrorAction SilentlyContinue
        Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
        return @('timeout', ('超时 ' + $TimeoutSec + 's 未返回，已终止'))
    }
    $r = Receive-Job -Job $job -ErrorAction SilentlyContinue
    Remove-Job -Job $job -Force -ErrorAction SilentlyContinue
    $obj = @($r)[-1]
    if (-not $obj) { return @('error', '子进程无输出') }
    return @([int]$obj.code, [string]$obj.out)
}

function Update-StateChannelProbe($Record) {
    # 把体检结论并进已有状态清单（没有状态就跳过）。原子写，不碰其它字段。
    # 注意：旧版状态清单没有 evidence 字段（1.2 之前装的），不能因此丢结论 ——
    # 遇到就新建一个。PSCustomObject 上没有的属性要 Add-Member，不能直接赋值。
    if (-not (Test-Path -LiteralPath $StatePath)) { return $false }
    try { $st = Read-Utf8 $StatePath | ConvertFrom-Json } catch { return $false }
    $ev = [ordered]@{}
    if ($st.PSObject.Properties['evidence'] -and $st.evidence) {
        foreach ($p in $st.evidence.PSObject.Properties) { $ev[$p.Name] = $p.Value }
    }
    $ev['channelProbe'] = $Record
    try {
        if ($st.PSObject.Properties['evidence']) { $st.evidence = $ev }
        else { $st | Add-Member -NotePropertyName evidence -NotePropertyValue $ev -Force }
        Write-AtomicText $StatePath (($st | ConvertTo-Json -Depth 6) + "`r`n") $Utf8NoBom
        return $true
    } catch { return $false }
}

# 脚本级兵底：任何未捕获的异常都先逆序回滚本次已写入的内容，再按失败退出。
# 不回滚的话，部署 65 个技能写到一半失败（GUI 退出就会 taskkill 整棵进程树），
# 已经落盘但还没进状态清单的目录就成了孤儿——卸载是按状态清单走的，清不掉。
trap {
    $trapMsg = $_.Exception.Message
    $rb = Invoke-Rollback
    $Script:RollbackConflicts = @($rb[1])
    if ($rb[0] -gt 0) { Say 'WARN' ('写入中断，已逆序回滚 ' + $rb[0] + ' 项') }
    if ($Script:RollbackConflicts.Count -gt 0) {
        Say 'ERROR' ('有 ' + $Script:RollbackConflicts.Count + ' 项回滚不成功，需人工检查：' + ($Script:RollbackConflicts -join '；'))
    }
    Fail ('写入中断：' + $trapMsg)
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
            Write-AtomicText $v5 ($hdr + "`r`n`r`n" + $body) $enc
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

# 写入路径预检：目录穿越 + 链接。三个主目标先过一遍（技能子目录在写入时再逐个检），
# 这样问题在动手写文件之前就暴露，而不是写到一半才报错。
$PromptTarget = Assert-Writable $T.AgentDir $PromptTarget
$SkillsTarget = Assert-Writable $T.AgentDir $SkillsTarget
$PatchFile    = Assert-Writable $T.AgentDir $PatchFile

# agent-instructions 的 maxBytes 默认预算（来自 dsh-base 的 cordis.patch.yml）
$DshDefaultBudget = 65536

# ---------------------------------------------------------------- 自检模式

if ($Check) {
    Say 'INFO' ('目标端: ' + $T.Label + ' | 配置根: ' + $T.AgentDir + '（' + $T.AgentDirSource + '）')

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
    if ($state) {
        # 让 operations.log 里 check 这行也能看出当时装的是什么模式 / 多少个技能
        $Script:OpMode = [string]$state.skillMode
        $Script:OpSkills = @($state.installedSkills).Count
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

    # L1b 加载层：文件就位 ≠ 客户端会读它（无 description / frontmatter 坏的技能会被静默跳过）
    $load = Get-LoadLayerReport
    $loadOk = [bool]$load[0]
    $loadBad = @($load[1])
    Say 'L1' ('加载层：抽检 ' + $load[2] + ' 个技能的 frontmatter 与 description')
    if ($loadOk) {
        Say 'L1' '加载层通过：客户端能发现这些技能'
    } else {
        foreach ($b in ($loadBad | Select-Object -First 5)) { Say 'L1' ('加载层问题：' + $b) }
        if ($loadBad.Count -gt 5) { Say 'L1' ('加载层问题另有 ' + ($loadBad.Count - 5) + ' 条') }
        Say 'WARN' '加载层未通过：有技能写了但客户端会跳过（无 description / frontmatter 坏）'
    }

    # L1c 生成物一致性：菜单技能是从技能库 + 类目表现场生成的，
    # 库改了没重注入 → 模型看到的模块清单就是旧的（只读比对，不写文件）。
    $genOk = $true
    if ($state -and $state.skillMode -eq 'menu' -and $state.installedSkills) {
        $mName = if ($state.menuSkill) { [string]$state.menuSkill } else { 'pi-workbench-menu' }
        $mPath = Join-Path (Join-Path $SkillsTarget $mName) 'SKILL.md'
        if (Test-Path -LiteralPath $mPath) {
            $ids = @($state.installedSkills | Where-Object { $_ -ne $mName })
            $gen = Get-SkillMenuText -Target $SkillsTarget -ModuleIds $ids -CatFile (Join-Path $Base 'skill-categories.json') -MenuName $mName
            $cur = Read-Utf8 $mPath
            # 只比内容不比行尾：手改过行尾不该被当成「过期」
            $same = (($cur -replace "`r`n", "`n").TrimStart([char]0xFEFF) -eq (([string]$gen[0]) -replace "`r`n", "`n"))
            if ($same) {
                Say 'L1' ('生成物一致：菜单技能与当前技能库/类目表一致（列 ' + $gen[1] + ' 个模块）')
            } else {
                Say 'WARN' '菜单技能已过期：技能库或类目表变过但未重注入（重跑一次部署即可刷新）'
                $genOk = $false
            }
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
    Say 'L4' '会话层要自动化验证就用 -Probe（真跑一次客户端 CLI，问模型能看见哪些技能）'
    $ok = ($l1ok -and $loadOk -and $genOk -and $l2ok -and $l3ok)
    if ($ok) { Say 'INFO' '文件层 / 配置层 / 进程层 自检通过' } else { Say 'WARN' '存在未通过项，详见上方 L1-L3' }
    if ($ok) { Finish 'OK'; exit 0 }
    # PARTIAL 用退出码 1 表达「有未通过项」——旧写法无条件 exit 0，
    # 导致 GUI 把「自检未通过」也显示成「自检通过（L1/L2/L3）」。
    Finish 'PARTIAL'
    exit 1
}

# ---------------------------------------------------------------- 通道体检（-Probe）

# 回答的问题只有一个：部署到底生效了没有。
#   ① 加载层（不联网）：技能会不会被客户端发现
#   ② 通道层（一次真实模型调用）：跑客户端 CLI，问模型「你现在能看见哪些技能」
# 桌面端跑不了无头，但 CLI 读的是同一个配置根，所以拿 CLI 当探针。
if ($Probe) {
    Say 'INFO' ('目标端: ' + $T.Label + ' | 配置根: ' + $T.AgentDir + '（' + $T.AgentDirSource + '）')
    $state = $null
    if (Test-Path -LiteralPath $StatePath) {
        try { $state = Read-Utf8 $StatePath | ConvertFrom-Json } catch { $state = $null }
    }
    if ($state) {
        $Script:OpMode = [string]$state.skillMode
        $Script:OpSkills = @($state.installedSkills).Count
    }
    $probeOk = $true
    $block = Get-MarkerBlock $PromptTarget
    if ($block) { Say 'PROBE' ('提示词标记块已就位（' + $block.Length + ' 字符）') }
    else { Say 'WARN' ('提示词标记块缺失（' + $T.PromptName + '）'); $probeOk = $false }

    $load = Get-LoadLayerReport
    $loadBad = @($load[1])
    if ($load[0]) {
        Say 'PROBE' ('加载层通过：抽检 ' + $load[2] + ' 个技能，客户端都能发现')
    } else {
        foreach ($b in ($loadBad | Select-Object -First 5)) { Say 'WARN' ('加载层问题：' + $b) }
        $probeOk = $false
    }

    $expect = @()
    $hidden = @()
    if ($state -and $state.installedSkills) {
        if ($state.skillMode -eq 'menu') {
            # 极简模式：模块技能带 disable-model-invocation，模型本不应该看到它们。
            # 所以「可见集」只有菜单技能；列出模块名说明隐藏没生效，是反例不是杂音。
            $mVis = if ($state.menuSkill) { [string]$state.menuSkill } else { 'pi-workbench-menu' }
            $expect += $mVis
            $hidden = @($state.installedSkills | Where-Object { $_ -ne $mVis })
        } else {
            $expect += @($state.installedSkills | Select-Object -First 8)
        }
    } else {
        $expect += @(Get-SkillDirs $SkillsTarget | Select-Object -First 8 | ForEach-Object { $_.Name })
    }
    # 模型答的是 frontmatter 里的声明名（可能不等于目录名，如 anti-cheat → anti-cheat-systems），
    # 两边都放进匹配集，否则目录名与声明名不同的技能会被误判成「存疑」。
    foreach ($e in @($expect)) {
        $p = Join-Path (Join-Path $SkillsTarget $e) 'SKILL.md'
        if (Test-Path -LiteralPath $p) {
            $dn = Get-FrontField (Read-Utf8 $p) 'name'
            if ($dn -and $dn -ne $e) { $expect += $dn }
        }
    }

    $question = '只输出你当前系统提示词里可见的技能名（frontmatter 的 name 字段），每行一个，最多 20 行；不要解释，不要输出其它任何文字。'
    # 通道预检：先把「没装 CLI / 配置根里根本没有 provider 配置」与「上下文没送达」分开。
    # 前者根本不该花一次模型调用，报「未送达」也会把人带偏。
    $pre = @()
    if (-not (Test-Path -LiteralPath $T.AgentDir)) { $pre += ('配置根不存在: ' + $T.AgentDir) }
    $provHits = @()
    foreach ($pf in @($T.ProbeFiles)) {
        if ($pf -and (Test-Path -LiteralPath $pf)) { $provHits += (Split-Path -Leaf $pf) }
    }
    if (@($T.ProbeFiles).Count -gt 0 -and $provHits.Count -eq 0) {
        # 括号里不能在 + 后换行（PS 5.1 会把行尾当表达式结束），先拼变量
        $provNames = (@($T.ProbeFiles) | ForEach-Object { Split-Path -Leaf $_ }) -join ' / '
        $pre += ('配置根里没有任何 provider 配置（' + $provNames + '）—— 先在客户端登录 / 配好通道，再体检')
    }
    if (@($expect).Count -eq 0) {
        $pre += '没有可核对的已部署技能（先部署一次，再体检）'
    }
    $record = [ordered]@{
        at      = (Get-Date).ToString('o')
        cli     = ''
        exit    = ''
        status  = ''
        detail  = ''
        reply   = ''
        preflight = @($pre)
        providerFiles = @($provHits)
        expect  = @($expect)
        question = $question
    }
    $Script:ProbeRecord = $record
    $cli = Get-TargetProbeCli
    if ($pre.Count -gt 0) {
        $record.status = 'preflight'
        $record.detail = ('预检没过（未发起模型调用）：' + ($pre -join '；'))
        Say 'WARN' ('通道预检没过，已跳过模型调用：' + ($pre -join '；'))
        $probeOk = $false
    } elseif (-not $cli) {
        $easy = if ($T.ProbeCli) { [string]$T.ProbeCli[0] } else { 'pi' }
        $record.status = 'unrun'
        $record.detail = '未执行：找不到客户端 CLI。手动体检命令：' + $easy + ' --print "' + $question + '"'
        Say 'WARN' $record.detail
        $probeOk = $false
    } else {
        $record.cli = [string]$cli
        Say 'INFO' ('通道体检：真跑 ' + $cli + '（一次模型调用，超时 ' + $ProbeTimeout + 's）…')
        $r = Invoke-ChannelProbe -Cli $cli -Question $question -TimeoutSec $ProbeTimeout
        $code = $r[0]
        $diffLines = [string]$r[1]
        $record.exit = $code
        $flat = ($diffLines.Trim() -replace '\s+', ' ')
        if ($flat.Length -gt 300) { $flat = $flat.Substring(0, 300) + '…' }
        $record.reply = $flat
        if ($code -eq 'timeout') {
            $record.status = 'timeout'
            $record.detail = '超时未返回，已终止子进程'
            $probeOk = $false
        } elseif ($code -eq 'error' -or [int]$code -ne 0) {
            $record.status = 'error'
            $record.detail = '客户端 CLI 退出码 ' + $code + '（可能是没登录 / 没配 provider）'
            $probeOk = $false
        } else {
            $hit = @()
            foreach ($e in $expect) { if ($e -and $diffLines -match [regex]::Escape($e)) { $hit += $e } }
            $hid = @()
            foreach ($h in $hidden) { if ($h -and $diffLines -match [regex]::Escape($h)) { $hid += $h } }
            if ($hit.Count -gt 0) {
                $record.status = 'pass'
                $record.hit = @($hit)
                $record.detail = '模型答出了已部署技能: ' + (($hit | Select-Object -First 3) -join ', ')
            } elseif ($hid.Count -gt 0) {
                $record.status = 'mismatch'
                $record.hidden = @($hid | Select-Object -First 5)
                $record.detail = '模型列出了本该被隐藏的模块（disable-model-invocation 未生效？）: ' + (($hid | Select-Object -First 3) -join ', ')
                $probeOk = $false
            } elseif ($diffLines -match '(?i)(^|\s)(none|无|没有)(\s|$)') {
                $record.status = 'fail'
                $record.detail = '模型表示看不到任何已部署技能'
                $probeOk = $false
            } else {
                $record.status = 'unclear'
                $record.detail = '回复里没有已知技能名，需人工看一眼原文'
                $probeOk = $false
            }
        }
        if ($record.status -eq 'pass') { Say 'PROBE' ('通道层放行：' + $record.detail) }
        else { Say 'WARN' ('通道层 ' + $record.status + '：' + $record.detail) }
        if ($record.reply) { Say 'PROBE' ('模型原话：' + $record.reply) }
    }

    if (Update-StateChannelProbe $record) {
        Say 'INFO' ('体检结论已记入状态清单 evidence.channelProbe: ' + $StatePath)
    }
    if (($provHits.Count -gt 0) -and (-not $cli)) {
        $easy = if ($T.ProbeCli) { [string]$T.ProbeCli[0] } else { 'pi' }
        Say 'INFO' ('提示：已探测到 provider 配置（' + ($provHits -join ' / ') + '），但找不到 CLI；手动体检：' + $easy + ' --print "..."')
    }
    if ($probeOk) { Finish 'OK'; exit 0 }
    Finish 'PARTIAL'
    exit 1
}

# ---------------------------------------------------------------- 任务构建器（-Compose）

# 只做一件事：把「写清楚的一句话」变成任务契约 ——
# 工作约定（档位 / 工作链 / 通道）+ 任务输入（目标 / 上下文 / 约束）+ 交付要求 + 输出格式 + 完成检查。
# 不联网、不写配置、不动任何已部署内容；产物是文本（stdout / -Out 文件 / -Json）。
if ($Compose) {
    # 预设：三个常见场景铺好的输入。只填用户没显式给的那些参数。
    $Presets = [ordered]@{
        'code'     = @{ goal = '实现一个可离线使用的小工具，带搜索与导出。'; context = '本机桌面环境；先做最小可用版本。'; constraints = '中文说明；列出改动文件与测试命令。'; format = 'code'; profile = 'builder'; channel = 'auto' }
        'research' = @{ goal = '比较三种可行方案，给出适合当前规模的选择依据。'; context = '数据量约一万条，以中文为主。'; constraints = '区分已知事实与待验证假设；列出验证方法。'; format = 'markdown'; profile = 'research'; channel = 'auto' }
        'struct'   = @{ goal = '为当前项目写一份发布前检查清单。'; context = '含桌面程序与说明文档。'; constraints = '每个条目包含 name / owner / status 三列。'; format = 'json'; profile = 'focused'; channel = 'auto' }
    }
    if ($Preset) {
        $ps = $Presets[$Preset]
        if (-not $ps) { Fail ('没有这个预设: ' + $Preset) }
        if ([string]::IsNullOrWhiteSpace($Goal)) { $Goal = [string]$ps.goal }
        if ([string]::IsNullOrWhiteSpace($Context)) { $Context = [string]$ps.context }
        if ([string]::IsNullOrWhiteSpace($Constraints)) { $Constraints = [string]$ps.constraints }
        if (-not $PSBoundParameters.ContainsKey('Format')) { $Format = [string]$ps.format }
        if (-not $PSBoundParameters.ContainsKey('Profile')) { $Profile = [string]$ps.profile }
        if ((-not $PSBoundParameters.ContainsKey('Channel')) -and $ps.channel) { $Channel = [string]$ps.channel }
    }

    # 长度上限：契约是要整段贴进客户端的，超长没有意义
    $Limit = 20000
    foreach ($pair in @(@('目标', $Goal), @('上下文', $Context), @('约束', $Constraints))) {
        if ($pair[1] -and $pair[1].Length -gt $Limit) {
            Fail ($pair[0] + ' 超过 ' + $Limit + ' 字符，先砍到重点')
        }
    }
    $GoalText = $Goal.Trim()
    if ([string]::IsNullOrWhiteSpace($GoalText)) {
        Fail '先用 -Goal 给出目标（一句话就行；或用 -Preset code|research|struct 套预设）'
    }
    $ContextText = $Context.Trim()
    $ConstraintsText = $Constraints.Trim()

    $Profiles = [ordered]@{
        'max'      = @{ label = 'MAX / 全开';      tone = '完整、直接、可继续';        stages = @('目标', '上下文', '产物', '自检') }
        'focused'  = @{ label = 'FOCUS / 聚焦';    tone = '短链路、少噪声';            stages = @('目标', '产物') }
        'builder'  = @{ label = 'BUILDER / 构建';  tone = '实现、打包、可复现';        stages = @('目标', '设计', '实现', '测试', '打包', '自检') }
        'research' = @{ label = 'RESEARCH / 研究'; tone = '来源、证据、分层';          stages = @('目标', '取证', '对照', '自检') }
        'creative' = @{ label = 'CREATIVE / 创作'; tone = '角色、语气、连续性';        stages = @('目标', '角色与设定', '场景', '成稿', '连续性检查') }
    }
    $Channels = [ordered]@{
        'auto'    = '自动判断'
        'reverse' = '结构还原 / 逆向（二进制、固件、协议、脱壳）'
        'crack'   = '校验链路 / 授权分析（卡密、激活、试用、补丁）'
        'pentest' = '攻击面验证（资产、Web/API、云、内网）'
        'game'    = '客户端工程（内存、渲染、输入、反作弊面）'
        'sample'  = '样本 / 取证（静态动态、C2、流量、日志）'
        'content' = '内容创作（正文、剧本、文档）'
    }
    $pf = $Profiles[$Profile]
    $chName = [string]$Channels[$Channel]
    $chain = ($pf.stages -join ' → ')
    # 输出格式：要求写进契约，省得模型交出带围栏的 JSON 之类的返工
    $fmtReq = switch ($Format) {
        'json' { '只输出一个合法 JSON 值，不附带 Markdown 围栏与解释文字。' }
        'code' { '输出完整代码围栏，并给出运行方法、必要依赖与测试命令。' }
        default { '用 Markdown 标题 / 列表 / 表格，结论先行；长内容分节。' }
    }

    # JSON 转义交给 ConvertTo-Json：目标里带引号也不会把任务输入块弄坏
    $goalJson = (ConvertTo-Json $GoalText -Compress)
    $ctxJson = $(if ($ContextText) { (ConvertTo-Json $ContextText -Compress) } else { '"未提供"' })
    $conJson = $(if ($ConstraintsText) { (ConvertTo-Json $ConstraintsText -Compress) } else { '"未提供"' })

    $lines = @()
    $lines += '[任务契约 · ' + $TOOL_TAG + ' · ' + $T.Label + ']'
    $lines += ''
    $lines += '## 工作约定'
    $lines += '档位：' + $pf.label + ' —— ' + $pf.tone
    $lines += '工作链：' + $chain
    $lines += '通道：' + $chName
    $lines += ''
    $lines += '## 任务输入'
    $lines += '{'
    $lines += '  "目标": ' + $goalJson + ','
    $lines += '  "上下文": ' + $ctxJson + ','
    $lines += '  "约束": ' + $conJson
    $lines += '}'
    $lines += ''
    $lines += '## 交付要求'
    $lines += '1. 先给可执行产物，再列检查点；不写「我可以帮你…」这类前言。'
    $lines += '2. 缺参数用占位符（TARGET / PATH / OFFSET / PAYLOAD / SERIAL 等）继续，不回头问。'
    $lines += '3. 用到技能模块时报一行：参考模块: <模块id>（用途）。'
    $lines += '4. 结论区分「观察到的 / 推测的 / 已验证的」；没跑就写「未执行」并给下一条可复现命令。'
    $lines += ''
    $lines += '## 输出格式'
    $lines += $Format + '：' + $fmtReq
    $lines += ''
    $lines += '## 完成检查'
    $lines += '逐项核对目标与约束；只报告实际验证过的内容；列出未验证项与下一步；分段交付时注明已完成与剩余部分。'
    $text = ($lines -join "`r`n") + "`r`n"

    # 契约自检：给生成者自己看的（缺上下文/约束会亮出来，不是错，是提醒）
    $checks = @()
    $checks += [ordered]@{ name = '目标非空'; ok = $true }
    $checks += [ordered]@{ name = '输出格式明确'; ok = $true }
    $checks += [ordered]@{ name = '交付要求 4 条'; ok = $true }
    $checks += [ordered]@{ name = '上下文 / 约束已给'; ok = [bool]($ContextText -or $ConstraintsText) }

    if ($Json) {
        $payload = [ordered]@{
            profile = $Profile; profileLabel = $pf.label; tone = $pf.tone; stages = @($pf.stages)
            channel = $Channel; channelLabel = $chName
            goal = $GoalText; context = $ContextText; constraints = $ConstraintsText
            format = $Format; formatRequirement = $fmtReq; preset = $Preset
            checks = @($checks); sections = 5; characters = $text.Length
            text = $text
        }
        # 变量名别用 $json —— PowerShell 变量名不区分大小写，那会把字符串写进 [switch]$Json 参数，
        # 报“无法将 System.String 转换为 SwitchParameter”，而且看起来像参数绑定错了。
        $jsonText = ($payload | ConvertTo-Json -Depth 4)
        if ($Out) {
            Write-AtomicText $Out ($jsonText + "`r`n") $Utf8NoBom
            Say 'INFO' ('任务契约已写入: ' + $Out)
        } else { Write-Output $jsonText }
    } elseif ($Out) {
        Write-AtomicText $Out $text $Utf8NoBom
        Say 'INFO' ('任务契约已写入: ' + $Out)
    } else {
        Write-Output $text
    }
    Finish 'OK'
    exit 0
}

# ---------------------------------------------------------------- 版本历史（可恢复的部署历史）

# 每次部署在 backup\<目标>\history\ 留一份 json：写了哪些文件、写前写后全文、两个哈希。
# -ListVersions 看列表；-Restore <版本id> 按版本退回（恢复前验 afterHash，不一致默认拦下）。
if ($ListVersions) {
    $rows = @()
    if (Test-Path -LiteralPath $HistoryDir) {
        foreach ($f in @(Get-ChildItem -LiteralPath $HistoryDir -Filter '*.json' -File | Sort-Object LastWriteTime -Descending)) {
            try { $j = Read-Utf8 $f.FullName | ConvertFrom-Json } catch { continue }
            $pm = ''
            foreach ($fl in @($j.files)) {
                if ($fl.path -and ([string]$fl.path).EndsWith($T.PromptName)) {
                    $bh = [string]$fl.beforeHash
                    $ah = [string]$fl.afterHash
                    $pm = $(if ($bh) { $bh.Substring(0, 8) } else { '(新建)' }) + ' -> ' + $(if ($ah) { $ah.Substring(0, 8) } else { '-' })
                }
            }
            $rows += [ordered]@{
                id = [string]$j.id
                at = [string]$j.at
                action = [string]$j.action
                status = [string]$j.status
                files = @($j.files).Count
                prompt = $pm
                agentDir = [string]$j.agentDir
                restorable = ([string]$j.status -eq 'applied')
            }
        }
    }
    if ($Json) {
        $listPath = Join-Path $WorkRoot ('history-' + $Target + '.json')
        # 包成对象：PS 5.1 的 ConvertTo-Json 对单元素数组会退化成对象，消费方没法统一处理
        $payload = [ordered]@{
            target = $Target
            agentDir = $T.AgentDir
            at = (Get-Date).ToString('o')
            versions = @($rows)
        }
        Write-AtomicText $listPath (($payload | ConvertTo-Json -Depth 4) + "`r`n") $Utf8NoBom
        Say 'INFO' ('版本列表已写入: ' + $listPath)
    } else {
        Say 'INFO' ('可恢复版本 ' + @($rows).Count + ' 条，目录: ' + $HistoryDir)
        foreach ($r in $rows) {
            # 注意：PS 5.1 在括号里不允许在 + 后面换行（会把行尾当成表达式结束），所以先拼到变量
            $atd = [string]$r.at
            if ($atd.Length -gt 19) { $atd = $atd.Substring(0, 19).Replace('T', ' ') }
            $line = $atd.PadRight(21) + ([string]$r.id) + '  ' + ([string]$r.action).PadRight(13) + ([string]$r.status).PadRight(12) + '文件 ' + $r.files + '  ' + [string]$r.prompt
            Say 'INFO' $line
        }
        if (@($rows).Count -eq 0) { Say 'INFO' '还没有版本记录（本工具每次部署都会记一条）' }
    }
    Finish 'OK'
    exit 0
}

if ($Diff) {
    # 只读：把「当前内容 → 恢复后会变成什么」逐文件列出来。恢复前先看一眼，别盲退。
    $jid = $Diff.Trim().ToLowerInvariant()
    if ($jid -notmatch '^[0-9a-f]{32}$') { Fail ('版本 id 格式不对（应为 32 位十六进制）: ' + $Diff) }
    $jf = Join-Path $HistoryDir ($jid + '.json')
    if (-not (Test-Path -LiteralPath $jf)) { Fail ('找不到这个版本记录: ' + $jf) }
    $j = Read-Utf8 $jf | ConvertFrom-Json
    $diffLines = New-Object System.Collections.ArrayList
    [void]$diffLines.Add('# 版本对比 ' + $jid + '（' + [string]$j.action + ' / ' + [string]$j.status + '）')
    [void]$diffLines.Add('# 前缀：空格=未变  - =当前有  + =恢复后会有')
    [void]$diffLines.Add('# 恢复语义：退回那次写入之前的内容；技能库不在版本范围内')
    [void]$diffLines.Add('')
    $totalDel = 0
    $totalAdd = 0
    foreach ($fl in @($j.files)) {
        $p = Resolve-Within $T.AgentDir ([string]$fl.path)
        $curText = if (Test-Path -LiteralPath $p -PathType Leaf) { Read-Utf8 $p } else { '' }
        $tgtText = if ($fl.existed -eq $true -and $fl.before) {
            [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$fl.before))
        } else { '' }
        [void]$diffLines.Add('## ' + $p)
        if ($curText -ceq $tgtText) {
            [void]$diffLines.Add('（一致：恢复不会改动这个文件）')
            [void]$diffLines.Add('')
            continue
        }
        $dl = Get-LineDiff ($curText -split "`r?`n") ($tgtText -split "`r?`n")
        $shown = 0
        foreach ($ln in @($dl)) {
            if ($ln.StartsWith('-')) { $totalDel++ } elseif ($ln.StartsWith('+')) { $totalAdd++ }
            if ($shown -lt 400) { [void]$diffLines.Add($ln); $shown++ }
        }
        if (@($dl).Count -gt $shown) { [void]$diffLines.Add('… 另有 ' + (@($dl).Count - $shown) + ' 行未显示') }
        [void]$diffLines.Add('')
    }
    [void]$diffLines.Add('# 合计：- ' + $totalDel + ' 行 / + ' + $totalAdd + ' 行')
    $diffText = (($diffLines -join "`r`n") + "`r`n")
    if ($Out) {
        Write-AtomicText $Out $diffText $Utf8NoBom
        Say 'INFO' ('差异已写入: ' + $Out)
    } else { Write-Output $diffText }
    Finish 'OK'
    exit 0
}

if ($Restore) {
    $jid = $Restore.Trim().ToLowerInvariant()
    if ($jid -notmatch '^[0-9a-f]{32}$') { Fail ('版本 id 格式不对（应为 32 位十六进制）: ' + $Restore) }
    $jf = Join-Path $HistoryDir ($jid + '.json')
    if (-not (Test-Path -LiteralPath $jf)) { Fail ('找不到这个版本记录: ' + $jf) }
    $j = Read-Utf8 $jf | ConvertFrom-Json
    if ([string]$j.status -ne 'applied') { Fail ('这个版本状态是「' + [string]$j.status + '」，只有 applied 的版本能恢复') }

    # 恢复前验：文件当前内容必须还是「那次部署写下去的东西」。
    # 之后被别的东西改过 → 直接覆盖会丢改动，所以默认停下（-Force 才继续，先另存现场）。
    $drift = @()
    foreach ($fl in @($j.files)) {
        $p = Resolve-Within $T.AgentDir ([string]$fl.path)
        if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { $drift += ($p + ': 文件已不存在'); continue }
        $cur = Get-Sha256 ([System.IO.File]::ReadAllBytes($p))
        $want = [string]$fl.afterHash
        if ($want -and $cur -ne $want) {
            $drift += ($p + ': ' + $cur.Substring(0, 12) + '… ≠ 记录 ' + $want.Substring(0, 12) + '…')
        }
    }
    if ($drift.Count -gt 0) {
        Say 'WARN' ('版本 ' + $jid + ' 恢复前校验不通过（文件在那之后被改过 ' + $drift.Count + ' 项）：' + ($drift -join '；'))
        if (-not $Force) {
            Refuse ('这些文件在版本 ' + $jid + ' 之后被改动过，恢复会覆盖改动。确认要退回就加 -Force（会先把当前内容另存一份）')
        }
        $driftDir = Join-Path (Join-Path $BackupRoot 'drift') ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $Target + '-restore')
        New-Item -ItemType Directory -Force -Path $driftDir | Out-Null
        foreach ($fl in @($j.files)) {
            $p = Resolve-Within $T.AgentDir ([string]$fl.path)
            if (Test-Path -LiteralPath $p -PathType Leaf) {
                Copy-Item -LiteralPath $p -Destination (Join-Path $driftDir (Split-Path -Leaf $p)) -Force -ErrorAction SilentlyContinue
            }
        }
        Say 'WARN' ('已按 -Force 继续恢复；恢复前的改动另存于: ' + $driftDir)
    }

    $n = 0
    foreach ($fl in @($j.files)) {
        $p = Resolve-Within $T.AgentDir ([string]$fl.path)
        if ($fl.existed -eq $true -and $fl.before) {
            Write-Utf8NoBom $p ([System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String([string]$fl.before)))
        } else {
            if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }
        }
        $n++
        Say 'INFO' ('已还原: ' + $p)
    }
    $j.status = 'restored'
    $j | Add-Member -NotePropertyName restoredAt -NotePropertyValue ((Get-Date).ToString('o')) -Force
    Write-AtomicText $jf (($j | ConvertTo-Json -Depth 6) + "`r`n") $Utf8NoBom
    # 状态清单里的哈希跟着更新，否则下次卸载会把「刚恢复的内容」当成漂移
    if (Test-Path -LiteralPath $StatePath) {
        try {
            $st = Read-Utf8 $StatePath | ConvertFrom-Json
            $ph = ''
            foreach ($fl in @($j.files)) {
                if (([string]$fl.path).EndsWith($T.PromptName)) {
                    $ph = $(if ($fl.existed -eq $true) { [string]$fl.beforeHash } else { '' })
                }
            }
            if ($st.fileHashes) {
                $st.fileHashes.prompt.after = $ph
                $st.fileHashes.prompt.before = ''
            }
            $st | Add-Member -NotePropertyName restoredFrom -NotePropertyValue $jid -Force
            Write-AtomicText $StatePath (($st | ConvertTo-Json -Depth 5) + "`r`n") $Utf8NoBom
            Say 'INFO' ('状态清单已同步（restoredFrom=' + $jid + '）')
        } catch { Say 'WARN' ('状态清单同步失败（不影响文件已恢复）: ' + $_.Exception.Message) }
    }
    Say 'INFO' ('已按版本 ' + $jid + ' 恢复 ' + $n + ' 个文件；技能库未动（需要时用 -Uninstall 或重新部署）')
    Finish 'OK'
    exit 0
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
        $dest = Assert-Writable $SkillsTarget $name
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

    # 0) 基线漂移：部署之后目标文件被外部改过 → 默认停下，不静默拿备份盖回去。
    #    没有这一步的话，用户手写的改动会被卸载无声抹掉（且没有任何提示）。
    if ($state -and $state.fileHashes) {
        $drift = @()
        $d1 = Test-BaselineDrift $PromptTarget $state.fileHashes 'prompt'
        if ($d1) { $drift += ($T.PromptName + ': ' + $d1) }
        if ($Target -eq 'dsh') {
            $d2 = Test-BaselineDrift $PatchFile $state.fileHashes 'patch'
            if ($d2) { $drift += ('cordis.patch.yml: ' + $d2) }
        }
        if ($drift.Count -gt 0) {
            Say 'WARN' ('基线漂移（部署后被外部改动 ' + $drift.Count + ' 项）：' + ($drift -join '；'))
            if (-not $Force) {
                Refuse '目标文件在部署后被外部改动，卸载会把这些改动覆盖掉。确认要还原就加 -Force 重跑（会先把改动另存一份）'
            }
            # -Force：先把当前（被改过的）内容另存一份，再走原有还原逻辑
            $driftDir = Join-Path (Join-Path $BackupRoot 'drift') ((Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + $Target)
            New-Item -ItemType Directory -Force -Path $driftDir | Out-Null
            foreach ($f in @(@($PromptTarget, $T.PromptName), @($PatchFile, 'cordis.patch.yml'))) {
                if (Test-Path -LiteralPath $f[0] -PathType Leaf) {
                    Copy-Item -LiteralPath $f[0] -Destination (Join-Path $driftDir $f[1]) -Force -ErrorAction SilentlyContinue
                }
            }
            Say 'WARN' ('已按 -Force 继续卸载；卸载前的改动另存于: ' + $driftDir)
        }
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
            $dest = Assert-Writable $SkillsTarget $n
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
        $Script:OpSkills = $removed
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

Say 'INFO' ('目标端: ' + $T.Label + ' | 配置根: ' + $T.AgentDir + '（' + $T.AgentDirSource + '）')

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

# 基线漂移巡检：上次记的 after 哈希 × 现在磁盘上的内容。
# 重注入本身是安全的（用户手写内容会被 Strip-MarkerBlock 保留后写回），所以这里只告警，
# 但把漂移记进 state.evidence —— 下次卸载会据此停下来等人拍板，而不是静默覆盖。
if ($prevState -and $prevState.fileHashes) {
    $d1 = Test-BaselineDrift $PromptTarget $prevState.fileHashes 'prompt'
    if ($d1) {
        [void]$Script:BaselineDrift.Add(($T.PromptName + ': ' + $d1))
        Say 'WARN' ('目标文件在部署后被外部改动（基线漂移：' + $d1 + '）: ' + $PromptTarget + '（本次会重写注入段；标记块以外的内容按现有逻辑保留）')
    }
    if ($Target -eq 'dsh') {
        $d2 = Test-BaselineDrift $PatchFile $prevState.fileHashes 'patch'
        if ($d2) {
            [void]$Script:BaselineDrift.Add(('cordis.patch.yml: ' + $d2))
            Say 'WARN' ('home 级 patch 在部署后被外部改动（基线漂移：' + $d2 + '）: ' + $PatchFile)
        }
    }
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
$Script:OpMode = $SkillMode

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
$promptBeforeHash = ''
$promptAfterHash  = ''
$promptBeforeB64  = ''
$patchBeforeHash  = ''
$patchAfterHash   = ''
$patchBeforeB64   = ''
$currentText   = ''
if ($hadPromptFile) { $currentText = Read-Utf8 $PromptTarget }
# 标记块健康检查。默认只报错不改文件 —— 用户文件里出现重复/颠倒的标记，
# 说明上一次写入被中断或被外部编辑过，静默自愈会把文件改成用户没预期的样子。
if ($hadPromptFile -and $currentText) {
    $markFixed = Assert-MarkerIntegrity $currentText -Repair:$RepairMarker
    if ($RepairMarker -and $markFixed -ne $currentText) {
        $currentText = $markFixed
        Write-Utf8NoBom $PromptTarget $currentText
        Say 'WARN' ('检测到标记块异常，已按 -RepairMarker 修复（只保留最后一对）: ' + $PromptTarget)
    }
}

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
$promptBody  = Read-Own $SourcePrompt
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
# 幂等短路：内容已经就是目标状态就不重写（按字节比较，BOM 与行尾也算在内）。
# 旧写法每次重注入都整文件重写一遍，即使内容一模一样。
$newBytes    = [System.Text.Encoding]::UTF8.GetBytes($newText)
$promptFull  = Assert-Writable $T.AgentDir $PromptTarget
$promptBeforeB64 = ''
if (Test-Path -LiteralPath $promptFull) {
    $pb = [System.IO.File]::ReadAllBytes($promptFull)
    $promptBeforeHash = Get-Sha256 $pb
    $promptBeforeB64 = [Convert]::ToBase64String($pb)
} else { $promptBeforeHash = '' }
if (Test-SameContent $promptFull $newBytes) {
    $promptAfterHash = $promptBeforeHash
    Say 'INFO' ('指令集已是目标内容，未重复写入: ' + $promptFull + '（版本 ' + $VersionLabel + '，模式 ' + $SkillMode + '）')
} else {
    # 从上面算哈希到这里之间可能被别的程序改过（TOCTOU），写之前再验一次
    Assert-Unchanged $promptFull $promptBeforeHash $T.PromptName
    Register-FileRollback $promptFull
    Write-Utf8NoBom $promptFull $newText
    $promptAfterHash = Get-Sha256 $newBytes
    Add-JournalFile $promptFull $promptBeforeB64 ($promptBeforeHash -ne '') ([Convert]::ToBase64String($newBytes)) $promptBeforeHash $promptAfterHash
    Say 'INFO' ('已写入指令集: ' + $promptFull + '（' + $promptBody.Length + ' 字符，版本 ' + $VersionLabel + '，模式 ' + $SkillMode + '）')
}
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
    if (Test-Path -LiteralPath $PatchFile) {
        $pb2 = [System.IO.File]::ReadAllBytes($PatchFile)
        $patchBeforeHash = Get-Sha256 $pb2
        $patchBeforeB64 = [Convert]::ToBase64String($pb2)
    }
    Assert-Unchanged $PatchFile $patchBeforeHash 'cordis.patch.yml'
    Register-FileRollback $PatchFile
    Write-Utf8NoBom $PatchFile $newPatch
    $patchAfterHash = Get-Sha256 ([System.Text.Encoding]::UTF8.GetBytes($newPatch))
    Add-JournalFile $PatchFile $patchBeforeB64 ($patchBeforeHash -ne '') ([Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($newPatch))) $patchBeforeHash $patchAfterHash
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
        if (-not (Test-Path -LiteralPath $SkillsTarget)) {
            New-Item -ItemType Directory -Force -Path $SkillsTarget | Out-Null
            Register-Rollback $SkillsTarget 'delete' '' $false
        }
        foreach ($d in $dirs) {
            $dest = Assert-Writable $SkillsTarget $d.Name
            $destExisted = Test-Path -LiteralPath $dest
            $backedUpNow = $false
            if ($destExisted) {
                $alreadyOurs = $false
                if ($prevState -and $prevState.installedSkills -and ($prevState.installedSkills -contains $d.Name)) { $alreadyOurs = $true }
                if (-not $alreadyOurs) {
                    $bk = Join-Path (Join-Path $BackupDir 'skills') $d.Name
                    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bk) | Out-Null
                    Copy-Tree $dest $bk
                    $overwrittenSkills[$d.Name] = $bk
                    $backedUpNow = $true
                    Say 'WARN' ('同名技能已存在，已备份原目录: ' + $d.Name)
                }
                if ($prevState -and $prevState.overwrittenSkills -and $prevState.overwrittenSkills.$($d.Name)) {
                    $overwrittenSkills[$d.Name] = [string]$prevState.overwrittenSkills.$($d.Name)
                }
            }
            # 回滚登记：
            #  · 本次新建 → 删掉
            #  · 本次把「不是我们的」原有目录备走 → 从刚做的备份还原
            #  · 目标本来就是上次部署的同名技能 → 不登记（重复部署会写回正确内容）
            if (-not $destExisted) {
                Register-Rollback $dest 'delete' '' $false
            } elseif ($backedUpNow) {
                Register-Rollback $dest 'restore' ([string]$overwrittenSkills[$d.Name]) $true
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
        $menuExisted = Test-Path -LiteralPath $menuDest
        if ($menuExisted) {
            $menuWasOurs = ($prevState -and $prevState.installedSkills -and ($prevState.installedSkills -contains $MenuSkillName))
            if (-not $menuWasOurs) {
                $bk = Join-Path (Join-Path $BackupDir 'skills') $MenuSkillName
                New-Item -ItemType Directory -Force -Path (Split-Path -Parent $bk) | Out-Null
                Copy-Tree $menuDest $bk
                $overwrittenSkills[$MenuSkillName] = $bk
                # 回滚时要把这个用户的原始目录还原回去（New-SkillMenu 会重建目录，
                # 所以不能只登记 delete —— 那样等于把用户原有的技能删了）
                Register-Rollback $menuDest 'restore' $bk $true
                Say 'WARN' ('同名技能已存在，已备份原目录: ' + $MenuSkillName)
            }
            Remove-Item -LiteralPath $menuDest -Recurse -Force -ErrorAction SilentlyContinue
        } else {
            Register-Rollback $menuDest 'delete' '' $false
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
# 回滚命令写进证据里：出事了不用猜怎么退回去（哈希表字面量里不放多行 if，先赋值）
$rollbackCmd = 'inject.ps1 -Target ' + $Target + ' -Uninstall'
if ($Script:Journal.Count -gt 0) {
    $rollbackCmd += '｜按版本恢复: inject.ps1 -Target ' + $Target + ' -Restore ' + $Script:VersionId
}
if ($Script:BaselineDrift.Count -gt 0) { $rollbackCmd += '（当前有基线漂移，默认会被拦下）' }
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
    # 本次写入的落地记录：能回答「这次到底写了什么、写前写后长什么样」
    status              = 'OK'
    conflicts           = @($Script:RollbackConflicts)
    # 哪个版本写下去的（对应 backup\<目标>\history\<id>.json，可按版本恢复）
    versionId           = $(if ($Script:Journal.Count -gt 0) { $Script:VersionId } else { $null })
    fileHashes          = [ordered]@{
        prompt = [ordered]@{ path = $PromptTarget; before = $promptBeforeHash; after = $promptAfterHash }
        patch  = [ordered]@{ path = $(if ($patchFileRel) { $PatchFile } else { $null }); before = $patchBeforeHash; after = $patchAfterHash }
    }
    # 证据记录：下次动手（重注入/卸载）前先拿这份哈希对一遍 ——
    # fileHashes 只写不读就是死数据，接上消费方它才能拦住「静默覆盖用户改动」。
    evidence            = [ordered]@{
        object         = $PromptTarget
        action         = 'deploy'
        baselineSha256 = $promptAfterHash
        baselineDrift  = @($Script:BaselineDrift)
        verification   = @(
            ('文件层：本文件写入前后 SHA-256 = ' + $(if ($promptBeforeHash) { $promptBeforeHash.Substring(0, 12) + '… -> ' } else { '(原不存在) -> ' }) + $promptAfterHash.Substring(0, 12) + '…'),
            '标记层：托管标记块恒为 1 对（异常时 -RepairMarker 可修，不静默自愈）',
            '人工层：模型侧未验证，需在新会话里确认客户端已加载（见 modelStatus）'
        )
        rollback       = $rollbackCmd
    }
}
Write-Utf8NoBom $StatePath (($state | ConvertTo-Json -Depth 5) + "`r`n")
$Script:OpSkills = @($installedSkills).Count
# 版本日志：状态清单写成功后才落盘，避免「日志里有一个失败的版本」
$Script:VersionId = Write-Journal 'applied' $(if ($SkillsOnly) { 'skills-only' } else { 'deploy' })
# 状态清单已落盘，本次部署算完成 —— 清掉回滚日志，
# 否则后续任何无关错误都会把已经记录在案的部署撤销掉。
$Script:Rollback.Clear()
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
