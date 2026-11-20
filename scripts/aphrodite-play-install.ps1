# Aphrodite protocol handler installer (run with PowerShell)
# Registers aphrodite-play:// to open files with the system default player.
# 本脚本与 backend/films.py 里的 HANDLER_INSTALL_PS1 保持一致。
$ErrorActionPreference = "Stop"

$dir = Join-Path $env:LOCALAPPDATA "AphroditePlay"
New-Item -ItemType Directory -Path $dir -Force | Out-Null

$bat = @'
@echo off
set "url=%~1"
set "path=%url:aphrodite-play://=%"
if "%path%"=="" exit /b 1
start "" "%path%"
'@
$batPath = Join-Path $dir "aphrodite-play.bat"
Set-Content -Path $batPath -Value $bat -Encoding ascii

New-PSDrive -Name HKCR -PSProvider Registry -Root HKEY_CLASSES_ROOT | Out-Null
New-Item -Path "HKCR:\aphrodite-play" -Force | Out-Null
Set-ItemProperty -Path "HKCR:\aphrodite-play" -Name "(Default)" -Value "URL:Aphrodite Play Protocol"
Set-ItemProperty -Path "HKCR:\aphrodite-play" -Name "URL Protocol" -Value ""
New-Item -Path "HKCR:\aphrodite-play\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "HKCR:\aphrodite-play\shell\open\command" -Name "(Default)" `
    -Value "`"$batPath`" `"%1`""

Write-Host "Done. aphrodite-play:// is now registered."
Write-Host "Handler: $batPath"
