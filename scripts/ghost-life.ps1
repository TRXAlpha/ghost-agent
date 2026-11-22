# Ghost's life layer: Telegram relay (ghost-bot on ghostpi) + daemon (heartbeat, briefings, worker).
# Secrets come from user env vars set once by the owner; they are never written here.
#   GHOST_TELEGRAM_BOT_TOKEN   ghostbot token (copied from ghostpi)
#   GHOST_OPENJARVIS_API_KEY   gonkarouter key for GLM-5.3-Flash
#   GHOST_SEARCH_URL           SearXNG on ghostpi (tailnet only)
# Routing is set for these processes only, so other ghost/GHOST_MODEL setups are untouched.
# One executor: the daemon runs queued builds (idle-gated); the relay only queues them.
#   .\scripts\ghost-life.ps1 -Check       probe the model route
#   .\scripts\ghost-life.ps1 -Supervise   keep relay + daemon running, restart on exit (Task Scheduler runs this)
#   .\scripts\ghost-life.ps1 -Install     register the GhostLife logon task (current user, no admin)
#   .\scripts\ghost-life.ps1 -Stop        stop the supervised processes
#   .\scripts\ghost-life.ps1 -Report      write docs/engineering/life-layer/24h-report.md and send a summary
#   .\scripts\ghost-life.ps1 -ScheduleReport   one-shot task that runs -Report 24 h from now
param([switch]$Check, [switch]$Supervise, [switch]$Install, [switch]$Stop, [switch]$Report, [switch]$ScheduleReport,
      [string]$Model = 'zai-org/GLM-5.3-Flash')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$logs = Join-Path $root '.ghost/life-logs'
New-Item -ItemType Directory -Force $logs | Out-Null
$statePath = Join-Path $logs 'supervisor.json'

if ($Install) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Supervise"
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew -StartWhenAvailable
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName 'GhostLife' -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'Ghost life layer: relay + daemon (scripts/ghost-life.ps1)' -Force | Out-Null
    "GhostLife task registered for $env:USERNAME"
    exit 0
}
if ($ScheduleReport) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Report"
    $at = (Get-Date).AddHours(24)
    $trigger = New-ScheduledTaskTrigger -Once -At $at
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName 'GhostLife24hReport' -Action $action -Trigger $trigger -Settings $settings `
        -Principal $principal -Description 'One-shot Ghost 24 h life-layer report' -Force | Out-Null
    "GhostLife24hReport scheduled for $at"
    exit 0
}
if ($Stop) {
    if (Test-Path $statePath) {
        $s = Get-Content $statePath -Raw | ConvertFrom-Json
        foreach ($p in @($s.supervisor_pid, $s.relay.pid, $s.daemon.pid)) {
            # /T: the venv python.exe is a launcher; its child is the real interpreter.
            if ($p) { & cmd.exe /c "taskkill /PID $p /T /F >nul 2>&1" }  # already gone is fine
        }
    }
    "stopped"
    exit 0
}

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
$env:GHOST_OLLAMA_TIMEOUT = '180'
$env:GHOST_SEARCH_URL = [Environment]::GetEnvironmentVariable('GHOST_SEARCH_URL', 'User')
$env:PYTHONIOENCODING = 'utf-8'

$py = Join-Path $root 'workspaces/openjarvis-venv/Scripts/python.exe'
if ($Check) {
    & $py -m ghost_agent.cli openjarvis doctor --probe
    exit $LASTEXITCODE
}
if ($Report) {
    & $py -m ghost_agent.cli life-report --hours 24 --out (Join-Path $root 'docs/engineering/life-layer/24h-report.md') --telegram |
        Set-Content (Join-Path $logs 'report.json') -Encoding utf8
    exit $LASTEXITCODE
}

$commands = [ordered]@{
    relay  = @('-m', 'ghost_agent.cli', 'openjarvis', 'telegram', 'relay', '--model', $Model, '--allow-sandbox-builds')
    daemon = @('-m', 'ghost_agent.cli', 'daemon', 'run', '--model', $Model)
}

function Start-Part($name) {
    Start-Process $py -ArgumentList $commands[$name] -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$logs/$name.out" -RedirectStandardError "$logs/$name.err"
}

if (-not $Supervise) {
    $relay = Start-Part 'relay'; $daemon = Start-Part 'daemon'
    "relay pid $($relay.Id), daemon pid $($daemon.Id); logs in $logs"
    exit 0
}

# Supervisor: restart each part on exit with backoff (5 s doubling to 5 min); write health every 30 s.
$parts = @{}
foreach ($name in $commands.Keys) {
    $parts[$name] = @{ proc = (Start-Part $name); restarts = 0; backoff = 5; next = $null; last_exit = $null }
}
$started = Get-Date
while ($true) {
    foreach ($name in $commands.Keys) {
        $part = $parts[$name]
        if ($part.proc -and $part.proc.HasExited) {
            $part.last_exit = $part.proc.ExitCode
            $part.proc = $null
            $part.next = (Get-Date).AddSeconds($part.backoff)
            $part.backoff = [Math]::Min($part.backoff * 2, 300)
        }
        if (-not $part.proc -and $part.next -and (Get-Date) -ge $part.next) {
            $part.proc = Start-Part $name
            $part.restarts += 1
            $part.next = $null
        }
        if ($part.proc -and ((Get-Date) - $part.proc.StartTime).TotalMinutes -gt 10) { $part.backoff = 5 }
    }
    $health = [ordered]@{
        supervisor_pid = $PID
        started_at = [DateTimeOffset]::new($started).ToUnixTimeSeconds()
        updated_at = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    }
    foreach ($name in $commands.Keys) {
        $part = $parts[$name]
        $health[$name] = [ordered]@{ alive = [bool]$part.proc; pid = $(if ($part.proc) { $part.proc.Id } else { $null });
                                     restarts = $part.restarts; last_exit = $part.last_exit }
    }
    $health | ConvertTo-Json -Depth 3 | Set-Content -Path $statePath -Encoding utf8
    Start-Sleep -Seconds 30
}
