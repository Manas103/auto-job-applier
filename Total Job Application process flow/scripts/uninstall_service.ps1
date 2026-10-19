$ErrorActionPreference = "SilentlyContinue"
Stop-ScheduledTask -TaskName "JobPipeline"
Unregister-ScheduledTask -TaskName "JobPipeline" -Confirm:$false
Write-Host "JobPipeline task removed (the serve process, if running, was asked to stop)."
