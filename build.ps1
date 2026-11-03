$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$python = $env:PRINTPAL_PYTHON
if (-not $python -or -not (Test-Path $python)) {
    $python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
}
if (-not (Test-Path $python)) {
    $python = (Get-Command python).Source
}

& $python -m PyInstaller --noconfirm --clean PrintPal.spec

$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    $iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
}
if (-not $iscc) {
    throw "Inno Setup 6 is required to build the installer."
}

& $iscc "installer\PrintPal.iss"
if ($LASTEXITCODE -ne 0) {
    throw "The installer build failed."
}

$portable = Join-Path $root "dist\PrintPal-Portable-1.0.0.zip"
if (Test-Path $portable) {
    Remove-Item $portable -Force
}
Compress-Archive -Path (Join-Path $root "dist\PrintPal\*") -DestinationPath $portable -Force

Write-Host ""
Write-Host "Installer : $root\dist\installer\PrintPal-Setup-1.0.0.exe"
Write-Host "Portable  : $portable"
