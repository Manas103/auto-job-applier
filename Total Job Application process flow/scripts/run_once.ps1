# One collection pass (pings for anything new).
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m pipeline collect @args
