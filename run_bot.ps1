# MR-70 V3 paper-study bot. Signals only -- this starts nothing that can trade.
#
# Keeps the bot running across crashes, Wi-Fi drops and laptop sleep. The bot
# itself already survives a bad cycle; this loop covers the cases it cannot,
# such as the Python process being killed outright.
#
#   .\run_bot.ps1              start it
#   Ctrl+C                     stop it
#   .\run_bot.ps1 -TestMessage send one Telegram message and exit

param([switch]$TestMessage)

$ErrorActionPreference = "Continue"
$root   = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root ".venv\Scripts\python.exe"
$script = Join-Path $root "mr70\study_bot.py"
$logdir = Join-Path $root "logs"

if (-not (Test-Path $python)) { Write-Error "No venv at $python"; exit 1 }
if (-not (Test-Path $script)) { Write-Error "No bot at $script";  exit 1 }
New-Item -ItemType Directory -Force -Path $logdir | Out-Null

if (-not $env:TELEGRAM_BOT_TOKEN -or -not $env:TELEGRAM_CHAT_ID) {
    Write-Warning "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set - alerts will be skipped."
    Write-Warning "See STUDY_BOT_README.md for how to set them permanently."
}

if ($TestMessage) { & $python $script --test-message; exit $LASTEXITCODE }

$attempt = 0
while ($true) {
    $attempt++
    $log = Join-Path $logdir ("study_bot_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
    "=== start attempt $attempt at $(Get-Date -Format u) ===" | Tee-Object -FilePath $log -Append
    & $python $script 2>&1 | Tee-Object -FilePath $log -Append

    if ($LASTEXITCODE -eq 0) { "bot exited cleanly" | Tee-Object -FilePath $log -Append; break }

    # Back off a little so a persistent failure does not spin the CPU, but stay
    # short enough that a laptop waking from sleep is watching again within a bar.
    $wait = [Math]::Min(60, 5 * $attempt)
    "exited with $LASTEXITCODE - restarting in $wait s" | Tee-Object -FilePath $log -Append
    Start-Sleep -Seconds $wait
}
