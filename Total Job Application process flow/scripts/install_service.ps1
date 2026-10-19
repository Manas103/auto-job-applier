# Registers the at-logon scheduled task that runs the pipeline service, then starts it.
# Run as the interactive user (toasts only display for the logged-on user).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = (Get-Command pythonw).Source
$taskName = "JobPipeline"

# Resource guardrail: cap WSL2 so builds cannot starve Windows and Chrome (memory/disk thrash hung new
# browser tabs). Installs .wslconfig.pipeline to the user profile only if there is no .wslconfig yet, so a
# hand-tuned one is never overwritten. Takes effect after the next `wsl --shutdown`.
$wslCfg = Join-Path $env:USERPROFILE ".wslconfig"
$src = Join-Path $root ".wslconfig.pipeline"
if ((Test-Path $src) -and -not (Test-Path $wslCfg)) {
  Copy-Item $src $wslCfg
  Write-Host "Installed $wslCfg (WSL capped to 12 GB / 12 cores). Applying with 'wsl --shutdown'..."
  try { wsl.exe --shutdown } catch {}
} elseif (Test-Path $wslCfg) {
  Write-Host "A $wslCfg already exists; leaving it. Ensure it caps WSL memory/processors (see .wslconfig.pipeline)."
}

Write-Host "Warm-up compile (installs any missing MiKTeX packages interactively once)..."
& (Join-Path (Split-Path $py) "python.exe") -m pipeline verify (Join-Path $root "tests\fixtures\tex\good") --no-meta | Out-Host

$action = New-ScheduledTaskAction -Execute $py -Argument "-m pipeline serve --quiet" -WorkingDirectory $root
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Self-healing: also try a start every 30 minutes for 10 years. If the service is already running,
# MultipleInstances IgnoreNew plus data/serve.lock make the extra start a no-op; if it died (sleep,
# logoff, crash), this brings it back within half an hour.
$triggerRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$trigger = @($triggerLogon, $triggerRepeat)
# Priority 8 (below normal) so the service and its children never outrank the interactive user.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -Priority 8
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
$t = Get-ScheduledTask -TaskName $taskName
Write-Host "Task '$taskName' state: $($t.State). Dashboard: http://127.0.0.1:8765"
