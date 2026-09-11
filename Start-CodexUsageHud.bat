@echo off
cd /d "%~dp0"

if exist "D:\QSoftware\Programs\Python\pythonw.exe" (
  start "" "D:\QSoftware\Programs\Python\pythonw.exe" "%~dp0codex_usage_hud.py"
  goto :eof
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw "%~dp0codex_usage_hud.py"
  goto :eof
)

start "" python "%~dp0codex_usage_hud.py"
