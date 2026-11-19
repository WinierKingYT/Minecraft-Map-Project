# Copy an exported world into .minecraft\saves under a new name. Never overwrites an existing save.
param([Parameter(Mandatory)][string]$WorldDir)
$ErrorActionPreference = 'Stop'
$src = Resolve-Path $WorldDir
$dst = Join-Path (Join-Path $env:APPDATA '.minecraft\saves') (Split-Path $src -Leaf)
if (Test-Path $dst) { throw "Save already exists: $dst (rename or remove it yourself)" }
Copy-Item $src $dst -Recurse
Remove-Item (Join-Path $dst 'session.lock') -ErrorAction SilentlyContinue
Write-Host "Installed: $dst"
