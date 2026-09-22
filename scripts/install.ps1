# PortForge agent installer (Windows / PowerShell). Idempotent -- safe to
# re-run for upgrades, reinstalls, or reconfiguration. See docs/installation.md
# for the full guide and docs/v1.1/upgrade.md for the upgrade path this reuses.
#
# No elevation is required: the native service is installed as a per-user
# Task Scheduler task (ONLOGON, LIMITED run level).

[CmdletBinding()]
param(
    [string]$CentralUrl,
    [string]$EnrollmentToken,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"

function Fail($message) {
    Write-Error $message
    exit 1
}

if ($EnrollmentToken -and -not $CentralUrl) {
    Fail "-EnrollmentToken was given without -CentralUrl."
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    $pythonCmd = Get-Command py -ErrorAction SilentlyContinue
}
if (-not $pythonCmd) {
    Fail "Python 3.9+ is required but no python/py was found on PATH."
}

$versionCheck = & $pythonCmd.Source -c "import sys; print(1 if sys.version_info[:2] >= (3, 9) else 0)"
if ($versionCheck.Trim() -ne "1") {
    $foundVersion = & $pythonCmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
    Fail "Python 3.9+ is required; found $foundVersion."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$agentPath = Join-Path $repoRoot "agent"

Write-Host "==> Installing the PortForge agent package (pip install -e .\agent)"
& $pythonCmd.Source -m pip install -e $agentPath --quiet
if ($LASTEXITCODE -ne 0) {
    Fail "pip install failed (exit code $LASTEXITCODE)."
}

$portforgeCmd = Get-Command portforge -ErrorAction SilentlyContinue
if (-not $portforgeCmd) {
    Fail "portforge was installed but is not on PATH -- check your Python Scripts directory is on PATH."
}
& portforge --help | Out-Null
Write-Host "==> portforge CLI is installed and on PATH"

Write-Host "==> Installing/reinstalling the native background service"
& portforge agent service install
if ($LASTEXITCODE -ne 0) {
    Fail "portforge agent service install failed (exit code $LASTEXITCODE)."
}

if ($EnrollmentToken) {
    Write-Host "==> Enrolling with Central at $CentralUrl"
    & portforge agent enroll --server $CentralUrl --token $EnrollmentToken
    if ($LASTEXITCODE -ne 0) {
        Fail "Enrollment failed (exit code $LASTEXITCODE)."
    }
}
elseif ($NonInteractive) {
    Write-Host "==> Skipping enrollment (-NonInteractive, no -EnrollmentToken given)."
    Write-Host "    Run 'portforge agent enroll --server <url> --token <token>' later."
}
else {
    Write-Host ""
    Write-Host "No enrollment token given. To enroll this host with Central later, run:"
    Write-Host "  portforge agent enroll --server <central-url> --token <token>"
    Write-Host "(mint a token on the Central host with:"
    Write-Host "  portforge central generate-token --url <central-url>)"
    Write-Host ""
}

Write-Host "==> Starting the native service"
& portforge agent service start
if ($LASTEXITCODE -ne 0) {
    Write-Host "    (service start reported an issue -- check 'portforge agent service status')"
}

Write-Host ""
Write-Host "==> Running portforge doctor"
if ($CentralUrl) {
    & portforge doctor --url $CentralUrl
} else {
    & portforge doctor
}

Write-Host ""
Write-Host "Install complete. Re-run this script any time to reinstall/upgrade the service in place --"
Write-Host "it never overwrites your host identity or enrollment."
