# Movie Splitter laptop helper: Windows installer.
# Nothing to download or pip install: it only needs Python. It checks your settings and GitHub,
# then makes the helper start by itself (hidden, no window) every time you log in.
#
# How to run: right-click this file > "Run with PowerShell".
#   If Windows refuses: open PowerShell in this folder and run
#   powershell -ExecutionPolicy Bypass -File install_windows.ps1
# To remove it later:  powershell -ExecutionPolicy Bypass -File install_windows.ps1 -Uninstall

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"
$TaskName = "Movie Splitter laptop helper"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Pause-Close { Read-Host "Press Enter to close" | Out-Null }

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed. The helper won't start at login any more."
    exit 0
}

# 1. Find Python: the "py" launcher first, then "python". (The "WindowsApps" python is only the
#    Microsoft Store shortcut, not a real install.) We ask Python itself where it lives.
$exe = $null
$ErrorActionPreference = "Continue"
$probe = "import sys; print(sys.executable if sys.version_info >= (3, 9) else '')"
foreach ($name in @("py", "python")) {
    $c = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $c -or $c.Source -like "*WindowsApps*") { continue }
    if ($name -eq "py") { $out = & $c.Source -3 -c $probe 2>$null | Select-Object -Last 1 }
    else { $out = & $c.Source -c $probe 2>$null | Select-Object -Last 1 }
    if ($out -and (Test-Path ([string]$out).Trim())) { $exe = ([string]$out).Trim(); break }
}
$ErrorActionPreference = "Stop"
if (-not $exe) {
    Write-Host "Python 3.9 or newer isn't installed." -ForegroundColor Red
    Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
    Pause-Close; exit 1
}
$exew = Join-Path (Split-Path $exe) "pythonw.exe"      # same Python, but runs without a window
if (-not (Test-Path $exew)) { $exew = $exe }
Write-Host "Using Python: $exe"

# 2. Settings file
$cfg = Join-Path $Here "config.json"
if (-not (Test-Path $cfg)) {
    Copy-Item (Join-Path $Here "config.example.json") $cfg
    Write-Host ""
    Write-Host "STEP 1 of 2: fill in config.json (Notepad opens now):" -ForegroundColor Cyan
    Write-Host "  - watch_folder, output_folder, download_folder (use / not \ in paths)"
    Write-Host "  - github_token (Contents + Actions: Read and write on movie-splitter-v2)"
    Write-Host "  - ntfy_topic (your ntfy name, or leave empty to get a new one)"
    Write-Host "Save it, close Notepad, then run this installer again."
    Start-Process notepad.exe $cfg
    Pause-Close; exit 0
}

# 3. Check GitHub; writes github-secrets.txt with the one secret to add on GitHub
$ErrorActionPreference = "Continue"
& $exe (Join-Path $Here "agent.py") --setup
$ErrorActionPreference = "Stop"

# 4. Start at login, hidden, restart if it ever stops
$agent = Join-Path $Here "agent.py"
$action = New-ScheduledTaskAction -Execute $exew -Argument "`"$agent`"" -WorkingDirectory $Here
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 5) `
    -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Sends movies to GitHub and fetches the finished parts (Movie Splitter)" -Force | Out-Null
Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "STEP 2 of 2: the helper is running, and starts by itself whenever you log in." -ForegroundColor Green
Write-Host "Now do the NEXT STEPS printed above (add the NTFY_TOPIC secret on GitHub)."
Write-Host "They're also in github-secrets.txt, opening in Notepad."
Write-Host "Log file: $Here\agent.log   Optional TikTok auto-posting:  py -m pip install composio"
$secrets = Join-Path $Here "github-secrets.txt"
if (Test-Path $secrets) { Start-Process notepad.exe $secrets }
Pause-Close
