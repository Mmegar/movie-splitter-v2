# Opens what Movie Splitter needs each time you log in to this laptop: Claude, FilmCraft, RustDesk,
# the To edit folder and Downloads, and makes sure FFmpeg is installed. Apps already open are left alone.
# The laptop helper puts a small "Movie Splitter apps" file in your Startup folder that runs this.
# Turn it off: set "startup_apps": false in laptop\config.json. Add more: "startup_apps": ["Notepad", "C:\\path\\app.exe"].
$ErrorActionPreference = "SilentlyContinue"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogFile = Join-Path $Here "startup_apps.log"
function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $m" | Out-File -FilePath $LogFile -Append -Encoding utf8 }

$cfg = @{}
try { $cfg = Get-Content (Join-Path $Here "config.json") -Raw | ConvertFrom-Json } catch { }
if ($cfg.startup_apps -eq $false) { Log "Turned off in config.json"; exit 0 }

Start-Sleep -Seconds 15          # let Windows and OneDrive settle after login
Log "Opening the Movie Splitter apps"

$menus = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs", "$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
           [Environment]::GetFolderPath("Desktop"), "$env:PUBLIC\Desktop")

function Open-App($label, $processes, $paths, $shortcut) {
    foreach ($p in $processes) { if (Get-Process -Name $p -ErrorAction SilentlyContinue) { Log "$label already open"; return } }
    foreach ($p in $paths) {
        $hit = Get-Item $p -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { Start-Process -FilePath $hit.FullName; Log "Opened $label ($($hit.FullName))"; return }
    }
    foreach ($m in $menus) {
        $lnk = Get-ChildItem -Path $m -Recurse -Filter "$shortcut.lnk" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($lnk) { Start-Process -FilePath $lnk.FullName; Log "Opened $label ($($lnk.FullName))"; return }
    }
    Log "Couldn't find $label (install it, or add its .exe path to startup_apps in config.json)"
}

Open-App "Claude" @("claude") @("$env:LOCALAPPDATA\AnthropicClaude\claude.exe", "$env:LOCALAPPDATA\Programs\Claude\Claude.exe") "Claude"
Open-App "FilmCraft" @("FilmCraft*") @("$env:ProgramFiles\FilmCraft*\FilmCraft*.exe", "$env:LOCALAPPDATA\Programs\FilmCraft*\FilmCraft*.exe") "FilmCraft*"
Open-App "RustDesk" @("rustdesk") @("$env:ProgramFiles\RustDesk\rustdesk.exe") "RustDesk"
foreach ($extra in @($cfg.startup_apps | Where-Object { $_ -is [string] })) {
    if (Test-Path $extra) { Start-Process -FilePath $extra; Log "Opened $extra" } else { Open-App $extra @($extra) @() "$extra*" }
}

# FFmpeg: needed to cut movies here. Installs it once if it's missing.
$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ff) { $ff = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 }
if ($ff) { Log "FFmpeg is installed" }
else {
    Log "FFmpeg missing: installing it with winget"
    Start-Process -FilePath "winget" -ArgumentList "install --id Gyan.FFmpeg -e --silent --accept-package-agreements --accept-source-agreements" -WindowStyle Hidden -Wait
}

# Folders: To edit (movies waiting to be cut) and Downloads.
$watch = if ($cfg.watch_folder) { [Environment]::ExpandEnvironmentVariables($cfg.watch_folder) } else { $null }
$downloads = (New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path
if (-not $downloads) { $downloads = Join-Path $env:USERPROFILE "Downloads" }
foreach ($f in @($watch, $downloads)) {
    if ($f -and (Test-Path $f)) { Start-Process explorer.exe -ArgumentList "`"$f`""; Log "Opened folder $f" }
}
