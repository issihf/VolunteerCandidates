@echo off
rem Double-click to install. Runs install.ps1 from this folder; no administrator rights needed.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -From "%~dp0"
if errorlevel 1 (
  echo.
  echo Installation failed. Please send a screenshot of this window.
  pause
)
