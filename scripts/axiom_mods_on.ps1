# Switch TLauncher's shared .minecraft\mods to the Axiom test set (Fabric 1.21.4 + Fabric API + Axiom).
# Reversible: your current mods folder is renamed, never deleted. Undo with axiom_mods_off.ps1.
$ErrorActionPreference = 'Stop'
$mc = Join-Path $env:APPDATA '.minecraft'
$backup = Join-Path $mc 'mods.terrain-ai-backup'
$set = Join-Path $PSScriptRoot '..\axiom\instance\mods'
if (Test-Path $backup) { throw "Backup already exists ($backup): Axiom set seems active. Run axiom_mods_off.ps1 first." }
Rename-Item (Join-Path $mc 'mods') 'mods.terrain-ai-backup'
New-Item -ItemType Directory (Join-Path $mc 'mods') | Out-Null
Copy-Item (Join-Path $set '*.jar') (Join-Path $mc 'mods')
Write-Host "Axiom mod set active. In TLauncher pick 'Fabric 1.21.4' and start. Original mods: $backup"
