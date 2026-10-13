#!/bin/bash
# Movie Splitter laptop helper: Mac installer.
# Nothing to download or pip install: it only needs Python 3. It checks your settings and GitHub,
# then makes the helper start by itself every time you log in (and restarts it if it stops).
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
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
  echo "Python 3.9 or newer isn't installed. Get it from https://www.python.org/downloads/ and run this again."
  exit 1
fi

if [ ! -f "$HERE/config.json" ]; then
  cp "$HERE/config.example.json" "$HERE/config.json"
  echo ""
  echo "STEP 1 of 2: fill in config.json (it opens now): your folders, github_token, ntfy_topic."
  echo "Save it, then run this installer again."
  open -e "$HERE/config.json"
  exit 0
fi

"$PY" "$HERE/agent.py" --setup

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
  <key>StandardErrorPath</key><string>$HERE/launchd-errors.log</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
echo ""
echo "STEP 2 of 2: the helper is running, and starts by itself whenever you log in."
echo "Now do the NEXT STEPS printed above (add the NTFY_TOPIC secret on GitHub); also in github-secrets.txt."
echo "Log file: $HERE/agent.log   Optional TikTok auto-posting:  python3 -m pip install composio"
[ -f "$HERE/github-secrets.txt" ] && open -e "$HERE/github-secrets.txt"
