<#
  sync_mql5.ps1 -- copy the repo's MQL5 source into the MT5 terminal's data folder.

  Rewritten 2026-09-19 after a run that printed 19 "being used by another
  process" errors and then "Synchronization complete." The old script
  overwrote EVERY file on every run, so any destination file open in a
  MetaEditor tab (MetaEditor blocks external writes to open files) or held
  by the running terminal (.ex5 of a live EA) failed -- even when the file
  had not changed at all -- and the failures were easy to miss.

  Now:
    * only files whose content differs (SHA-256) are copied; unchanged files
      are skipped, so an open-but-unchanged file is never an error;
    * a locked file that DID change is retried, then reported by name with
      the likely cause, and the script exits non-zero -- a partial sync is
      never reported as complete;
    * only git-tracked files are synced (falls back to all files if git is
      unavailable), and zero-byte files are skipped with a warning -- three
      empty, untracked VO_*Bridge.mq5 stubs kept reappearing in the working
      tree and were being copied into the terminal as broken Experts;
    * -DryRun shows what would be copied without touching the terminal.

  Usage (from the repo root, in cmd or PowerShell):
    powershell -ExecutionPolicy Bypass -File .\sync_mql5.ps1 [-DryRun]
#>
param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$repo = Join-Path $PSScriptRoot "mql5"
$mt5  = "C:\Users\nazir\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5"
$folders = @("Experts", "Include", "Scripts", "Indicators")

Write-Host "Synchronizing Vector Odyssey MQL5 source..."
Write-Host "Source: $repo"
Write-Host "Target: $mt5"
if ($DryRun) { Write-Host "(dry run -- nothing will be written)" }

if (-not (Test-Path $mt5)) {
    Write-Host "ERROR: terminal data folder not found: $mt5" -ForegroundColor Red
    exit 2
}

# ---- source file set: git-tracked files under mql5/, else everything ----
$tracked = $null
try {
    Push-Location $PSScriptRoot
    $gitOut = & git ls-files -- mql5 2>$null
    if ($LASTEXITCODE -eq 0 -and $gitOut) {
        $tracked = @{}
        foreach ($line in $gitOut) {
            $tracked[(Join-Path $PSScriptRoot ($line -replace '/', '\'))] = $true
        }
    }
} catch {
    $tracked = $null
} finally {
    Pop-Location
}
if ($null -eq $tracked) {
    Write-Host "note: git not available -- syncing every file under mql5\ (untracked files included)" -ForegroundColor Yellow
}

$copied  = New-Object System.Collections.Generic.List[string]
$skipped = 0
$empty   = New-Object System.Collections.Generic.List[string]
$failed  = New-Object System.Collections.Generic.List[string]

function Get-Sha256([string]$path) {
    return (Get-FileHash -Path $path -Algorithm SHA256).Hash
}

foreach ($folder in $folders) {
    $srcRoot = Join-Path $repo $folder
    if (-not (Test-Path $srcRoot)) { continue }
    $dstRoot = Join-Path $mt5 $folder

    Get-ChildItem -Path $srcRoot -Recurse -File | ForEach-Object {
        $src = $_.FullName
        if ($null -ne $tracked -and -not $tracked.ContainsKey($src)) {
            return   # untracked in git: not part of the repo, never synced
        }
        if ($_.Length -eq 0) {
            $empty.Add($src)
            return
        }
        $rel = $src.Substring($srcRoot.Length).TrimStart('\')
        $dst = Join-Path $dstRoot $rel

        if (Test-Path $dst) {
            if ((Get-Sha256 $src) -eq (Get-Sha256 $dst)) {
                $script:skipped++
                return
            }
        }

        if ($DryRun) {
            Write-Host "  would copy  $folder\$rel"
            $copied.Add("$folder\$rel")
            return
        }

        $dstDir = Split-Path $dst -Parent
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }

        $ok = $false
        for ($attempt = 1; $attempt -le 4; $attempt++) {
            try {
                Copy-Item -Path $src -Destination $dst -Force
                $ok = $true
                break
            } catch {
                # locked destination (open in MetaEditor / running .ex5): wait and retry
                if ($attempt -lt 4) { Start-Sleep -Milliseconds 500 }
            }
        }
        if ($ok) {
            Write-Host "  copied      $folder\$rel"
            $copied.Add("$folder\$rel")
        } else {
            Write-Host "  FAILED      $folder\$rel  (destination is locked by another process)" -ForegroundColor Red
            $failed.Add("$folder\$rel")
        }
    }
}

Write-Host ""
Write-Host ("copied {0}, unchanged {1}, failed {2}" -f $copied.Count, $skipped, $failed.Count)

if ($empty.Count -gt 0) {
    Write-Host ""
    Write-Host "skipped zero-byte files (not synced; delete them from the repo if they are junk):" -ForegroundColor Yellow
    foreach ($e in $empty) { Write-Host "  $e" }
}

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "SYNC INCOMPLETE. These CHANGED files could not be written:" -ForegroundColor Red
    foreach ($f in $failed) { Write-Host "  $f" -ForegroundColor Red }
    Write-Host "Likely causes: the file is open in a MetaEditor tab (close the tab), or it is the .ex5 of an" -ForegroundColor Red
    Write-Host "EA/indicator currently running in the terminal (remove it from its chart), then run this again." -ForegroundColor Red
    exit 1
}

if ($copied.Count -gt 0 -and -not $DryRun) {
    Write-Host ""
    Write-Host "Copied files still need compiling in MetaEditor (F7) before the terminal uses them."
}
Write-Host "Synchronization complete."
exit 0
