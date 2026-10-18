# Build the program folder for installation.
#   .\build.ps1          tests, then dist\VolunteerCandidates\ and dist\VolunteerCandidates-<version>.zip
# A version whose tests fail is not packaged.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".\.venv\Scripts\python.exe")) { py -3.12 -m venv .venv }
& ".\.venv\Scripts\python.exe" -m pip install -q -r requirements-dev.txt
& ".\.venv\Scripts\python.exe" -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed: not packaging." }
Remove-Item -Recurse -Force ".\build", ".\dist\VolunteerCandidates" -ErrorAction SilentlyContinue
& ".\.venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean ".\packaging\volunteercandidates.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
$version = (& ".\.venv\Scripts\python.exe" -c "import app; print(app.__version__)").Trim()
# The installer travels inside the zip, so unzip + double-click Install.cmd is all the user does.
Copy-Item ".\install.ps1", ".\packaging\Install.cmd", ".\packaging\README-INSTALL.txt" ".\dist\VolunteerCandidates\"
$zip = ".\dist\VolunteerCandidates-$version.zip"
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path ".\dist\VolunteerCandidates\*" -DestinationPath $zip
Write-Host "Built $zip. To install on this computer: .\install.ps1"
