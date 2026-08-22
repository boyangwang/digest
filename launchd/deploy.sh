#!/bin/bash
# Deploy the Digest bot as a launchd service. Repo is the source of truth.
set -euo pipefail

REPO="/Users/claw/Code/digest-bot"
LABEL="network.deardiary.digest"
LOG="$HOME/.local/share/digest/digest.log"          # the app's own log (rotated, 0600)
SERVICE_LOG="$HOME/.local/share/digest/digest-service.log"  # launchd's stdout/stderr net
PLIST_SRC="$REPO/launchd/$LABEL.plist"
PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "→ npm install (production deps)"
cd "$REPO" && npm install --omit=dev

echo "→ ensure durable log dir"
mkdir -p "$(dirname "$LOG")"

echo "→ install plist → $PLIST_DST"
cp "$PLIST_SRC" "$PLIST_DST"

echo "→ (re)load service"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo "→ status"
sleep 2
launchctl list | grep "$LABEL" || echo "NOT RUNNING — check $SERVICE_LOG"
echo "done."
echo "  app log     : tail -f $LOG          (rotated, 0600 — the one to read)"
echo "  service log : tail -f $SERVICE_LOG  (launchd's net; NON-EMPTY means something escaped the logger)"
