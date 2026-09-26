[CmdletBinding()]
param(
    [string]$SourcePrompt,
    [string]$SkillsSource,
    [string]$AgentDir,
    [switch]$Uninstall,
    [switch]$Check,
    [switch]$NoSkills,
    [switch]$SkillsOnly,
    [string]$RemoveAddons,
    # 以下两个开关以前没转发，从包装脚本调用时会被默默丢掉（改用 inject.ps1 直调）
    [switch]$Force,
    [switch]$RepairMarker
)

$core = Join-Path $PSScriptRoot 'inject.ps1'
if (-not (Test-Path -LiteralPath $core)) { Write-Host ('找不到 inject.ps1: ' + $core); exit 1 }

# 用哈希表 splat。数组 splat（$argv = @('-Target','pideck'); & $core @argv）在
# PowerShell 里只按位置绑定，命名参数绑不上，会报 Target 的 ValidateSet 失败。
$opts = @{ Target = 'dsh' }
if ($SourcePrompt) { $opts.SourcePrompt = $SourcePrompt }
if ($SkillsSource) { $opts.SkillsSource = $SkillsSource }
if ($AgentDir)     { $opts.AgentDir = $AgentDir }
if ($RemoveAddons) { $opts.RemoveAddons = $RemoveAddons }
if ($Uninstall)    { $opts.Uninstall = $true }
if ($Check)        { $opts.Check = $true }
if ($NoSkills)     { $opts.NoSkills = $true }
if ($SkillsOnly)   { $opts.SkillsOnly = $true }
if ($Force)        { $opts.Force = $true }
if ($RepairMarker) { $opts.RepairMarker = $true }

& $core @opts
exit $LASTEXITCODE
