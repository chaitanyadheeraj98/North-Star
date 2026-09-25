<#
.SYNOPSIS
    Keep the Pi bridge running in the background. Launched hidden by the
    "NorthStar Pi Bridge" scheduled task (see install-pi-autostart.ps1).

    Every 30s it checks GET /health on 127.0.0.1:$Port. If a healthy bridge is
    already listening (e.g. started by hand via start-pi.ps1) it leaves it alone.
    After 3 failed checks it kills only the process on that port and starts a
    fresh bridge. Docker is never touched. Log: pi-bridge\watchdog.log
#>
param([int]$Port = 31415)

$root = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $root 'pi-bridge'
$log = Join-Path $bridge 'watchdog.log'
$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node) { $node = 'C:\Program Files\nodejs\node.exe' }

function Log($m) { "$(Get-Date -Format s) $m" | Add-Content $log }
function Healthy {
    try { (Invoke-WebRequest "http://127.0.0.1:$Port/health" -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 }
    catch { $false }
}

$env:PI_BRIDGE_HOST = '127.0.0.1'
$env:PI_BRIDGE_PORT = "$Port"
$fails = 0
Log "watchdog started"
while ($true) {
    if (Healthy) { $fails = 0 }
    else {
        $fails++
        $listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        # Give a live-but-slow bridge a few chances; start immediately if nothing is listening.
        if (-not $listening -or $fails -ge 3) {
            $listening | ForEach-Object { Log "killing unhealthy pid $($_.OwningProcess)"; Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
            Log "starting bridge"
            Start-Process $node -ArgumentList 'src/server.ts' -WorkingDirectory $bridge -WindowStyle Hidden `
                -RedirectStandardOutput (Join-Path $bridge 'bridge.out.log') -RedirectStandardError (Join-Path $bridge 'bridge.err.log')
            $fails = 0
            Start-Sleep 15
        }
    }
    Start-Sleep 30
}
