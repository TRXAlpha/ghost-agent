# Install beside the existing Ghost environment; never repoint or erase it.
[CmdletBinding()]
param([switch]$SkipNative)
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$upstreamCommit = 'a2eb7f0b0ca7b96882d25ac820228d5c43690321'
$upstreamPath = Join-Path $taskRoot 'workspaces/OpenJarvis'
$pythonPath = Join-Path $taskRoot 'workspaces/openjarvis-python/python.exe'
$environmentPath = Join-Path $taskRoot 'workspaces/openjarvis-venv'
$environmentPython = Join-Path $environmentPath 'Scripts/python.exe'
$patchPath = Join-Path $taskRoot 'patches/openjarvis-telegram.patch'
$lockPath = Join-Path $taskRoot 'requirements-openjarvis-windows-py313.lock'

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed (exit $LASTEXITCODE). Existing files were preserved." }
}

if (-not (Test-Path -LiteralPath $upstreamPath)) {
    Invoke-Checked 'git' @('clone', '--filter=blob:none', '--no-checkout', 'https://github.com/open-jarvis/OpenJarvis.git', $upstreamPath)
    Invoke-Checked 'git' @('-C', $upstreamPath, 'checkout', '--detach', $upstreamCommit)
}
$actualCommit = & git -C $upstreamPath rev-parse HEAD
if ($LASTEXITCODE -ne 0 -or $actualCommit.Trim() -ne $upstreamCommit) {
    throw "Existing OpenJarvis checkout is not the qualified commit. Preserve/review it; no automatic reset was attempted."
}
# Apply exactly the maintained Telegram patch, or verify it is already applied.
& git -C $upstreamPath apply --reverse --check $patchPath 2>$null
if ($LASTEXITCODE -ne 0) {
    Invoke-Checked 'git' @('-C', $upstreamPath, 'apply', '--check', $patchPath)
    Invoke-Checked 'git' @('-C', $upstreamPath, 'apply', $patchPath)
}
$changedPaths = @(& git -C $upstreamPath diff --name-only HEAD)
if ($changedPaths | Where-Object { $_ -ne 'src/openjarvis/channels/telegram.py' }) {
    throw 'Unqualified upstream changes detected. Review them before installation.'
}
$actualDiff = ((& git -C $upstreamPath diff --no-ext-diff --no-color HEAD -- src/openjarvis/channels/telegram.py) -join "`n").Trim()
$expectedDiff = (Get-Content -LiteralPath $patchPath -Raw).Replace("`r`n", "`n").Trim()
if ($actualDiff -ne $expectedDiff) {
    throw 'Telegram source differs from the maintained patch. No unqualified source will be installed.'
}

if (-not (Test-Path -LiteralPath $pythonPath)) {
    # Windows Python installation manager: targeted extraction, not a global default change.
    Invoke-Checked 'py' @('install', '--target', (Split-Path -Parent $pythonPath), '3.13')
}
if (-not (Test-Path -LiteralPath $environmentPython)) {
    Invoke-Checked $pythonPath @('-m', 'venv', $environmentPath)
}
Invoke-Checked $environmentPython @('-c', 'import sys; assert sys.version_info[:2] == (3, 13), "Expected isolated Python 3.13"')
Invoke-Checked $environmentPython @('-m', 'pip', 'install', '-c', $lockPath, '-e', $upstreamPath, '-e', $taskRoot, 'pytest')
if (-not $SkipNative) {
    $cargoBin = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.cargo/bin'
    if (Test-Path -LiteralPath (Join-Path $cargoBin 'cargo.exe')) { $env:PATH = "$cargoBin;$env:PATH" }
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        throw 'Rust is required for upstream retrieval. Install Rust + Windows C++ build tools, then rerun; or use -SkipNative for inference-only.'
    }
    Invoke-Checked $environmentPython @('-m', 'pip', 'install', (Join-Path $upstreamPath 'rust/crates/openjarvis-python'))
}
Invoke-Checked $environmentPython @('-m', 'pip', 'check')
Write-Output "Ready: $environmentPath\Scripts\ghost-jarvis.exe"
Write-Output 'No model, Telegram poller, Docker daemon, or paid service was started.'
