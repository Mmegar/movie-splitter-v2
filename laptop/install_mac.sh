#!/bin/bash
# Movie Splitter laptop helper: Mac installer.
# Makes the helper start by itself every time you log in (and restarts it if it stops).
#
# How to run: open Terminal, type   bash   and a space, drag this file into the window, press Enter.
# To remove it later:  bash install_mac.sh --uninstall

set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.moviesplitter.laptop-helper"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$1" = "--uninstall" ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed. The helper won't start at login any more."
  exit 0
fi

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "Python 3 isn't installed. Get it from https://www.python.org/downloads/ and run this again."
  exit 1
fi
RCLONE="$(command -v rclone || true)"
if [ -z "$RCLONE" ]; then
  echo "Note: rclone wasn't found. Install it (see README) or put its full path in config.json (rclone_path)."
fi

if [ ! -f "$HERE/config.json" ]; then
  cp "$HERE/config.example.json" "$HERE/config.json"
  echo "Made config.json. Fill it in (it opens now), save it, then run this installer again."
  open -e "$HERE/config.json"
  exit 0
fi

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>$HERE/agent.py</string></array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardErrorPath</key><string>$HERE/launchd-errors.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
echo "Done! The helper is running now and will start by itself whenever you log in."
echo "Its log is in $HERE/agent.log"
