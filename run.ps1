# Start the system with the real data folder (%LOCALAPPDATA%\VolunteerCandidates).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& ".\.venv\Scripts\python.exe" -m app.launcher @args
