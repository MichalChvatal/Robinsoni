@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run_windows.ps1" send
if errorlevel 1 (
    pause
    exit /b 1
)
