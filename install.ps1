# Install (or update) the program for the current Windows user. No administrator rights, no Python needed.
#   Double-click Install.cmd           from the unzipped release (this is what the manager does)
#   .\install.ps1                      from .\dist\VolunteerCandidates (after .\build.ps1)
#   .\install.ps1 -From <folder>       from an unzipped release folder
# Program: %LOCALAPPDATA%\Programs\VolunteerCandidates   Data: %LOCALAPPDATA%\VolunteerCandidates (never touched)
# The previous version is kept in ...\VolunteerCandidates.previous to go back if an update fails.
param([string]$From = "")
$ErrorActionPreference = "Stop"
if (-not $From) {
    if (Test-Path (Join-Path $PSScriptRoot "VolunteerCandidates.exe")) { $From = $PSScriptRoot }
    else { $From = Join-Path $PSScriptRoot "dist\VolunteerCandidates" }
}
$From = (Resolve-Path $From.Trim([char]34)).Path.TrimEnd("\")
$exe = Join-Path $From "VolunteerCandidates.exe"
if (-not (Test-Path $exe)) { throw "Not found: $exe. Unzip the whole release first (do not run from inside the zip)." }
if (Get-Process -Name "VolunteerCandidates" -ErrorAction SilentlyContinue) {
    throw "The program is open. Close it and run again."
}
$programs = Join-Path $env:LOCALAPPDATA "Programs"
$target = Join-Path $programs "VolunteerCandidates"
if ($From -ieq $target) { throw "Run the installer from the downloaded folder, not from the installed program." }
$previous = "$target.previous"
New-Item -ItemType Directory -Force -Path $programs | Out-Null
if (Test-Path $target) {
    Remove-Item -Recurse -Force $previous -ErrorAction SilentlyContinue
    Move-Item $target $previous
}
Copy-Item -Recurse $From $target
# Files from a downloaded zip carry a "from the internet" mark that makes Windows warn on every start.
Get-ChildItem -Recurse -File $target | Unblock-File -ErrorAction SilentlyContinue

$shell = New-Object -ComObject WScript.Shell
$hebrewName = "מועמדים למלווים.lnk"
function New-Shortcut([string]$folder) {
    # WScript.Shell cannot save a shortcut with a Hebrew file name: save it in English, then rename.
    $english = Join-Path $folder "VolunteerCandidates.lnk"
    $hebrew = Join-Path $folder $hebrewName
    Remove-Item $english, $hebrew -ErrorAction SilentlyContinue
    $link = $shell.CreateShortcut($english)
    $link.TargetPath = Join-Path $target "VolunteerCandidates.exe"
    $link.WorkingDirectory = $target
    $link.IconLocation = (Join-Path $target "VolunteerCandidates.exe") + ",0"
    $link.Description = "Volunteer candidates - Paamonim Jerusalem"
    $link.Save()
    try { Rename-Item -LiteralPath $english -NewName $hebrewName -ErrorAction Stop; return $hebrew } catch { return $english }
}
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$startLink = New-Shortcut $menu
$deskLink = New-Shortcut ([Environment]::GetFolderPath("Desktop"))
Write-Host "Installed in $target."
Write-Host "Shortcuts: $startLink"
Write-Host "           $deskLink"
Write-Host "To go back: close the program, delete $target and rename $previous to VolunteerCandidates."
Start-Process (Join-Path $target "VolunteerCandidates.exe")
