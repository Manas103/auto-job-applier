# Registers the at-logon scheduled task that runs the Smart Apply daemon (dashboard + filler), then starts it.
# Run as the interactive user: toasts and the SmartApply Chrome window only exist for the logged-on user, and a
# Windows service (Session 0) could never show a browser window.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = (Get-Command pythonw).Source
$taskName = "SmartApply"

$action = New-ScheduledTaskAction -Execute $py -Argument "-m smartapply serve --quiet" -WorkingDirectory $root
$triggerLogon = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Self-healing: try a start every 30 minutes; the daemon's lock file makes extra starts a no-op.
$triggerRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -Priority 8
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger @($triggerLogon, $triggerRepeat) -Settings $settings -RunLevel Limited -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3
$t = Get-ScheduledTask -TaskName $taskName
Write-Host "Task '$taskName' state: $($t.State). Dashboard: http://127.0.0.1:8770"
