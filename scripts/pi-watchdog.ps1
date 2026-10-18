<#
.SYNOPSIS
    Keep the Pi bridge running in the background. Launched hidden by the
    "NorthStar Pi Bridge" scheduled task (see install-pi-autostart.ps1).

    Every 30s it checks GET /health on 127.0.0.1:$Port. If a healthy bridge is
    already listening (e.g. started by hand via start-pi.ps1) it leaves it alone.
    After 3 failed checks it kills only the process on that port and starts a
    fresh bridge. Docker is never touched. Log: pi-bridge\watchdog.log

    The Reconnect button reaches it through data\pi-restart.request: the backend
    container writes the file, this script deletes it within 3s and restarts the
    bridge - including a healthy one started by hand, since that is the request.
#>
param([int]$Port = 31415)

$root = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $root 'pi-bridge'
$log = Join-Path $bridge 'watchdog.log'
$request = Join-Path $root 'data\pi-restart.request'
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) { $node = 'C:\Program Files\nodejs\node.exe' }

. (Join-Path $PSScriptRoot 'bridge-deps.ps1')

# $script:log, not $log: PowerShell resolves variables through the CALLER's
# scopes too, so a caller's own $Log (a logger scriptblock) would be used as the
# path - which PowerShell then invokes as a script, recursing until it overflows.
function Log($m) { "$(Get-Date -Format s) $m" | Add-Content $script:log }
function Healthy {
    try { (Invoke-WebRequest "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { $false }
}
function Restart-Bridge($reason) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object {
            Log "killing pid $($_.OwningProcess) ($reason)"
            Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
            # -Force returns before the process is gone; until it is, it still
            # holds node_modules open and npm ci below would see it as busy.
            Wait-Process -Id $_.OwningProcess -Timeout 10 -ErrorAction SilentlyContinue
        }
    # After a failed npm ci (e.g. offline) retry at most every 10 minutes rather
    # than on every restart; the bridge is started either way and its error log
    # says what is missing.
    if (-not $script:depsFailedAt -or ((Get-Date) - $script:depsFailedAt).TotalMinutes -ge 10) {
        $script:depsFailedAt = if ((Sync-BridgeDeps $bridge { param($m) Log $m }) -in 'failed', 'busy') { Get-Date } else { $null }
    }
    Log "starting bridge ($reason)"
    Start-Process $node -ArgumentList 'src/server.ts' -WorkingDirectory $bridge -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $bridge 'bridge.out.log') -RedirectStandardError (Join-Path $bridge 'bridge.err.log')
    # No sleep here: the caller resets $sinceCheck, which gives the new bridge 30s
    # before its first health check, and the next restart request is still seen
    # within 3s (the backend gives up waiting for pickup after 10s).
}

# One watchdog per port. Two would both act on every restart request, each
# killing the bridge the other just started.
$script:mutex = New-Object System.Threading.Mutex($false, "Local\NorthStarPiWatchdog-$Port")
try { $owned = $script:mutex.WaitOne(0) }
catch [System.Threading.AbandonedMutexException] { $owned = $true }  # previous watchdog was killed
if (-not $owned) {
    Log "another watchdog is already running for port $Port; exiting"
    exit 0
}

$env:PI_BRIDGE_HOST = '127.0.0.1'
$env:PI_BRIDGE_PORT = "$Port"
$fails = 0
$sinceCheck = 30
Log "watchdog started"
while ($true) {
    if (Test-Path $request) {
        Remove-Item $request -Force -ErrorAction SilentlyContinue
        Restart-Bridge 'reconnect requested'
        $fails = 0
        $sinceCheck = 0
    }
    elseif ($sinceCheck -ge 30) {
        $sinceCheck = 0
        if (Healthy) { $fails = 0 }
        else {
            $fails++
            $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
            # Give a live-but-slow bridge a few chances; start immediately if nothing is listening.
            if (-not $listening -or $fails -ge 3) {
                Restart-Bridge 'unhealthy'
                $fails = 0
            }
        }
    }
    Start-Sleep 3
    $sinceCheck += 3
}
