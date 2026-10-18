<#
.SYNOPSIS
    Keep pi-bridge\node_modules in step with pi-bridge\package-lock.json.
    Dot-sourced by start-pi.ps1, pi-watchdog.ps1 and setup.ps1.

.DESCRIPTION
    "node_modules exists" is not the same as "node_modules is current": after a
    pull that changes the lockfile (for example the Pi SDK move to
    @earendil-works/pi-coding-agent), an old install makes the bridge die on
    import, and the watchdog would restart it forever. So a successful `npm ci`
    records the lockfile's hash in node_modules\.northstar-lock.sha256, and a
    missing or different hash means `npm ci` runs again before the bridge starts.
#>

$script:BridgeDepsStamp = '.northstar-lock.sha256'

function Get-BridgeDepsState([string]$Bridge) {
    $lock = Join-Path $Bridge 'package-lock.json'
    $stamp = Join-Path $Bridge "node_modules\$script:BridgeDepsStamp"
    $want = (Get-FileHash $lock -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    $have = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { '' }
    [pscustomobject]@{ Current = ($have -eq $want); Hash = $want; Stamp = $stamp }
}

# Processes that have a module loaded from this node_modules, i.e. a running
# bridge. On Windows they hold native .node addons open, so npm ci cannot
# delete the folder under them: it fails partway and leaves it half-removed.
function Get-BridgeDepsUsers([string]$Bridge) {
    # Full path with a trailing separator: matches module paths as Windows
    # reports them, and not a sibling such as node_modules_old.
    $modules = [IO.Path]::GetFullPath((Join-Path $Bridge 'node_modules')) + [IO.Path]::DirectorySeparatorChar
    foreach ($p in Get-Process -Name node -ErrorAction SilentlyContinue) {
        try {
            if ($p.Modules | Where-Object { $_.FileName.StartsWith($modules, [StringComparison]::OrdinalIgnoreCase) }) { $p.Id }
        }
        catch { }  # a process we may not inspect is not one we started
    }
}

# Returns 'current', 'installed', 'busy' (a running bridge must stop first) or
# 'failed'. Never throws.
function Sync-BridgeDeps([string]$Bridge, [scriptblock]$Logger = { param($m) Write-Host $m }) {
    # Callers may run with ErrorActionPreference=Stop (start-pi.ps1 does). Under
    # that, Windows PowerShell turns any line npm writes to stderr - even a
    # harmless "npm warn" - into a terminating error. npm's exit code decides.
    $ErrorActionPreference = 'Continue'
    try { $state = Get-BridgeDepsState $Bridge }
    catch { & $Logger "cannot read pi-bridge\package-lock.json: $_"; return 'failed' }
    if ($state.Current) { return 'current' }

    $users = @(Get-BridgeDepsUsers $Bridge)
    if ($users.Count -gt 0) {
        & $Logger ("pi-bridge dependencies are out of date, but a running bridge (pid $($users -join ', ')) " +
            'is using them. Stop it first; the watchdog does this itself on its next restart.')
        return 'busy'
    }

    & $Logger 'pi-bridge dependencies are missing or out of date; running npm ci'
    Push-Location $Bridge
    try {
        $output = & npm ci --no-audit --no-fund 2>&1
        if ($LASTEXITCODE -ne 0) {
            & $Logger "npm ci failed (exit $LASTEXITCODE): $(($output | Select-Object -Last 5) -join ' | ')"
            return 'failed'
        }
    }
    catch {
        & $Logger "npm ci could not run: $_"
        return 'failed'
    }
    finally { Pop-Location }

    try {
        # npm creates no node_modules for a package without dependencies.
        New-Item -ItemType Directory -Force -Path (Split-Path $state.Stamp) -ErrorAction Stop | Out-Null
        Set-Content -Path $state.Stamp -Value $state.Hash -NoNewline -Encoding ascii -ErrorAction Stop
    }
    catch {
        # Installed, but the next start will run npm ci again. Worth saying.
        & $Logger "dependencies installed, but the install stamp could not be written: $_"
        return 'installed'
    }
    & $Logger 'pi-bridge dependencies installed'
    return 'installed'
}
