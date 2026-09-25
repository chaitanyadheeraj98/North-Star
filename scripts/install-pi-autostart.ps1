<#
.SYNOPSIS
    Register (or with -Remove, unregister) the "NorthStar Pi Bridge" scheduled
    task: runs pi-watchdog.ps1 hidden at logon, restarts on failure.
    Removing it does not affect Docker, Pi auth, or start-pi.ps1.
#>
param([switch]$Remove)
$name = 'NorthStar Pi Bridge'

if ($Remove) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed '$name'. The bridge process (if running) was left alone."
    exit
}

$watchdog = Join-Path $PSScriptRoot 'pi-watchdog.ps1'
# conhost --headless: no console window at all (powershell -WindowStyle Hidden still flashes).
$action = New-ScheduledTaskAction -Execute 'conhost.exe' `
    -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$watchdog`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Registered and started '$name'."
