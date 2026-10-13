# Movie Splitter laptop helper: Windows installer.
# Makes the helper start by itself (hidden, no window) every time you log in.
#
# How to run: right-click this file > "Run with PowerShell".
# To remove it later:  powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Uninstall

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$TaskName = "Movie Splitter laptop helper"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed. The helper won't start at login any more."
    exit 0
}

# 1. Python (pythonw.exe runs without a window)
$py = Get-Command pythonw.exe -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "Python isn't installed (or wasn't added to PATH)." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ and tick 'Add python.exe to PATH', then run this again."
    Read-Host "Press Enter to close"
    exit 1
}

# 2. rclone
if (-not (Get-Command rclone.exe -ErrorAction SilentlyContinue)) {
    Write-Host "Note: rclone wasn't found on PATH. Either install it (see README) or put the full path to rclone.exe in config.json (rclone_path)." -ForegroundColor Yellow
}

# 3. Settings file
$cfg = Join-Path $Here "config.json"
if (-not (Test-Path $cfg)) {
    Copy-Item (Join-Path $Here "config.example.json") $cfg
    Write-Host "Made config.json. Fill it in (Notepad opens now), save it, then run this installer again."
    Start-Process notepad.exe $cfg
    Read-Host "Press Enter to close"
    exit 0
}

# 4. Start at login, hidden, restart if it ever stops
$agent = Join-Path $Here "agent.py"
$action = New-ScheduledTaskAction -Execute $py.Source -Argument "`"$agent`"" -WorkingDirectory $Here
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Uploads movies to Google Drive for Movie Splitter" -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Done! The helper is running now and will start by itself whenever you log in." -ForegroundColor Green
Write-Host "Its log is in $Here\agent.log"
Read-Host "Press Enter to close"
