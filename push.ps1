# Send the committed code to GitHub (origin). Right-click > Run with PowerShell, or: .\push.ps1
# Pushes the branch that is checked out. The first time, GitHub may ask you to sign in.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$branch = (git branch --show-current).Trim()
$pending = git status --short
if ($pending) { Write-Host "Note: there are changes that are not committed (they are not pushed):"; $pending }
git push -u origin $branch
if ($LASTEXITCODE -ne 0) { Read-Host "Push failed. Press Enter to close"; exit 1 }
Write-Host "Pushed $branch to GitHub."
Read-Host "Press Enter to close"
