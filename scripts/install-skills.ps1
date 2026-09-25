<#
.SYNOPSIS
    Install the canonical router-outcome skill into the agent skill directories
    that exist on this machine.

.DESCRIPTION
    There is ONE source skill, in skills/router-outcome. This copies it into
    whichever supported locations are detected, so Claude, Codex and Pi all
    report outcomes in the same format.

    It only ever touches a directory named router-outcome. Nothing else in your
    skills folders is read, moved or modified. If a router-outcome directory
    already exists it is replaced, because there is one canonical version and a
    stale copy would silently emit an old receipt format.

.PARAMETER WhatIfOnly
    Show what would be installed and exit without writing anything.

.PARAMETER Destination
    Install into an additional directory as well as the detected ones.

.EXAMPLE
    .\scripts\install-skills.ps1

.EXAMPLE
    .\scripts\install-skills.ps1 -WhatIfOnly
#>

[CmdletBinding()]
param(
    [switch]$WhatIfOnly,
    [string[]]$Destination = @()
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root 'skills\router-outcome'

if (-not (Test-Path (Join-Path $source 'SKILL.md'))) {
    Write-Host "Cannot find the source skill at $source" -ForegroundColor Red
    exit 1
}

# Candidate skill roots. Only ones that already exist are used: creating a
# skills directory for an agent that is not installed would be presumptuous,
# and guessing at a layout we have not seen would be worse.
$candidates = @(
    @{ Name = 'Claude Code (user)'; Path = Join-Path $env:USERPROFILE '.claude\skills' }
    @{ Name = 'Codex (user)';       Path = Join-Path $env:USERPROFILE '.codex\skills' }
    @{ Name = 'Pi (user)';          Path = Join-Path $env:USERPROFILE '.pi\skills' }
    @{ Name = 'Pi agent (user)';    Path = Join-Path $env:USERPROFILE '.pi\agent\skills' }
)
foreach ($extra in $Destination) {
    $candidates += @{ Name = 'Custom'; Path = $extra }
}

Write-Host "Adaptive LLM Router - outcome skill installer" -ForegroundColor White
Write-Host "Source: $source"
Write-Host ""

$installed = @()
$missing = @()

foreach ($candidate in $candidates) {
    $parent = Split-Path -Parent $candidate.Path
    $target = Join-Path $candidate.Path 'router-outcome'

    if (-not (Test-Path $candidate.Path)) {
        if (Test-Path $parent) {
            # The agent is installed but has no skills folder yet, so making one
            # is expected rather than presumptuous.
            if ($WhatIfOnly) {
                Write-Host "  WOULD CREATE  $($candidate.Path)" -ForegroundColor Yellow
                Write-Host "  WOULD INSTALL $target" -ForegroundColor Yellow
                $installed += $candidate.Name
                continue
            }
            New-Item -ItemType Directory -Path $candidate.Path -Force | Out-Null
        }
        else {
            $missing += "$($candidate.Name) - $($candidate.Path) not found"
            continue
        }
    }

    if ($WhatIfOnly) {
        $verb = if (Test-Path $target) { 'WOULD REPLACE' } else { 'WOULD INSTALL' }
        Write-Host "  $verb $target" -ForegroundColor Yellow
        $installed += $candidate.Name
        continue
    }

    if (Test-Path $target) {
        Remove-Item $target -Recurse -Force
    }
    Copy-Item $source $target -Recurse -Force
    Write-Host "  INSTALLED  $target" -ForegroundColor Green
    $installed += $candidate.Name
}

Write-Host ""

if ($missing.Count -gt 0) {
    Write-Host "Not installed (agent directory not present):" -ForegroundColor DarkGray
    foreach ($item in $missing) { Write-Host "  - $item" -ForegroundColor DarkGray }
    Write-Host ""
}

if ($installed.Count -eq 0) {
    Write-Host "Nothing was installed. No supported skill directory was found." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Skill locations differ between agent versions. Rather than guess and"
    Write-Host "scatter copies around your home directory, install it by hand:"
    Write-Host ""
    Write-Host "  Copy  $source"
    Write-Host "  into  <your agent's skills directory>\router-outcome"
    Write-Host ""
    Write-Host "Or pass the path directly:"
    Write-Host "  .\scripts\install-skills.ps1 -Destination 'C:\path\to\skills'"
    exit 1
}

if ($WhatIfOnly) {
    Write-Host "Dry run only. Nothing was written." -ForegroundColor Cyan
    exit 0
}

Write-Host "Installed into: $($installed -join ', ')" -ForegroundColor Green
Write-Host ""
Write-Host "After finishing a routed task, ask the agent:" -ForegroundColor White
Write-Host '  "generate the router result receipt"'
Write-Host "then paste the [LLM-ROUTER-RESULT] block into the Result page."
