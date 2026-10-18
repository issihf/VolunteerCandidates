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
    # WScript.Shell fails on any path with Hebrew letters (the Desktop folder under OneDrive is one):
    # build the shortcut in the temp folder under an English name, then copy it to its place.
    $temp = Join-Path $env:TEMP "VolunteerCandidates-shortcut.lnk"
    Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue
    $link = $shell.CreateShortcut($temp)
    $link.TargetPath = Join-Path $target "VolunteerCandidates.exe"
    $link.WorkingDirectory = $target
    $link.IconLocation = (Join-Path $target "VolunteerCandidates.exe") + ",0"
    $link.Description = "Volunteer candidates - Paamonim Jerusalem"
    $link.Save()
    $dest = Join-Path $folder $hebrewName
    Copy-Item -LiteralPath $temp -Destination $dest -Force
    Remove-Item -LiteralPath $temp -ErrorAction SilentlyContinue
    return $dest
}
function Try-Shortcut([string]$folder) {
    try { return New-Shortcut $folder } catch { Write-Warning "Could not create a shortcut in $folder ($($_.Exception.Message))"; return "(not created)" }
}
$menu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$startLink = Try-Shortcut $menu
$deskLink = Try-Shortcut ([Environment]::GetFolderPath("Desktop"))
Write-Host "Installed in $target."
Write-Host "Shortcuts: $startLink"
Write-Host "           $deskLink"
Write-Host "To go back: close the program, delete $target and rename $previous to VolunteerCandidates."
Start-Process (Join-Path $target "VolunteerCandidates.exe")
