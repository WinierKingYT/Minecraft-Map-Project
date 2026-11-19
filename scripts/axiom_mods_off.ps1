# Restore the original .minecraft\mods folder saved by axiom_mods_on.ps1.
$ErrorActionPreference = 'Stop'
$mc = Join-Path $env:APPDATA '.minecraft'
$backup = Join-Path $mc 'mods.terrain-ai-backup'
if (-not (Test-Path $backup)) { throw "No backup at $backup; nothing to restore." }
$extra = Get-ChildItem (Join-Path $mc 'mods') | Where-Object { $_.Name -notmatch '^(Axiom-|fabric-api-)' }
if ($extra) { throw "mods contains files not from the Axiom set: $($extra.Name -join ', '). Move them out first." }
Remove-Item (Join-Path $mc 'mods') -Recurse
Rename-Item $backup 'mods'
Write-Host "Original mods restored."
