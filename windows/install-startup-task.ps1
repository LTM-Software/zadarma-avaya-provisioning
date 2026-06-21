param(
    [string]$InstallDir = "C:\AvayaZadarma",
    [string]$ServerIp = "auto",
    [switch]$NoCopy
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

function Set-OrAddLine {
    param(
        [string[]]$Lines,
        [string]$Pattern,
        [string]$NewLine
    )
    $found = $false
    $result = foreach ($line in $Lines) {
        if ($line -match $Pattern) {
            $found = $true
            $NewLine
        } else {
            $line
        }
    }
    if (-not $found) {
        $result += $NewLine
    }
    return $result
}

function Write-EnvFile {
    param(
        [string]$Path,
        [string]$Ip
    )
    $IpLower = if ([string]::IsNullOrWhiteSpace($Ip)) { "" } else { $Ip.ToLowerInvariant() }
    $AutoEnvIp = [string]::IsNullOrWhiteSpace($Ip) -or ($IpLower -in @("auto", "detect", "dhcp"))
    if ($AutoEnvIp) {
        $LogoUrl = "auto"
        $AdvertiseHost = "auto"
    } else {
        $LogoUrl = "http://$Ip/ltm-logo-232x140.jpg"
        $AdvertiseHost = $Ip
    }
    @(
        "AVAYA_EXTENSION=373316-100",
        "AVAYA_DOMAIN=pbx.zadarma.com",
        "SIP_ADVERTISE_HOST=$AdvertiseHost",
        "AVAYA_INTERFACE_ALIAS=",
        "SIP_ADVERTISE_PORT=5060",
        "SIP_REMOTE_HOST=185.45.152.164",
        "SIP_REMOTE_PORT=5060",
        "SIP_EXPIRES=120",
        "SIP_INVITE_EXPIRES=180",
        "HTTP_PORT=80",
        "SYSLOG_PORT=514",
        "AVAYA_LOGO_LABEL=LTM",
        "AVAYA_LOGO_URL=$LogoUrl"
    ) | Set-Content -Path $Path -Encoding ASCII
}

function Update-AvayaSettings {
    param(
        [string]$Path,
        [string]$Ip
    )
    if (-not (Test-Path $Path)) {
        throw "Missing Avaya settings file: $Path"
    }
    $lines = Get-Content -Path $Path
    $lines = Set-OrAddLine $lines '^SET\s+SIP_CONTROLLER_LIST\s+' "SET SIP_CONTROLLER_LIST ${Ip}:5060;transport=udp"
    $lines = Set-OrAddLine $lines '^SET\s+CONFIGURATION_SERVER\s+' "SET CONFIGURATION_SERVER $Ip"
    $lines = Set-OrAddLine $lines '^SET\s+LOGOS\s+' "SET LOGOS LTM=http://$Ip/ltm-logo-232x140.jpg"
    $lines = Set-OrAddLine $lines '^SET\s+LOGSRVR\s+' "SET LOGSRVR $Ip"
    $lines | Set-Content -Path $Path -Encoding ASCII
}

Assert-Administrator

$ServerIpLower = if ([string]::IsNullOrWhiteSpace($ServerIp)) { "" } else { $ServerIp.ToLowerInvariant() }
$AutoIp = [string]::IsNullOrWhiteSpace($ServerIp) -or ($ServerIpLower -in @("auto", "detect", "dhcp"))
if ($AutoIp) {
    $ServerIp = "auto"
}

$SourceDir = $PSScriptRoot
$ExeSource = Join-Path $SourceDir "AvayaGateway.exe"
if (-not (Test-Path $ExeSource)) {
    throw "AvayaGateway.exe was not found in $SourceDir. Run windows\build-exe.ps1 first."
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

if (-not $NoCopy) {
    Copy-Item $ExeSource $InstallDir -Force
    Copy-Item (Join-Path $SourceDir "http") $InstallDir -Recurse -Force
    Copy-Item (Join-Path $SourceDir "install-startup-task.ps1") $InstallDir -Force
    Copy-Item (Join-Path $SourceDir "uninstall-startup-task.ps1") $InstallDir -Force
    Copy-Item (Join-Path $SourceDir "README-Windows.md") $InstallDir -Force
}

New-Item -ItemType Directory -Force -Path (Join-Path $InstallDir "logs") | Out-Null
Write-EnvFile -Path (Join-Path $InstallDir ".env") -Ip $ServerIp
if (-not $AutoIp) {
    Update-AvayaSettings -Path (Join-Path $InstallDir "http\46xxsettings.txt") -Ip $ServerIp
}

$Exe = Join-Path $InstallDir "AvayaGateway.exe"

Get-NetFirewallRule -DisplayName "Avaya Gateway*" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName "Avaya Gateway HTTP TCP 80" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80 -Program $Exe | Out-Null
New-NetFirewallRule -DisplayName "Avaya Gateway SIP UDP 5060" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5060 -Program $Exe | Out-Null
New-NetFirewallRule -DisplayName "Avaya Gateway Syslog UDP 514" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 514 -Program $Exe | Out-Null

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$Action = New-ScheduledTaskAction -Execute $Exe
$Trigger = New-ScheduledTaskTrigger -AtStartup
$Principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Avaya Gateway installed."
Write-Host "Install dir: $InstallDir"
if ($AutoIp) {
    Write-Host "Server IP:   auto-detected at AvayaGateway startup"
} else {
    Write-Host "Server IP:   $ServerIp"
}
Write-Host "Task name:   $TaskName"
Write-Host ""
Write-Host "Logs:"
Write-Host "  $InstallDir\logs\avaya-gateway.log"
Write-Host "  $InstallDir\logs\avaya-syslog.log"
Write-Host ""
Write-Host "Make the phone use the Windows server as its HTTP provisioning server, then reboot the phone."
