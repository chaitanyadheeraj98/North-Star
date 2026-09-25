<#
.SYNOPSIS
    First-time setup for Adaptive LLM Router.

.DESCRIPTION
    Checks prerequisites, installs the Pi bridge dependencies, creates the data
    directory and .env, and then tells you exactly what to run next. It changes
    nothing outside this repository except the npm cache.

.EXAMPLE
    .\scripts\setup.ps1
#>

[CmdletBinding()]
param(
    [switch]$SkipDocker,
    [switch]$SkipPiBridge
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$problems = @()

function Write-Step { param([string]$Text) Write-Host "`n== $Text" -ForegroundColor Cyan }
function Write-Ok   { param([string]$Text) Write-Host "   OK    $Text" -ForegroundColor Green }
function Write-Warn { param([string]$Text) Write-Host "   WARN  $Text" -ForegroundColor Yellow }
function Write-Bad  { param([string]$Text) Write-Host "   FAIL  $Text" -ForegroundColor Red }

Write-Host "Adaptive LLM Router - setup" -ForegroundColor White
Write-Host "Repository: $root"

# --- Prerequisites ----------------------------------------------------------

Write-Step "Checking prerequisites"

if (-not $SkipDocker) {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $docker) {
        Write-Bad "Docker not found. Install Docker Desktop: https://docs.docker.com/desktop/"
        $problems += 'docker'
    }
    else {
        $version = (docker --version) 2>&1
        Write-Ok "$version"
        docker info *> $null
        if ($LASTEXITCODE -ne 0) {
            Write-Warn "Docker is installed but the daemon is not responding. Start Docker Desktop."
            $problems += 'docker-daemon'
        }
        else {
            Write-Ok "Docker daemon is running"
        }
    }
}

$node = Get-Command node -ErrorAction SilentlyContinue
if ($null -eq $node) {
    Write-Bad "Node.js not found. The Pi bridge needs Node 22.6 or newer: https://nodejs.org/"
    $problems += 'node'
}
else {
    $nodeVersion = (node --version).TrimStart('v')
    $major = [int]($nodeVersion.Split('.')[0])
    $minor = [int]($nodeVersion.Split('.')[1])
    if ($major -lt 22 -or ($major -eq 22 -and $minor -lt 6)) {
        Write-Bad "Node $nodeVersion is too old. The bridge runs TypeScript directly, which needs 22.6+."
        $problems += 'node-version'
    }
    else {
        Write-Ok "Node $nodeVersion"
    }
}

$pi = Get-Command pi -ErrorAction SilentlyContinue
if ($null -eq $pi) {
    Write-Warn "The 'pi' CLI is not on PATH. Install it with: npm install -g @mariozechner/pi-coding-agent"
    Write-Warn "The bridge can still run, but Pi must be authenticated before it can classify anything."
}
else {
    # pi writes its version to the host stream, so capture it explicitly
    # rather than letting it print itself before the status line.
    $piVersion = (pi --version 2>&1 | Out-String).Trim()
    Write-Ok "pi $piVersion"
}

# --- Directories and .env ---------------------------------------------------

Write-Step "Preparing directories"

$dataDir = Join-Path $root 'data'
if (-not (Test-Path $dataDir)) {
    New-Item -ItemType Directory -Path $dataDir | Out-Null
    Write-Ok "Created data\  (the SQLite database lives here)"
}
else {
    Write-Ok "data\ already exists"
}

$envFile = Join-Path $root '.env'
$envExample = Join-Path $root '.env.example'
if (-not (Test-Path $envFile)) {
    if (Test-Path $envExample) {
        Copy-Item $envExample $envFile
        Write-Ok "Created .env from .env.example"
    }
}
else {
    Write-Ok ".env already exists (left untouched)"
}

# --- Pi bridge dependencies -------------------------------------------------

if (-not $SkipPiBridge -and $problems -notcontains 'node' -and $problems -notcontains 'node-version') {
    Write-Step "Installing Pi bridge dependencies"
    $bridge = Join-Path $root 'pi-bridge'
    Push-Location $bridge
    try {
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) {
            Write-Bad "npm install failed in pi-bridge"
            $problems += 'npm'
        }
        else {
            Write-Ok "Pi bridge dependencies installed"
        }
    }
    finally {
        Pop-Location
    }
}

# --- Pi authentication check ------------------------------------------------

Write-Step "Checking Pi authentication"

$authPath = Join-Path $env:USERPROFILE '.pi\agent\auth.json'
if (Test-Path $authPath) {
    # Report provider NAMES only. Never read or print a credential value.
    try {
        $providers = (Get-Content $authPath -Raw | ConvertFrom-Json).PSObject.Properties.Name
        if ($providers.Count -gt 0) {
            Write-Ok "Pi holds credentials for: $($providers -join ', ')"
            Write-Warn "Credentials can still be expired. The bridge checks this at startup and says so."
        }
        else {
            Write-Warn "No providers authenticated. Run 'pi' and use /login."
        }
    }
    catch {
        Write-Warn "Could not read Pi's credential file. Run 'pi' and use /login."
    }
}
else {
    Write-Warn "Pi is not authenticated yet. Run 'pi' in a terminal and use /login."
}

# --- Summary ----------------------------------------------------------------

Write-Step "Next steps"

if ($problems.Count -gt 0) {
    Write-Host ""
    Write-Bad "Fix the problems above first: $($problems -join ', ')"
    exit 1
}

Write-Host @"

1. Authenticate Pi once, if you have not already:

       pi
       /login

2. Start the web stack:

       docker compose up -d

3. Start the Pi bridge on this machine (it stays out of Docker so it can use
   your existing subscription logins):

       .\scripts\start-pi.ps1

4. Install the outcome skill into Claude and Codex:

       .\scripts\install-skills.ps1

5. Open http://localhost:3000

"@ -ForegroundColor White

Write-Ok "Setup complete"
