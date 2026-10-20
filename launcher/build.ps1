# Build Launcher.exe with PyInstaller.
# Bundles ONLY the chat GUI runtime + requests (used by chris at runtime).
# The chris package, data/ and models are NOT bundled.
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = (Get-Command python).Source
$icon = Join-Path $root "launcher\launcher.ico"
$work = Join-Path $root "launcher\build"
$script = Join-Path $root "launcher\launcher.py"

& $python -m PyInstaller --noconfirm --onefile --windowed --name Launcher `
    --icon $icon `
    --hidden-import requests `
    --distpath $root `
    --workpath $work `
    --specpath (Join-Path $root "launcher") `
    $script

if (Test-Path (Join-Path $root "Launcher.exe")) {
    Write-Output "BUILD OK -> $root\Launcher.exe"
} else {
    Write-Error "Build failed: Launcher.exe not produced."
    exit 1
}
