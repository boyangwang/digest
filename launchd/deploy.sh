#!/bin/bash
# Deploy the Digest bot's launchd services. Repo is the source of truth.
#   network.deardiary.digest       Telegram  (src/index.js)
#   network.deardiary.digest-lark  Lark      (src/lark-index.js, "Sleep Digest" app)
# Usage: bash launchd/deploy.sh            # both
#        bash launchd/deploy.sh lark       # only the Lark service
#        bash launchd/deploy.sh telegram   # only the Telegram service
set -euo pipefail

REPO="/Users/claw/Code/digest-bot"
DATA="$HOME/.local/share/digest"

case "${1:-all}" in
  all) LABELS=(network.deardiary.digest network.deardiary.digest-lark) ;;
  telegram) LABELS=(network.deardiary.digest) ;;
  lark) LABELS=(network.deardiary.digest-lark) ;;
  *) echo "usage: deploy.sh [all|telegram|lark]" >&2; exit 64 ;;
esac

echo "→ npm install (production deps)"
cd "$REPO" && npm install --omit=dev

echo "→ ensure durable log dir + private service logs"
mkdir -p "$DATA"
# launchd appends to an existing file and keeps its mode. Make the service logs 0600
# BEFORE the first load: an uncaught library error can print request URLs into them,
# and a Telegram URL carries the bot token.
for f in "$DATA/digest-service.log" "$DATA/digest-lark-service.log"; do
  touch "$f" && chmod 600 "$f"
done

for LABEL in "${LABELS[@]}"; do
  PLIST_SRC="$REPO/launchd/$LABEL.plist"
  PLIST_DST="$HOME/Library/LaunchAgents/$LABEL.plist"
  echo "→ install plist → $PLIST_DST"
  cp "$PLIST_SRC" "$PLIST_DST"
  echo "→ (re)load $LABEL"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DST"
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
done

echo "→ status"
sleep 3
for LABEL in "${LABELS[@]}"; do
  launchctl list | grep "$LABEL" || echo "NOT RUNNING: $LABEL"
done
echo "done."
echo "  telegram app log : tail -f $DATA/digest.log            (service net: $DATA/digest-service.log)"
echo "  lark app log     : tail -f $DATA/digest-lark.log       (service net: $DATA/digest-lark-service.log)"
echo "  A NON-EMPTY service log means something escaped the logger — read it."
