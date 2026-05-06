@echo off
chcp 65001 >nul 2>&1
rem Ensure UTF-8 BOM on run.ps1 (Cursor may strip it)
PowerShell -NoProfile -Command "$f='%~dp0run.ps1';$b=[IO.File]::ReadAllBytes($f);if($b.Length-lt3-or$b[0]-ne0xEF-or$b[1]-ne0xBB-or$b[2]-ne0xBF){[IO.File]::WriteAllText($f,[IO.File]::ReadAllText($f,[Text.Encoding]::UTF8),[Text.UTF8Encoding]::new($true))}" >nul 2>&1
PowerShell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
