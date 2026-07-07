#!/bin/bash
# Keep Obsidian running on the mini so Obsidian Sync propagates Digest notes to the phone.
# Obsidian Sync only runs while the app is open; this watchdog relaunches it if it's closed.
# Single-instance safe: only calls `open` when no Obsidian process exists.
while true; do
  /usr/bin/pgrep -x Obsidian >/dev/null 2>&1 || /usr/bin/open -a Obsidian
  sleep 60
done
