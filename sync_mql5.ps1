$repo = Join-Path $PSScriptRoot "mql5"
$mt5 = "C:\Users\nazir\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5"

Write-Host "Synchronizing Vector Odyssey MQL5 source..."
Write-Host "Source: $repo"
Write-Host "Target: $mt5"

Copy-Item -Path (Join-Path $repo "Experts\*") -Destination (Join-Path $mt5 "Experts") -Recurse -Force
Copy-Item -Path (Join-Path $repo "Include\*") -Destination (Join-Path $mt5 "Include") -Recurse -Force
Copy-Item -Path (Join-Path $repo "Scripts\*") -Destination (Join-Path $mt5 "Scripts") -Recurse -Force

Write-Host "Synchronization complete."
