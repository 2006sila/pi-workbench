[CmdletBinding()]
param(
    [string]$SourcePrompt,
    [string]$SkillsSource,
    [string]$AgentDir,
    [switch]$Uninstall,
    [switch]$Check,
    [switch]$NoSkills,
    [switch]$SkillsOnly,
    [string]$RemoveAddons
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

& $core @opts
exit $LASTEXITCODE
