# Run all automated tests. Report only the last line (passed / failed).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& ".\.venv\Scripts\python.exe" -m pytest
