param(
    [string]$Python = "py -3"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildRoot = Join-Path $ProjectRoot "build\windows"
$VenvDir = Join-Path $BuildRoot ".venv"
$PyInstallerDist = Join-Path $BuildRoot "pyinstaller-dist"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller-work"
$OutputDir = Join-Path $ProjectRoot "dist\AvayaGateway"

New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null

if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating Python virtualenv..."
    Invoke-Expression "$Python -m venv `"$VenvDir`""
}

$PythonExe = Join-Path $VenvDir "Scripts\python.exe"

& $PythonExe -m pip install --upgrade pip pyinstaller

Remove-Item -Recurse -Force $PyInstallerDist, $PyInstallerWork, $OutputDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null

Write-Host "Building AvayaGateway.exe..."
& $PythonExe -m PyInstaller `
    --clean `
    --onefile `
    --name AvayaGateway `
    --distpath $PyInstallerDist `
    --workpath $PyInstallerWork `
    --specpath $BuildRoot `
    --add-data "$ProjectRoot\avaya-shim\avaya_shim.py;avaya-shim" `
    "$PSScriptRoot\avaya_gateway.py"

Copy-Item (Join-Path $PyInstallerDist "AvayaGateway.exe") $OutputDir
Copy-Item (Join-Path $ProjectRoot "http") $OutputDir -Recurse
Copy-Item (Join-Path $ProjectRoot ".env.example") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "install-startup-task.ps1") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "uninstall-startup-task.ps1") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "README-Windows.md") $OutputDir
New-Item -ItemType Directory -Force -Path (Join-Path $OutputDir "logs") | Out-Null

Write-Host ""
Write-Host "Done:"
Write-Host "  $OutputDir"
Write-Host ""
Write-Host "Copy that folder to the Windows server, then run install-startup-task.ps1 as Administrator."
