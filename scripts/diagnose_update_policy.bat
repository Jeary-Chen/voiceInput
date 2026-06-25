@echo off
chcp 65001 >nul 2>&1
setlocal
set "SCRIPT=%~dp0diagnose_update_policy.ps1"
set "LOGDIR=%USERPROFILE%\.voiceinput\logs"

echo.
echo ============================================================
echo  VoiceInput Update Policy Diagnostic
echo  Tests A vs H: current updater vs proposed fix
echo ============================================================
echo.
echo  A = -WindowStyle Hidden  ^(current updater.py^)
echo  H = -NoProfile, no Hidden ^(proposed fix^)
echo.

if not exist "%SCRIPT%" (
    echo [ERROR] Script not found: %SCRIPT%
    pause
    exit /b 1
)

rem Ensure UTF-8 BOM so Windows PowerShell 5.1 parses Chinese correctly
PowerShell -NoProfile -Command "$f='%SCRIPT%';$b=[IO.File]::ReadAllBytes($f);if($b.Length-lt3-or$b[0]-ne0xEF-or$b[1]-ne0xBB-or$b[2]-ne0xBF){[IO.File]::WriteAllText($f,[IO.File]::ReadAllText($f,[Text.Encoding]::UTF8),[Text.UTF8Encoding]::new($true))}" >nul 2>&1

echo Running diagnostic...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "RC=%ERRORLEVEL%"

if %RC% neq 0 (
    echo.
    echo [警告] PowerShell 诊断未能正常完成 ^(exit=%RC%^)
    echo 尝试最小化 cmd 探测...
    echo.

    set "PROBE=%TEMP%\voiceinput_diag_probe.bat"
    >"%PROBE%" echo @echo off
    >>"%PROBE%" echo echo PROBE_OK_BAT
    >>"%PROBE%" echo exit /b 0

    cmd /c "%PROBE%"
    if errorlevel 1 (
        echo [FAIL] cmd 也无法从 %%TEMP%% 运行 bat — 很可能是 Temp 脚本策略
    ) else (
        echo [PASS] cmd+bat 从 Temp 可用 — 问题可能仅限 PowerShell
    )
    del /f /q "%PROBE%" 2>nul

    echo.
    echo 若 PowerShell 被完全拦截，请将本 bat 与 ps1 复制到已安装目录后重试:
    echo   %%LOCALAPPDATA%%\Programs\VoiceInput\
    echo.
)

if exist "%LOGDIR%" (
    echo 最新报告位于: %LOGDIR%
    dir /b /o-d "%LOGDIR%\update_policy_diagnose_*.log" 2>nul | findstr /r "." >nul && (
        for /f "delims=" %%F in ('dir /b /o-d "%LOGDIR%\update_policy_diagnose_*.log" 2^>nul') do (
            echo   %%F
            goto :shown
        )
    )
)
:shown
echo.
pause
exit /b %RC%
