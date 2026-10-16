# Install (or update) the program for the current Windows user. No administrator rights.
#   .\install.ps1                      from .\dist\VolunteerCandidates (after .\build.ps1)
#   .\install.ps1 -From <folder>       from an unzipped release
# Program: %LOCALAPPDATA%\Programs\VolunteerCandidates   Data: %LOCALAPPDATA%\VolunteerCandidates (never touched)
# The previous version is kept in ...\VolunteerCandidates.previous to go back if an update fails.
param([string]$From = (Join-Path $PSScriptRoot "dist\VolunteerCandidates"))
$ErrorActionPreference = "Stop"
$exe = Join-Path $From "VolunteerCandidates.exe"
if (-not (Test-Path $exe)) { throw "Not found: $exe. Run .\build.ps1 first." }
if (Get-Process -Name "VolunteerCandidates" -ErrorAction SilentlyContinue) {
    throw "The program is open. Close it and run again."
}
$programs = Join-Path $env:LOCALAPPDATA "Programs"
$target = Join-Path $programs "VolunteerCandidates"
$previous = "$target.previous"
New-Item -ItemType Directory -Force -Path $programs | Out-Null
if (Test-Path $target) {
    Remove-Item -Recurse -Force $previous -ErrorAction SilentlyContinue
    Move-Item $target $previous
}
Copy-Item -Recurse $From $target
$shell = New-Object -ComObject WScript.Shell
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
# WScript.Shell cannot save a shortcut with a Hebrew file name: save it in English, then rename.
$english = Join-Path $menu "VolunteerCandidates.lnk"
$hebrew = Join-Path $menu "מועמדים למלווים.lnk"
Remove-Item $english, $hebrew -ErrorAction SilentlyContinue
$link = $shell.CreateShortcut($english)
$link.TargetPath = Join-Path $target "VolunteerCandidates.exe"
$link.WorkingDirectory = $target
$link.IconLocation = (Join-Path $target "VolunteerCandidates.exe") + ",0"
$link.Description = "Volunteer candidates - Paamonim Jerusalem"
$link.Save()
try { Rename-Item -LiteralPath $english -NewName "מועמדים למלווים.lnk" -ErrorAction Stop } catch { $hebrew = $english }
Write-Host "Installed in $target. Start menu shortcut: $hebrew"
Write-Host "To go back: close the program, delete $target and rename $previous to VolunteerCandidates."
