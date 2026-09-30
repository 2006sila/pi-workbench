# ============================================================
#  自我部署：把构建好的 exe 装进分发目录
# ------------------------------------------------------------
#  流程：停进程 → 备份旧 exe（.rollback-<时间戳>）→ 覆盖 → 校验 → 重启
#  回滚：把 <分发目录>\<exe>.rollback-* 复制回去（脚本会打印确切命令）
#
#  用法：
#    powershell -ExecutionPolicy Bypass -File deploy-self.ps1 -WhatIfOnly   # 只打印计划，不动手
#    powershell -ExecutionPolicy Bypass -File deploy-self.ps1               # 实际部署
#    powershell -ExecutionPolicy Bypass -File deploy-self.ps1 -Pkg 'D:\Agent\workplace'
#
#  ══ 为什么默认路径按脚本位置推导 ══════════════════════════════════
#  写死盘符（F:\... 之类）换台机器就抛异常，这类本机路径也不该进公开仓库。
#  这里以脚本自身所在目录为基准：仓库根 = 脚本所在目录，分发目录默认取仓库的上一级
#  （用户通常把 exe 放在仓库旁边），构建产物默认取 dist\ 下最新的 pi-workbench-v*.exe。
# ============================================================
[CmdletBinding()]
param(
    # 只打印计划，不做任何写操作（停进程 / 备份 / 覆盖 / 重启 全跳过）
    [switch]$WhatIfOnly,

    # 分发目录：存放 exe 的地方（默认 = 仓库的上一级目录）
    [string]$Pkg,

    # 构建产物：要装进去的 exe（默认 = dist\ 下最新的 pi-workbench-v*.exe）
    [string]$Built,

    # 目标 exe 文件名（分发目录里叫这个名字）
    [string]$ExeName = 'pi-workbench-v1.4.exe',

    # 保留最近几份回滚备份（其余自动清理，和备份保留策略同一个口径）
    [int]$KeepRollbacks = 3,

    # 部署完不自动启动（脚本化更新 / 自动化测试用；默认会拉起新 exe）
    [switch]$NoStart
)

Set-StrictMode -Off
$ErrorActionPreference = 'Stop'

function Say-Plan([string]$Text) { Write-Host $Text }

$repoRoot = $PSScriptRoot
if (-not $Pkg) { $Pkg = Split-Path -Parent $repoRoot }
if (-not $Built) {
    $distDir = Join-Path $repoRoot 'dist'
    $cand = @()
    if (Test-Path -LiteralPath $distDir) {
        $cand = @(Get-ChildItem -LiteralPath $distDir -Filter 'pi-workbench-v*.exe' -File -ErrorAction SilentlyContinue |
                  Sort-Object LastWriteTime -Descending)
    }
    if ($cand.Count -gt 0) { $Built = $cand[0].FullName }
    else { $Built = Join-Path $distDir 'pi-workbench-v1.4.exe' }
}

$dst = Join-Path $Pkg $ExeName

if (-not (Test-Path -LiteralPath $Built -PathType Leaf)) {
    throw ("找不到构建产物：$Built" + "`n" + '先构建： py -m PyInstaller --clean --noconfirm bj_tool.spec')
}
if (-not (Test-Path -LiteralPath $Pkg -PathType Container)) {
    throw ("分发目录不存在：$Pkg" + "`n" + '用 -Pkg 指定一个目录。')
}
if ([System.IO.Path]::GetFullPath($Built) -eq [System.IO.Path]::GetFullPath($dst)) {
    throw ('构建产物与目标是同一个文件，无需部署：' + $dst)
}

$procName = [System.IO.Path]::GetFileNameWithoutExtension($ExeName)
$running = @(Get-Process -Name $procName -ErrorAction SilentlyContinue)
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$bak = "$dst.rollback-$stamp"

Say-Plan ('仓库根    : ' + $repoRoot)
Say-Plan ('构建产物  : ' + $Built)
Say-Plan ('分发目录  : ' + $Pkg)
Say-Plan ('目标 exe  : ' + $dst)
Say-Plan ('回滚备份  : ' + (Split-Path -Leaf $bak))
Say-Plan ''

# 1) 停进程（正在运行的 exe 占着文件，覆盖会失败）
if ($running.Count -gt 0) {
    Say-Plan ('[1/5] 停止运行中的进程：' + (($running | ForEach-Object { $_.Id }) -join ', '))
    if (-not $WhatIfOnly) {
        $running | Stop-Process -Force -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 1200
    }
} else {
    Say-Plan '[1/5] 没有运行中的进程'
}

# 2) 备份现有 exe
Say-Plan ('[2/5] 备份现有 exe → ' + (Split-Path -Leaf $bak))
if (-not $WhatIfOnly) {
    if (Test-Path -LiteralPath $dst -PathType Leaf) {
        Copy-Item -LiteralPath $dst -Destination $bak -Force
    } else {
        Say-Plan '      目标尚不存在，跳过备份'
    }
}

# 3) 覆盖
Say-Plan ('[3/5] 部署：' + (Split-Path -Leaf $Built) + ' → ' + (Split-Path -Leaf $dst))
if (-not $WhatIfOnly) {
    Copy-Item -LiteralPath $Built -Destination $dst -Force
    $new = Get-Item -LiteralPath $dst
    $sha = (Get-FileHash -LiteralPath $dst -Algorithm SHA256).Hash.ToLowerInvariant()
    Say-Plan ('      ' + $new.Length + ' 字节，mtime ' + $new.LastWriteTime)
    Say-Plan ('      SHA256 ' + $sha)
}

# 4) 清理旧的回滚备份（保留最近 $KeepRollbacks 份）
Say-Plan ('[4/5] 保留最近 ' + $KeepRollbacks + ' 份回滚备份')
if (-not $WhatIfOnly) {
    $olds = @(Get-ChildItem -LiteralPath $Pkg -Filter ((Split-Path -Leaf $dst) + '.rollback-*') -File -ErrorAction SilentlyContinue |
              Sort-Object Name -Descending)
    foreach ($f in @($olds | Select-Object -Skip $KeepRollbacks)) {
        Remove-Item -LiteralPath $f.FullName -Force -ErrorAction SilentlyContinue
        Say-Plan ('      已清理：' + $f.Name)
    }
}

# 5) 重启
Say-Plan '[5/5] 启动'
if ($WhatIfOnly) {
    Say-Plan '      (WhatIfOnly：未执行任何写操作)'
} elseif ($NoStart) {
    Say-Plan '      (NoStart：未启动，需要时手动运行目标 exe)'
} else {
    Start-Process -FilePath $dst -WorkingDirectory $Pkg
    Say-Plan '      已启动'
}

Say-Plan ''
Say-Plan ('回滚命令：Copy-Item -LiteralPath ''' + $bak + ''' -Destination ''' + $dst + ''' -Force')
exit 0
