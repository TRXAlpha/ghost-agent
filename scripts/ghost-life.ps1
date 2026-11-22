# Start Ghost's life layer: Telegram relay (ghost-bot on ghostpi) + daemon (heartbeat, briefings).
# Secrets come from user env vars set once by the owner; they are never written here.
#   GHOST_TELEGRAM_BOT_TOKEN   ghostbot token (copied from ghostpi)
#   GHOST_OPENJARVIS_API_KEY   gonkarouter key for GLM-5.3-Flash
# Routing is set for these processes only, so other ghost/GHOST_MODEL setups are untouched.
# One executor: the daemon runs queued builds (idle-gated); the relay only queues them.
# The daemon needs `ghost policy set idle_work on` (owner decision); without it only the relay runs.
# First run check: .\scripts\ghost-life.ps1 -Check
param([switch]$Check, [string]$Model = 'zai-org/GLM-5.3-Flash')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

foreach ($name in 'GHOST_TELEGRAM_BOT_TOKEN', 'GHOST_OPENJARVIS_API_KEY') {
    $value = [Environment]::GetEnvironmentVariable($name, 'User')
    if (-not $value) { throw "$name is not set (user scope). See docs/engineering/life-layer/STATUS.md." }
    Set-Item "env:$name" $value
}
$env:GHOST_INFERENCE_BACKEND = 'openjarvis'
$env:GHOST_OPENJARVIS_ENGINE = 'openai-compat'
$env:GHOST_OPENJARVIS_URL = 'https://api.gonkarouter.io/v1'
$env:GHOST_ALLOW_REMOTE_INFERENCE = '1'
$env:GHOST_MODEL = $Model

$py = Join-Path $root 'workspaces/openjarvis-venv/Scripts/python.exe'
if ($Check) {
    & $py -m ghost_agent.cli openjarvis doctor --probe
    exit $LASTEXITCODE
}
$logs = Join-Path $root '.ghost/life-logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$relay = Start-Process $py -ArgumentList '-m', 'ghost_agent.cli', 'openjarvis', 'telegram', 'relay',
    '--model', $Model, '--allow-sandbox-builds' -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "$logs/relay.out" -RedirectStandardError "$logs/relay.err"
$daemon = Start-Process $py -ArgumentList '-m', 'ghost_agent.cli', 'daemon', 'run', '--model', $Model `
    -WindowStyle Hidden -PassThru -RedirectStandardOutput "$logs/daemon.out" -RedirectStandardError "$logs/daemon.err"
"relay pid $($relay.Id), daemon pid $($daemon.Id); logs in $logs"
