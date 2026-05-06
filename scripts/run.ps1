#Requires -Version 5.1
# encoding: utf-8 (save this file with UTF-8 BOM)
#
# ============================================================
#  VoiceInput 管理脚本 (PowerShell)
#  用法:
#    .\run.ps1                # 交互式菜单（单次执行后退出）
#    .\run.ps1 start          # 直接执行命令
#    .\run.ps1 -start         # 直接执行命令
#    .\run.ps1 --start        # 直接执行命令
#    .\run.ps1 --help         # 查看帮助
#
#  参数: start|install|build [type]|clean
#        logs|publish|rollback|help
#        同时兼容 -start / --start 这类写法
# ============================================================

param(
    [switch]$start,
    [switch]$install,
    [switch]$build,
    [switch]$clean,
    [switch]$logs,
    [switch]$publish,
    [switch]$rollback,
    [switch]$help,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"

# —— 编码（解决 Windows 中文乱码） ——

[Console]::InputEncoding  = [System.Text.Encoding]::UTF8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# —— 配置区 ——

$ScriptDir  = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$Python     = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$ReqFile    = Join-Path $ProjectDir "src\requirements.txt"
$LogDir     = Join-Path $env:USERPROFILE ".voiceinput\logs"
$ReleaseSrc = Join-Path $ProjectDir "_release\_发布和运营指南\相关资源"

# —— 日志函数 ——

function Log-Info  { param([string]$Msg) Write-Host "[INFO]  $Msg" -ForegroundColor Blue }
function Log-Ok    { param([string]$Msg) Write-Host "[ OK ]  $Msg" -ForegroundColor Green }
function Log-Warn  { param([string]$Msg) Write-Host "[WARN]  $Msg" -ForegroundColor Yellow }
function Log-Error { param([string]$Msg) Write-Host "[ERROR] $Msg" -ForegroundColor Red }
function Log-Step  { param([string]$Msg) Write-Host "  ->  $Msg" -ForegroundColor Cyan }
function Log-Cmd   { param([string]$Msg) Write-Host "  `$ $Msg" -ForegroundColor DarkGray }

function Show-Divider {
    Write-Host ("-" * 55) -ForegroundColor DarkGray
}

# —— 辅助函数 ——

function Assert-Venv {
    if (-not (Test-Path $Python)) {
        Log-Error "未找到 .venv，请先运行: .\run.ps1 --install"
        exit 1
    }
}

# —— 核心操作 ——

function Invoke-Start {
    Assert-Venv
    Log-Info "启动 VoiceInput..."
    Log-Cmd "$Python -u src\main.py"
    Write-Host ""
    Push-Location $ProjectDir
    try { & $Python -u src\main.py }
    finally { Pop-Location }
}

function Invoke-Install {
    Log-Info "安装依赖..."
    Push-Location $ProjectDir
    try {
        $hasUv = Get-Command uv -ErrorAction SilentlyContinue
        if ($hasUv) {
            if (-not (Test-Path ".venv\Scripts\activate.bat")) {
                Log-Cmd "uv venv"
                uv venv
            }
            Log-Cmd "uv pip install -r src\requirements.txt"
            uv pip install -r src\requirements.txt
        } else {
            if (-not (Test-Path ".venv\Scripts\activate.bat")) {
                Log-Cmd "python -m venv .venv"
                python -m venv .venv
            }
            Log-Cmd "$Python -m pip install -r src\requirements.txt"
            & $Python -m pip install -r src\requirements.txt
        }
        Write-Host ""
        Log-Ok "依赖安装完成"
    }
    finally { Pop-Location }
}

function Invoke-Build {
    Assert-Venv

    $buildType = ""
    if ($ExtraArgs.Count -gt 0) {
        $buildType = $ExtraArgs[0]
    } elseif ($script:BuildArg) {
        $buildType = $script:BuildArg
    }

    $validTypes = @("portable", "onefile", "installer", "all", "clean")

    if (-not $buildType) {
        Write-Host ""
        Write-Host "  构建选项" -ForegroundColor White
        Write-Host ""
        Write-Host "    1) 嵌入式 Python 便携包    " -NoNewline; Write-Host "portable" -ForegroundColor DarkGray
        Write-Host "    2) PyInstaller 单文件 exe   " -NoNewline; Write-Host "onefile" -ForegroundColor DarkGray
        Write-Host "    3) Inno Setup 安装包        " -NoNewline; Write-Host "installer" -ForegroundColor DarkGray
        Write-Host "    4) 全部构建                 " -NoNewline; Write-Host "all" -ForegroundColor DarkGray
        Write-Host "    5) 清理 dist/ build/        " -NoNewline; Write-Host "clean" -ForegroundColor Red
        Write-Host ""
        $choice = Read-Host "  请选择 [1-5]"
        switch ($choice) {
            "1" { $buildType = "portable" }
            "2" { $buildType = "onefile" }
            "3" { $buildType = "installer" }
            "4" { $buildType = "all" }
            "5" { $buildType = "clean" }
            default { Log-Error "无效选项: $choice"; exit 1 }
        }
    }

    if ($buildType -notin $validTypes) {
        Log-Error "无效构建类型: $buildType (可选: $($validTypes -join ', '))"
        exit 1
    }

    Write-Host ""
    Log-Info "构建: --$buildType"
    Log-Cmd "$Python scripts\build.py --$buildType"
    Write-Host ""
    Push-Location $ProjectDir
    try { & $Python scripts\build.py "--$buildType" }
    finally { Pop-Location }
}

function Invoke-Clean {
    Assert-Venv
    Log-Info "清理构建产物..."
    Log-Cmd "$Python scripts\clean_build.py --confirm"
    Write-Host ""
    Push-Location $ProjectDir
    try { & $Python scripts\clean_build.py --confirm }
    finally { Pop-Location }
}

function Invoke-Logs {
    if (-not (Test-Path $LogDir)) {
        Log-Error "日志目录不存在: $LogDir"
        return
    }

    $latest = Get-ChildItem $LogDir -Filter "*.log" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) {
        Log-Warn "暂无日志文件"
        return
    }

    Log-Info "最新日志: $($latest.Name)"
    Show-Divider
    Get-Content $latest.FullName -Tail 50 -Wait
}

function Invoke-Publish {
    Push-Location $ProjectDir
    try {
        Log-Info "发布文件到项目根目录..."
        $readmeSrc = Join-Path $ReleaseSrc "README.md"
        if (-not (Test-Path $readmeSrc)) {
            Log-Error "未找到: $readmeSrc"
            return
        }
        Copy-Item $readmeSrc "README.md" -Force
        Log-Step "README.md"

        $licenseSrc = Join-Path $ReleaseSrc "LICENSE"
        if (Test-Path $licenseSrc) {
            Copy-Item $licenseSrc "LICENSE" -Force
            Log-Step "LICENSE"
        }

        if (-not (Test-Path "docs")) { New-Item -ItemType Directory -Path "docs" -Force | Out-Null }
        $gifs = Join-Path $ReleaseSrc "docs\*.gif"
        if (Test-Path $gifs) {
            Copy-Item $gifs "docs\" -Force
            Log-Step "docs\*.gif"
        }

        Write-Host ""
        Log-Ok "已发布"
    }
    finally { Pop-Location }
}

function Invoke-Rollback {
    Push-Location $ProjectDir
    try {
        Log-Info "回滚发布文件..."
        if (Test-Path "README.md")  { Remove-Item "README.md" -Force;          Log-Step "已删除 README.md" }
        if (Test-Path "LICENSE")    { Remove-Item "LICENSE" -Force;             Log-Step "已删除 LICENSE" }
        if (Test-Path "docs")      { Remove-Item "docs" -Recurse -Force;       Log-Step "已删除 docs\" }
        Write-Host ""
        Log-Ok "已回滚"
    }
    finally { Pop-Location }
}

# —— 交互式菜单（单次执行后退出） ——

function Show-Menu {
    Write-Host ""
    Write-Host "  +========================================+" -ForegroundColor White
    Write-Host "  |       VoiceInput 管理脚本              |" -ForegroundColor White
    Write-Host "  +========================================+" -ForegroundColor White
    Write-Host ""
    Write-Host "    1) 启动应用      " -NoNewline; Write-Host "--start" -ForegroundColor DarkGray
    Write-Host "    2) 安装依赖      " -NoNewline; Write-Host "--install" -ForegroundColor DarkGray
    Write-Host "    3) 构建项目      " -NoNewline; Write-Host "--build" -ForegroundColor DarkGray
    Write-Host "    4) 清理构建      " -NoNewline; Write-Host "--clean" -ForegroundColor DarkGray
    Write-Host "    5) 查看日志      " -NoNewline; Write-Host "--logs" -ForegroundColor DarkGray
    Write-Host "    6) 发布文件      " -NoNewline; Write-Host "--publish" -ForegroundColor DarkGray
    Write-Host "    7) 回滚发布      " -NoNewline; Write-Host "--rollback" -ForegroundColor DarkGray
    Write-Host ""
}

function Invoke-Interactive {
    Show-Menu
    $choice = Read-Host "  请选择 [1-7]"
    Write-Host ""
    switch ($choice) {
        "1" { Invoke-Start }
        "2" { Invoke-Install }
        "3" { Invoke-Build }
        "4" { Invoke-Clean }
        "5" { Invoke-Logs }
        "6" { Invoke-Publish }
        "7" { Invoke-Rollback }
        default { Log-Error "无效选项: $choice"; exit 1 }
    }
}

# —— 帮助信息 ——

function Show-Usage {
    Write-Host ""
    Write-Host "  VoiceInput 管理脚本" -ForegroundColor White
    Write-Host ""
    Write-Host "  用法:  .\run.ps1 [参数]"
    Write-Host ""
    Write-Host "  参数:"
    Write-Host "    start         启动应用"
    Write-Host "    install       安装/更新依赖"
    Write-Host "    build         构建项目 (可选: portable|onefile|installer|all|clean)"
    Write-Host "    clean         清理构建产物 (dist/, build/, *.spec)"
    Write-Host "    logs          查看最新日志 (tail -f)"
    Write-Host "    publish       将发布文件复制到项目根目录"
    Write-Host "    rollback      回滚发布文件"
    Write-Host "    help          显示此帮助信息"
    Write-Host ""
    Write-Host "  也兼容: -start, --start, -build, --build 等写法"
    Write-Host ""
    Write-Host "  不带参数则进入交互式菜单。"
    Write-Host ""
}

# —— 参数兼容层 ——

function Normalize-Token {
    param([string]$Token)
    if (-not $Token) { return "" }
    return $Token.Trim().TrimStart('-').ToLowerInvariant()
}

$script:BuildArg = ""

if (-not $start -and -not $install -and -not $build -and -not $clean -and -not $logs -and -not $publish -and -not $rollback -and -not $help) {
    if ($ExtraArgs.Count -gt 0) {
        $action = Normalize-Token $ExtraArgs[0]
        $remaining = @()
        if ($ExtraArgs.Count -gt 1) {
            $remaining = $ExtraArgs[1..($ExtraArgs.Count - 1)]
        }

        switch ($action) {
            "start"    { $start = $true; $ExtraArgs = $remaining }
            "install"  { $install = $true; $ExtraArgs = $remaining }
            "build"    { $build = $true; $ExtraArgs = $remaining }
            "clean"    { $clean = $true; $ExtraArgs = $remaining }
            "logs"     { $logs = $true; $ExtraArgs = $remaining }
            "publish"  { $publish = $true; $ExtraArgs = $remaining }
            "rollback" { $rollback = $true; $ExtraArgs = $remaining }
            "help"     { $help = $true; $ExtraArgs = $remaining }
        }
    }
}

if ($build -and $ExtraArgs.Count -gt 0) {
    $script:BuildArg = $ExtraArgs[0]
}

$hasParam = $start -or $install -or $build -or $clean -or $logs -or $publish -or $rollback -or $help

if (-not $hasParam) {
    Invoke-Interactive
    exit 0
}

if ($help)     { Show-Usage;      exit 0 }
if ($start)    { Invoke-Start;    exit 0 }
if ($install)  { Invoke-Install;  exit 0 }
if ($build)    { Invoke-Build;    exit 0 }
if ($clean)    { Invoke-Clean;    exit 0 }
if ($logs)     { Invoke-Logs;     exit 0 }
if ($publish)  { Invoke-Publish;  exit 0 }
if ($rollback) { Invoke-Rollback; exit 0 }
