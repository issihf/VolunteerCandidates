# Fails if the folder to be zipped holds this program's own data: the database, backups, import files or the browser profile.
# Usage: .\packaging\check-package.ps1 -Folder dist\VolunteerCandidates
param([Parameter(Mandatory = $true)][string]$Folder)
$ErrorActionPreference = "Stop"
$bad = Get-ChildItem -Recurse -Force -File -Path $Folder |
    Where-Object { $_.Name -in "data.db", "data.db-journal", "data.db-wal" -or $_.Extension -in ".vcbak", ".xls", ".xlsx", ".oft" }
$badDirs = Get-ChildItem -Recurse -Force -Directory -Path $Folder |
    Where-Object { $_.Name -eq "browser-profile" -or $_.Name -eq "VolunteerCandidates-data" }
if ($bad -or $badDirs) {
    $bad | ForEach-Object { Write-Host "DATA FILE: $($_.FullName)" }
    $badDirs | ForEach-Object { Write-Host "DATA FOLDER: $($_.FullName)" }
    throw "The package must not contain data. Remove the files above and build again."
}
Write-Host "Package check passed: no data in $Folder."
