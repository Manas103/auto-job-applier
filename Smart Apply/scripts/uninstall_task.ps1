$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "SmartApply"
Unregister-ScheduledTask -TaskName "SmartApply" -Confirm:$false
Write-Host "Task 'SmartApply' removed."
