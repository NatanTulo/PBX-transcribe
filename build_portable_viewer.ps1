param(
    [switch]$InstallBuilder,
    [switch]$BundleData,
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$OutputName = 'PBX-Transcribe-Viewer'
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$package = Join-Path $root 'dist\PBX-Transcribe-Viewer'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Brak środowiska .venv: $python"
}
if ($InstallBuilder) {
    & $python -m pip install 'pyinstaller>=6,<7'
    if ($LASTEXITCODE -ne 0) {
        throw "Instalacja PyInstallera nie powiodła się (kod $LASTEXITCODE)."
    }
}

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --name $OutputName `
    --paths (Join-Path $root 'src') `
    --add-data "$(Join-Path $root 'src\pbx_transcribe\static');pbx_transcribe\static" `
    --distpath $package `
    --workpath (Join-Path $root 'build\portable-viewer') `
    --specpath (Join-Path $root 'build') `
    (Join-Path $root 'packaging\viewer_entry.py')
if ($LASTEXITCODE -ne 0) {
    throw "Budowanie EXE nie powiodło się (kod $LASTEXITCODE). Zamknij uruchomiony viewer i spróbuj ponownie."
}

if ($BundleData) {
    $outputTarget = Join-Path $package 'output_full'
    $audioTarget = Join-Path $package 'audio'
    New-Item -ItemType Directory -Path $outputTarget -Force | Out-Null
    New-Item -ItemType Directory -Path $audioTarget -Force | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $root 'output_full') -Force |
        Copy-Item -Destination $outputTarget -Recurse -Force
    Get-ChildItem -LiteralPath (Join-Path $root 'rozmowy') -Force |
        Copy-Item -Destination $audioTarget -Recurse -Force
}

Write-Host "Gotowe: $package"
