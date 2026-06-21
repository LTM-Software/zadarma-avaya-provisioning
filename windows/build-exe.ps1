param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"

function Resolve-Python {
    param([string]$RequestedPython)

    if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) {
        return $RequestedPython
    }

    $candidates = @("py -3", "python", "python3")
    foreach ($candidate in $candidates) {
        try {
            $null = Invoke-Expression "$candidate --version 2>&1"
            if ($LASTEXITCODE -eq 0 -or $LASTEXITCODE -eq $null) {
                return $candidate
            }
        } catch {
        }
    }

    throw "Python 3 was not found. Install it with: winget install -e --id Python.Python.3.12"
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BuildRoot = Join-Path $ProjectRoot "build\windows"
$VenvDir = Join-Path $BuildRoot ".venv"
$PyInstallerDist = Join-Path $BuildRoot "pyinstaller-dist"
$PyInstallerWork = Join-Path $BuildRoot "pyinstaller-work"
$OutputDir = Join-Path $ProjectRoot "dist\AvayaGateway"

New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
$PythonCommand = Resolve-Python $Python

if (-not (Test-Path $VenvDir)) {
    Write-Host "Creating Python virtualenv..."
    Invoke-Expression "$PythonCommand -m venv `"$VenvDir`""
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
    --hidden-import functools `
    --hidden-import html `
    --hidden-import http.server `
    --hidden-import socketserver `
    --hidden-import urllib.parse `
    --hidden-import xml.etree.ElementTree `
    --add-data "$ProjectRoot\avaya-shim\avaya_shim.py;avaya-shim" `
    "$PSScriptRoot\avaya_gateway.py"

Copy-Item (Join-Path $PyInstallerDist "AvayaGateway.exe") $OutputDir
Copy-Item (Join-Path $ProjectRoot "http") $OutputDir -Recurse
Copy-Item (Join-Path $ProjectRoot ".env.example") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "install-startup-task.ps1") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "install-startup-task.cmd") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "uninstall-startup-task.ps1") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "run-debug.cmd") $OutputDir
Copy-Item (Join-Path $PSScriptRoot "README-Windows.md") $OutputDir
New-Item -ItemType Directory -Force -Path (Join-Path $OutputDir "logs") | Out-Null

Write-Host ""
Write-Host "Done:"
Write-Host "  $OutputDir"
Write-Host ""
Write-Host "Copy that folder to the Windows server, then run install-startup-task.ps1 as Administrator."
