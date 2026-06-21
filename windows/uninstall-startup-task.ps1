param(
    [string]$InstallDir = "C:\AvayaZadarma",
    [switch]$RemoveFiles
)

$ErrorActionPreference = "Stop"
$TaskName = "AvayaGateway"

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this PowerShell script as Administrator."
    }
}

Assert-Administrator

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Get-NetFirewallRule -DisplayName "Avaya Gateway*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule

if ($RemoveFiles -and (Test-Path $InstallDir)) {
    Remove-Item -Recurse -Force $InstallDir
}

Write-Host "Avaya Gateway task/firewall rules removed."
if (-not $RemoveFiles) {
    Write-Host "Files were kept in $InstallDir"
}
