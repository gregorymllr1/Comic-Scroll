# packaging/build.ps1
$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot\..

Write-Host "Building frontend..."
Push-Location ui
npm ci
npm run build
Pop-Location

Write-Host "Building app..."
# Use the real CPython install. `python` on PATH can be the WindowsApps stub.
$Python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python 3.13 not found at $Python. Install it, or do not use the WindowsApps python stub."
}
& $Python -m PyInstaller --noconfirm --clean packaging\scrollstrip.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Write-Host "Zipping..."
Compress-Archive -Path dist\Scrollstrip\* -DestinationPath dist\Scrollstrip.zip -Force
Pop-Location
Write-Host "Done: dist\Scrollstrip.zip"
