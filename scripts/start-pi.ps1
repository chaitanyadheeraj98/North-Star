<#
.SYNOPSIS
    Start the Pi bridge on this machine.

.DESCRIPTION
    The bridge is the seam between the Dockerised backend and Pi. It runs on the
    host rather than in a container because Pi authenticates against your
    existing Claude / ChatGPT subscriptions through its own credential store,
    and a container would have neither those credentials nor a browser to
    complete an OAuth login with.

    It binds to 127.0.0.1 only. Anything that can reach this port can spend your
    subscription quota, so it must not be exposed to the network.

    The analyzer provider, model and thinking level come from .env at the
    repository root. The parameters below override that for one run; omit them
    and .env wins, so there is a single place to change the analyzer.

.PARAMETER Provider
    Pi provider for the analyzer, e.g. openai-codex. Overrides PI_PROVIDER.

.PARAMETER Model
    Pi model id for the analyzer. Overrides PI_MODEL.
    Run `pi --list-models` to see the options.

.PARAMETER Thinking
    Pi thinking level for the analyzer itself. Overrides PI_THINKING_LEVEL.
    This is the level for the CLASSIFIER, not the effort the router recommends
    for the model that does the actual work.

.EXAMPLE
    .\scripts\start-pi.ps1

.EXAMPLE
    .\scripts\start-pi.ps1 -Provider openai-codex -Model gpt-5.4-mini
#>

[CmdletBinding()]
param(
    [string]$Provider,
    [string]$Model,
    # The five levels the installed Pi SDK accepts.
    [ValidateSet('minimal', 'low', 'medium', 'high', 'xhigh')]
    [string]$Thinking,
    [int]$Port = 31415
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$bridge = Join-Path $root 'pi-bridge'

if (-not (Test-Path (Join-Path $bridge 'node_modules'))) {
    Write-Host "Installing Pi bridge dependencies (first run)..." -ForegroundColor Cyan
    Push-Location $bridge
    try {
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    }
    finally { Pop-Location }
}

$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Something is already listening on 127.0.0.1:$Port." -ForegroundColor Yellow
    Write-Host "If that is an old bridge, stop it first:" -ForegroundColor Yellow
    Write-Host "  Get-NetTCPConnection -LocalPort $Port -State Listen | ForEach-Object { Stop-Process -Id `$_.OwningProcess -Force }"
    exit 1
}

$env:PI_BRIDGE_HOST = '127.0.0.1'
$env:PI_BRIDGE_PORT = "$Port"

# Only set these when explicitly passed. An unconditional assignment would
# shadow .env, which is where the analyzer is actually configured.
if ($Provider) { $env:PI_PROVIDER = $Provider }
if ($Model) { $env:PI_MODEL = $Model }
if ($Thinking) { $env:PI_THINKING_LEVEL = $Thinking }

if ($Provider -and -not $Model) {
    Write-Host "Pass -Model as well as -Provider: a provider paired with another" -ForegroundColor Yellow
    Write-Host "provider's model id cannot resolve." -ForegroundColor Yellow
    exit 1
}

Write-Host "Starting Pi bridge on http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "  analyzer runs with NO TOOLS: no filesystem, no shell, no repository access."
Write-Host "  The startup banner reports which model it will classify with."
Write-Host "  Press Ctrl+C to stop."
Write-Host ""

Push-Location $bridge
try {
    node src/server.ts
}
finally {
    Pop-Location
}
