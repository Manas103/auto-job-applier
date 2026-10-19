# Runs the service in the foreground (Ctrl+C to stop). The scheduled task uses pythonw instead.
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m pipeline serve @args
