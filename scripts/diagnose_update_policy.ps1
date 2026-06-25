# VoiceInput 更新策略诊断 — 定位 WinError 786 / 企业策略拦截原因
# 用法: 双击 diagnose_update_policy.bat，或在 PowerShell 中:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\diagnose_update_policy.ps1

$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::UTF8

$LogRoot = Join-Path $env:USERPROFILE '.voiceinput\logs'
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$LogPath = Join-Path $LogRoot ('update_policy_diagnose_{0:yyyy-MM-dd_HH-mm-ss}.log' -f (Get-Date))

$AppDirCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\VoiceInput'),
    (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
) | Select-Object -Unique

$AppDir = ($AppDirCandidates | Where-Object { Test-Path (Join-Path $_ 'VoiceInput.exe') } | Select-Object -First 1)
if (-not $AppDir) {
    $AppDir = $AppDirCandidates[0]
}

$TempDir = [IO.Path]::GetTempPath().TrimEnd('\')
$StagingDir = Join-Path $TempDir 'VoiceInput_update_staging'
$ReportLines = [System.Collections.Generic.List[string]]::new()

function Add-Line([string]$Text) {
    $ReportLines.Add($Text)
    Write-Host $Text
}

function Format-Argument([string]$Arg) {
    if ($Arg -match '[\s"]') {
        return '"' + ($Arg -replace '"', '""') + '"'
    }
    return $Arg
}

function Test-Launch {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments = @(),
        [switch]$AcceptAnyExitCode
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    if ($Arguments.Count -gt 0) {
        $psi.Arguments = ($Arguments | ForEach-Object { Format-Argument $_ }) -join ' '
    }
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    try {
        $proc = [System.Diagnostics.Process]::Start($psi)
        $stdout = $proc.StandardOutput.ReadToEnd()
        $stderr = $proc.StandardError.ReadToEnd()
        [void]$proc.WaitForExit(15000)
        $timedOut = -not $proc.HasExited
        if ($timedOut) {
            try { $proc.Kill() } catch {}
            return [pscustomobject]@{
                Name = $Name; Ok = $false; ExitCode = $null
                Detail = 'timeout (>15s)'; Stdout = $stdout; Stderr = $stderr
            }
        }
        $ok = $AcceptAnyExitCode.IsPresent -or ($proc.ExitCode -eq 0)
        $detail = if ($ok) {
            if ($AcceptAnyExitCode.IsPresent) { "launched ok exit=$($proc.ExitCode)" } else { 'ok exit=0' }
        } else {
            "exit=$($proc.ExitCode)"
        }
        if ($stderr.Trim()) { $detail += " stderr=$($stderr.Trim())" }
        return [pscustomobject]@{
            Name = $Name; Ok = $ok; ExitCode = $proc.ExitCode
            Detail = $detail; Stdout = $stdout.Trim(); Stderr = $stderr.Trim()
        }
    } catch {
        $msg = $_.Exception.Message
        if ($_.Exception -is [System.ComponentModel.Win32Exception]) {
            $win = $_.Exception
            $msg = "[WinError $($win.NativeErrorCode)] $($win.Message)"
        }
        return [pscustomobject]@{
            Name = $Name; Ok = $false; ExitCode = $null
            Detail = $msg; Stdout = ''; Stderr = ''
        }
    }
}

function Write-ProbeScript {
    param([string]$Path, [ValidateSet('ps1', 'bat')] [string]$Kind)
    if ($Kind -eq 'ps1') {
        Set-Content -Path $Path -Encoding UTF8 -Value @(
            'Write-Output "PROBE_OK_PS1"'
            'exit 0'
        )
    } else {
        Set-Content -Path $Path -Encoding ASCII -Value @(
            '@echo off'
            'echo PROBE_OK_BAT'
            'exit /b 0'
        )
    }
}

function Get-RecentAppLockerBlocks {
    $logName = 'Microsoft-Windows-AppLocker/MSI and Script'
    if (-not (Get-WinEvent -ListLog $logName -ErrorAction SilentlyContinue)) {
        return @('  (AppLocker Script 事件日志不存在 — 可能未启用 AppLocker Script 规则)')
    }
    try {
        $events = Get-WinEvent -FilterHashtable @{
            LogName = $logName
            Id = 8007
            StartTime = (Get-Date).AddHours(-24)
        } -MaxEvents 15 -ErrorAction Stop
        if (-not $events) {
            return @('  (近 24 小时无 AppLocker 8007 拦截记录)')
        }
        return $events | ForEach-Object {
            $xml = [xml]$_.ToXml()
            $data = @{}
            foreach ($node in $xml.Event.EventData.Data) {
                if ($node.Name) { $data[$node.Name] = $node.'#text' }
            }
            $target = $data.TargetUser
            $file = $data.FilePath
            if (-not $file) { $file = $_.Message }
            "  $($_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss')) | user=$target | $file"
        }
    } catch {
        return @("  (读取 AppLocker 日志失败: $($_.Exception.Message))")
    }
}

function Get-SrpHints {
    $lines = [System.Collections.Generic.List[string]]::new()
    $srpPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Safer\CodeIdentifiers'
    if (Test-Path $srpPath) {
        $lines.Add('  Software Restriction Policies 注册表项存在')
        $levels = Get-ChildItem -Path $srpPath -ErrorAction SilentlyContinue
        foreach ($level in $levels) {
            if ($level.PSChildName -match '^\d+$') {
                $lines.Add("    Level $($level.PSChildName): $($level.GetValue('Description','(no desc)'))")
            }
        }
        $rules = Get-ChildItem -Path $srpPath -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.GetValue('ItemData') -match 'temp|Temp|TEMP|powershell|PowerShell|\.ps1|\.bat|\.cmd' }
        foreach ($rule in $rules) {
            $item = $rule.GetValue('ItemData', '')
            $desc = $rule.GetValue('Description', '')
            $lines.Add("    相关规则: ItemData=$item Description=$desc")
        }
        if ($rules.Count -eq 0) {
            $lines.Add('    (未发现名称含 temp/powershell/ps1/bat 的 SRP 规则项 — 规则可能在 GPO 其他路径)')
        }
    } else {
        $lines.Add('  未发现 SRP 注册表项 (HKLM\...\Safer\CodeIdentifiers)')
    }
    return $lines
}

# ── 报告头 ──
Add-Line '============================================================'
Add-Line ' VoiceInput 更新策略诊断'
Add-Line ' 用于定位: WinError 786 / 管理员策略拦截更新脚本'
Add-Line '============================================================'
Add-Line ''
Add-Line "[环境]"
Add-Line "  时间:       $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Line "  用户:       $env:USERDOMAIN\$env:USERNAME"
Add-Line "  计算机:     $env:COMPUTERNAME"
Add-Line "  OS:         $((Get-CimInstance Win32_OperatingSystem).Caption) $( (Get-CimInstance Win32_OperatingSystem).Version )"
Add-Line "  架构:       $env:PROCESSOR_ARCHITECTURE"
Add-Line "  Temp:       $TempDir"
Add-Line "  应用目录:   $AppDir $(if (Test-Path (Join-Path $AppDir 'VoiceInput.exe')) { '(已找到 VoiceInput.exe)' } else { '(未找到 VoiceInput.exe)' })"
Add-Line "  Staging:    $StagingDir $(if (Test-Path $StagingDir) { '(存在)' } else { '(不存在)' })"
Add-Line "  报告文件:   $LogPath"
Add-Line ''

# ── 写入探测脚本 ──
$probeTempPs1 = Join-Path $TempDir 'voiceinput_diag_probe.ps1'
$probeTempBat = Join-Path $TempDir 'voiceinput_diag_probe.bat'
$probeAppPs1 = Join-Path $AppDir 'voiceinput_diag_probe.ps1'
$probeAppBat = Join-Path $AppDir 'voiceinput_diag_probe.bat'
$probeUpdaterPs1 = Join-Path $TempDir 'voiceinput_update.ps1'

Write-ProbeScript -Path $probeTempPs1 -Kind 'ps1'
Write-ProbeScript -Path $probeTempBat -Kind 'bat'
if (Test-Path $AppDir) {
    Write-ProbeScript -Path $probeAppPs1 -Kind 'ps1'
    Write-ProbeScript -Path $probeAppBat -Kind 'bat'
}
Set-Content -Path $probeUpdaterPs1 -Encoding UTF8 -Value @(
    '# probe: same path/filename as updater install script'
    'Write-Output "PROBE_OK_UPDATER_PS1"'
    'exit 0'
)

# ── [1] updater 启动方式对比：当前 vs 建议修复 ──
Add-Line '[1] Updater 启动方式对比 (core/updater.py install)'
Add-Line '  A = 当前代码: -WindowStyle Hidden -ExecutionPolicy Bypass -File voiceinput_update.ps1'
Add-Line '  H = 建议修复: -NoProfile -ExecutionPolicy Bypass -File (无 Hidden, Python CREATE_NO_WINDOW)'
Add-Line ''
$updaterTests = [System.Collections.Generic.List[object]]::new()
[void]$updaterTests.Add(@{
    Name = 'A. CURRENT updater launch (Hidden + File)'
    File = 'powershell'
    Args = @('-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', $probeUpdaterPs1)
    AcceptAnyExit = $false
})
[void]$updaterTests.Add(@{
    Name = 'H. PROPOSED fix (NoProfile + File, no Hidden)'
    File = 'powershell'
    Args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $probeUpdaterPs1)
    AcceptAnyExit = $false
})

$updaterResults = @(foreach ($t in $updaterTests) {
    Test-Launch -Name $t.Name -FilePath $t.File -Arguments $t.Args
})

foreach ($r in $updaterResults) {
    $mark = if ($r.Ok) { 'PASS' } else { 'FAIL' }
    Add-Line ("  [{0}] {1}" -f $mark, $r.Name)
    Add-Line ("        {0}" -f $r.Detail)
    if ($r.Stdout) { Add-Line ("        stdout: {0}" -f $r.Stdout) }
}

Add-Line ''
if ($a -and $a.Ok -and $h -and $h.Ok) {
    Add-Line '  >> 结论: 两种启动方式均可 — 当前环境不受 Hidden 策略影响'
} elseif ($a -and -not $a.Ok -and $h -and $h.Ok) {
    Add-Line '  >> 结论: 去掉 -WindowStyle Hidden 并改用 -NoProfile 应可修复 updater 安装失败'
} elseif ($a -and -not $a.Ok -and $h -and -not $h.Ok) {
    Add-Line '  >> 结论: 建议修复仍失败 — 需 bat/robocopy 回退或手动更新'
}
Add-Line ''

# ── [2] 其他环境探测 ──
Add-Line '[2] 其他环境探测'
$tests = [System.Collections.Generic.List[object]]::new()
[void]$tests.Add(@{
    Name = 'B. powershell -File (Temp probe)'
    File = 'powershell'
    Args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $probeTempPs1)
    AcceptAnyExit = $false
})
[void]$tests.Add(@{
    Name = 'C. powershell -Command (inline, no script file)'
    File = 'powershell'
    Args = @('-NoProfile', '-Command', 'Write-Output PROBE_OK_INLINE')
    AcceptAnyExit = $false
})
[void]$tests.Add(@{
    Name = 'D. cmd /c (Temp bat)'
    File = 'cmd.exe'
    Args = @('/c', $probeTempBat)
    AcceptAnyExit = $false
})
[void]$tests.Add(@{
    Name = 'E. robocopy /? (system binary)'
    File = 'robocopy.exe'
    Args = @('/?')
    AcceptAnyExit = $true
})

if (Test-Path $AppDir) {
    [void]$tests.Add(@{
        Name = 'F. powershell -File (app install dir)'
        File = 'powershell'
        Args = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $probeAppPs1)
        AcceptAnyExit = $false
    })
    [void]$tests.Add(@{
        Name = 'G. cmd /c (app install dir bat)'
        File = 'cmd.exe'
        Args = @('/c', $probeAppBat)
        AcceptAnyExit = $false
    })
}

$results = @(foreach ($t in $tests) {
    if ($t.AcceptAnyExit) {
        Test-Launch -Name $t.Name -FilePath $t.File -Arguments $t.Args -AcceptAnyExitCode
    } else {
        Test-Launch -Name $t.Name -FilePath $t.File -Arguments $t.Args
    }
})

foreach ($r in $results) {
    $mark = if ($r.Ok) { 'PASS' } else { 'FAIL' }
    Add-Line ("  [{0}] {1}" -f $mark, $r.Name)
    Add-Line ("        {0}" -f $r.Detail)
    if ($r.Stdout) { Add-Line ("        stdout: {0}" -f $r.Stdout) }
}

Add-Line ''

# ── 策略线索 ──
Add-Line '[3] AppLocker 近 24h 脚本拦截 (事件 ID 8007)'
Get-RecentAppLockerBlocks | ForEach-Object { Add-Line $_ }
Add-Line ''

Add-Line '[4] Software Restriction Policies (SRP) 线索'
Get-SrpHints | ForEach-Object { Add-Line $_ }
Add-Line ''

Add-Line '[5] PowerShell 语言模式'
try {
    $lang = $ExecutionContext.SessionState.LanguageMode
    Add-Line "  当前会话 LanguageMode: $lang"
    if ($lang -eq 'ConstrainedLanguage') {
        Add-Line '  → 处于 ConstrainedLanguage，常见于 AppLocker/WDAC 启用时'
    }
} catch {
    Add-Line "  无法读取: $($_.Exception.Message)"
}
Add-Line ''

# ── 自动结论 ──
Add-Line '[6] 综合结论'
$a = $updaterResults | Where-Object { $_.Name -like 'A.*' } | Select-Object -First 1
$h = $updaterResults | Where-Object { $_.Name -like 'H.*' } | Select-Object -First 1
$b = $results | Where-Object { $_.Name -like 'B.*' } | Select-Object -First 1
$c = $results | Where-Object { $_.Name -like 'C.*' } | Select-Object -First 1
$d = $results | Where-Object { $_.Name -like 'D.*' } | Select-Object -First 1
$f = $results | Where-Object { $_.Name -like 'F.*' } | Select-Object -First 1
$g = $results | Where-Object { $_.Name -like 'G.*' } | Select-Object -First 1
$rob = $results | Where-Object { $_.Name -like 'E.*' } | Select-Object -First 1

if ($a -and -not $a.Ok -and $h -and $h.Ok) {
    Add-Line '  ● 根因确认: -WindowStyle Hidden 被企业策略拦截；建议修复 (H) 可用'
    Add-Line '    代码改动: updater.py 去掉 Hidden，改为 -NoProfile (保留 CREATE_NO_WINDOW)'
} elseif ($a -and -not $a.Ok -and $a.Detail -match '786|restricted by your Administrator|restricted by policy') {
    Add-Line '  ● 复现 updater 失败: 策略拦截 (WinError 786 或同类)'
    if ($h -and -not $h.Ok) {
        Add-Line '  ● 建议修复 (H) 也失败 — 需 bat+robocopy 回退或手动更新'
    }
}
if ($c -and -not $c.Ok) {
    Add-Line '  ● PowerShell 本身无法启动 — 问题不仅是脚本路径，需 IT 放行 powershell.exe'
} elseif ($c -and $c.Ok -and $b -and -not $b.Ok -and $d -and -not $d.Ok) {
    Add-Line '  ● PowerShell 可启动，但 Temp 下 .ps1 和 .bat 均失败 — 很可能是「禁止 Temp 执行脚本」'
    Add-Line '    建议: 更新脚本改到应用安装目录，或用户手动更新 / 使用 setup.exe'
} elseif ($c -and $c.Ok -and $b -and -not $b.Ok -and $d -and $d.Ok) {
    Add-Line '  ● 仅 PowerShell/Temp .ps1 失败，cmd+bat 可用 — 可改用 bat+robocopy 作为回退'
} elseif ($f -and $f.Ok -and $b -and -not $b.Ok) {
    Add-Line '  ● 应用目录可跑 PowerShell 脚本，Temp 不行 — 把更新脚本写到安装目录即可'
} elseif ($g -and $g.Ok -and $d -and -not $d.Ok) {
    Add-Line '  ● 应用目录 bat 可用、Temp bat 不可用 — bat 回退需放在安装目录'
}
if ($rob -and $rob.Ok) {
    Add-Line '  ● robocopy 可用 — 文件复制步骤本身不受阻，瓶颈在「启动更新 helper」'
}
if (-not ($results | Where-Object { -not $_.Ok })) {
    Add-Line '  ● 所有探测均通过 — 当前环境应可正常更新；若仍失败请对比 updater 日志中的路径/PID'
}

Add-Line ''
Add-Line '[7] 清理'
foreach ($p in @($probeTempPs1, $probeTempBat, $probeAppPs1, $probeAppBat)) {
    if ($p -and (Test-Path $p)) {
        Remove-Item $p -Force -ErrorAction SilentlyContinue
        Add-Line "  已删除: $p"
    }
}
Add-Line ''
Add-Line '诊断完成。请将此报告文件发给开发者或在 issue 中附上。'
Add-Line "报告路径: $LogPath"

$ReportLines | Set-Content -Path $LogPath -Encoding UTF8
Write-Host ''
Write-Host "完整报告已保存: $LogPath" -ForegroundColor Green
