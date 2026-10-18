<#
.SYNOPSIS
    Register (or with -Remove, unregister) the "NorthStar Pi Bridge" scheduled
    task: runs pi-watchdog.ps1 hidden at logon, restarts on failure.
    Removing it does not affect Docker, Pi auth, or start-pi.ps1.

    Re-run it after updating the repository: it stops the running watchdog,
    starts the new one, and has that restart the bridge (installing changed
    dependencies first), so everything runs the new code.
#>
param(
    [switch]$Remove,
    [string]$TaskName = 'NorthStar Pi Bridge',
    [int]$Port = 31415
)
$name = $TaskName
$watchdog = Join-Path $PSScriptRoot 'pi-watchdog.ps1'

# Stop-ScheduledTask alone does NOT end the watchdog: the task shows Ready while
# its powershell.exe loop keeps running, and starting the task again would run a
# second watchdog beside the old one. So end the process itself - only this
# checkout's, matched on the script's full path, never another clone's.
function Stop-Watchdog {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq 'powershell.exe' -and $_.CommandLine -and
            $_.CommandLine.IndexOf($watchdog, [StringComparison]::OrdinalIgnoreCase) -ge 0
        } |
        ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped running watchdog (pid $($_.ProcessId))."
        }
}

if ($Remove) {
    Stop-Watchdog
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed '$name'. The bridge process (if running) was left alone."
    exit
}

Stop-Watchdog
# Have the new watchdog restart the bridge as its first act, so the running
# bridge picks up the new code too. That restart stops the bridge BEFORE it
# runs npm ci for a changed package-lock.json, which matters on Windows: a
# running bridge holds native .node addons in node_modules open, and npm ci
# cannot delete them under it.
$request = Join-Path (Split-Path -Parent $PSScriptRoot) 'data\pi-restart.request'
New-Item -ItemType Directory -Force -Path (Split-Path $request) | Out-Null
Set-Content -Path $request -Value 'install-pi-autostart' -Encoding ascii

# conhost --headless: no console window at all (powershell -WindowStyle Hidden still flashes).
$action = New-ScheduledTaskAction -Execute 'conhost.exe' `
    -Argument "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$watchdog`" -Port $Port"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $name
Write-Host "Registered and started '$name'."
