@echo off
rem Starts Cameo on Windows without a console window.
rem Python: runtime\controller-env (existing Cameo runtime) if present, else .venv (made by install.ps1).
cd /d "%~dp0"
set "PYW=%~dp0runtime\controller-env\Scripts\pythonw.exe"
if not exist "%PYW%" set "PYW=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
    echo Cameo is not installed yet. Right-click install.ps1 and choose "Run with PowerShell".
    pause
    exit /b 1
)
start "" "%PYW%" -m cameo run %*
