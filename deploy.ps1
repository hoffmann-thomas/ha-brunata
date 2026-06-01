# Deploy brunata_online integration to Home Assistant and reload it — no HA restart needed.
#
# One-time setup:
#   1. In HA: Profile > Security > Create long-lived access token
#   2. Set env var:  $env:HA_TOKEN = "your_token_here"
#
# Usage:
#   .\deploy.ps1                          # SCP + reload via REST API (needs HA_TOKEN)
#   .\deploy.ps1 -Token "eyJ..."         # Pass token inline
#   .\deploy.ps1 -Host 192.168.0.164     # Custom host

param(
    [string]$HAHost = "192.168.0.164",
    [string]$User   = "root",
    [string]$Token  = $env:HA_TOKEN,
    [int]   $Port   = 8123
)

$SRC  = "$PSScriptRoot\custom_components\brunata_online"
$DEST = "${User}@${HAHost}:/config/custom_components/brunata_online"

# ── 1. Copy files ──────────────────────────────────────────────────────────────
Write-Host "Copying files to $DEST ..." -ForegroundColor Cyan
scp -r $SRC $DEST
if ($LASTEXITCODE -ne 0) {
    Write-Host "SCP failed (exit $LASTEXITCODE). Check SSH access." -ForegroundColor Red
    exit 1
}

# Clear pycache so HA picks up the new bytecode
ssh "${User}@${HAHost}" "find /config/custom_components/brunata_online -name '__pycache__' -exec rm -rf {} + 2>/dev/null; true" | Out-Null
Write-Host "Files deployed." -ForegroundColor Green

# ── 2. Reload integration via REST API (no full restart) ───────────────────────
if (-not $Token) {
    Write-Host ""
    Write-Host "No HA_TOKEN set — reload the integration manually:" -ForegroundColor Yellow
    Write-Host "  HA > Settings > Integrations > Brunata Online > ⋮ > Reload" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "To automate reloads, set `$env:HA_TOKEN to a long-lived access token" -ForegroundColor DarkGray
    Write-Host "(HA Profile > Security > Long-lived access tokens)" -ForegroundColor DarkGray
    exit 0
}

$BaseUrl = "http://${HAHost}:${Port}/api"
$Headers = @{ Authorization = "Bearer $Token"; "Content-Type" = "application/json" }

# Find the brunata_online config entry ID
Write-Host "Looking up config entry..." -ForegroundColor Cyan
try {
    $Entries  = Invoke-RestMethod -Uri "$BaseUrl/config/config_entries" -Headers $Headers
    $EntryId  = ($Entries | Where-Object { $_.domain -eq "brunata_online" } | Select-Object -First 1).entry_id
} catch {
    Write-Host "Could not reach HA REST API: $_" -ForegroundColor Red
    Write-Host "Reload manually: HA > Settings > Integrations > Brunata Online > Reload" -ForegroundColor Yellow
    exit 1
}

if (-not $EntryId) {
    Write-Host "brunata_online config entry not found. Is the integration configured?" -ForegroundColor Yellow
    exit 1
}

# Flush submodule cache via pyscript, then reload entry.
# This avoids a full HA restart when only submodule (api/*.py) code changes.
Write-Host "Flushing submodule cache via pyscript..." -ForegroundColor Cyan
try {
    Invoke-RestMethod -Method Post -Uri "$BaseUrl/services/pyscript/brunata_flush_and_reload" -Headers $Headers -Body "{}" | Out-Null
    Write-Host "Module cache flushed — integration reloading." -ForegroundColor Green
} catch {
    # Fallback: call reload directly (works for __init__.py / sensor.py changes)
    Write-Host "pyscript flush failed ($_), falling back to direct entry reload..." -ForegroundColor Yellow
    try {
        Invoke-RestMethod -Method Post -Uri "$BaseUrl/config/config_entries/$EntryId/reload" -Headers $Headers | Out-Null
        Write-Host "Integration reloaded (submodule changes may need a restart)." -ForegroundColor Yellow
    } catch {
        Write-Host "Reload failed: $_" -ForegroundColor Red
        Write-Host "Reload manually: HA > Settings > Integrations > Brunata Online > Reload" -ForegroundColor Yellow
    }
}
