#!/bin/bash
# Safely upgrade Obsidian on the mini WITHOUT the Gatekeeper "downloaded from internet" hang.
# brew re-downloads the app quarantined; a headless `open -a` can't approve the prompt and
# hangs. This pauses the keepalive, upgrades, strips the quarantine attr, then restores it.
set -uo pipefail
U=$(id -u)

echo "→ pausing Obsidian keep-alive"
launchctl bootout "gui/$U/com.boyang.obsidian" 2>/dev/null || true

echo "→ quitting Obsidian"
osascript -e 'quit app "Obsidian"' 2>/dev/null || true
sleep 3
pgrep -x Obsidian >/dev/null && { pkill -x Obsidian; sleep 2; } || true

echo "→ brew upgrade"
brew upgrade --cask obsidian || brew reinstall --cask obsidian

echo "→ stripping com.apple.quarantine (prevents the Gatekeeper prompt)"
xattr -dr com.apple.quarantine /Applications/Obsidian.app 2>/dev/null || true

echo "→ restoring keep-alive (relaunches Obsidian)"
launchctl bootstrap "gui/$U" "$HOME/Library/LaunchAgents/com.boyang.obsidian.plist" 2>/dev/null || true
sleep 5

echo "→ version: $(defaults read /Applications/Obsidian.app/Contents/Info.plist CFBundleShortVersionString)"
pgrep -x Obsidian >/dev/null && echo "Obsidian running ✓" || echo "keep-alive will relaunch within 60s"
