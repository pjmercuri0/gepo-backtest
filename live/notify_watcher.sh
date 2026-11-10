#!/usr/bin/env bash
# Watches live/notifications/ for new JSON files, forwards to Telegram, de-dups health alerts.
# Lives on Mya (cron, every minute 9-16 ET Mon-Fri); this repo copy was pulled from her 2026-11-10 and is
# deployed by rsync like the other live/ files. Site-neutral: it forwards whatever lands in notifications/.
set -euo pipefail

DIR="/opt/vito-managed/data/user/apps/gepo-backtest/live/notifications"
STATE="$DIR/.processed"
DEDUP="$DIR/.last_health_alert"
DEDUP_WINDOW=1800  # 30 min in seconds

# Telegram config — read bot token from Vito secrets
BOT_TOKEN=$(python3 -c "import json; print(json.load(open('/opt/vito-managed/data/user/secrets.json'))['TELEGRAM_BOT_TOKEN'])")
CHAT_ID="538663201"

touch "$STATE"

# WIN alerts (user 2026-11-10: "add an alert for the green highlights in actuals ... 90% or whatever we
# had it light up green"): ask the DEBIT site which Actuals rows it paints green right now and write a
# notification file for each NEW one (live/win_alert.py, ledger .win_alerted.json); the loop below
# forwards it to Telegram in this same run. Soft-fail: a hiccup there must not stop the forwarding.
python3 /opt/vito-managed/data/user/apps/gepo-backtest/live/win_alert.py \
  >> /opt/vito-managed/data/user/apps/gepo-backtest/live/logs/win_alert.log 2>&1 || true

for f in "$DIR"/*.json; do
  [ -f "$f" ] || continue
  fname=$(basename "$f")

  # Skip already processed
  grep -qxF "$fname" "$STATE" && continue

  # Parse type and message
  type=$(python3 -c "import json; print(json.load(open('$f')).get('type',''))" 2>/dev/null || echo "")
  message=$(python3 -c "import json; print(json.load(open('$f')).get('message',''))" 2>/dev/null || echo "")

  if [ -z "$message" ]; then
    echo "$fname" >> "$STATE"
    continue
  fi

  # Health files are edge-triggered market-hours alerts. If an old health file
  # gets re-synced later, do not notify again outside the original session.
  if [ "$type" = "health-alert" ]; then
    # Market holiday mute: Labor Day 2026. SPY/IBKR stale alerts are noise when market is closed.
    today=$(TZ=America/New_York date +%Y%m%d)
    if [ "$today" = "20260907" ]; then
      echo "$fname" >> "$STATE"
      continue
    fi

    stamp=${fname#health-}
    stamp=${stamp%.json}
    file_day=${stamp%-*}
    file_hms=${stamp#*-}
    file_hm=${file_hms:0:4}
    today=$(TZ=America/New_York date +%Y%m%d)
    if [ "$file_day" != "$today" ] || [[ "$file_hm" < "0930" ]] || [[ "$file_hm" > "1605" ]]; then
      echo "$fname" >> "$STATE"
      continue
    fi
  fi

  # De-dup health alerts: skip if we sent one within DEDUP_WINDOW
  if [ "$type" = "health-alert" ] && [ -f "$DEDUP" ]; then
    last=$(cat "$DEDUP")
    now=$(date +%s)
    if [ $((now - last)) -lt $DEDUP_WINDOW ]; then
      echo "$fname" >> "$STATE"
      continue
    fi
  fi

  # Send to Telegram
  curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d chat_id="$CHAT_ID" \
    -d text="$message" \
    -d parse_mode="HTML" > /dev/null 2>&1

  # Mark processed + update dedup timestamp
  echo "$fname" >> "$STATE"
  if [ "$type" = "health-alert" ]; then
    date +%s > "$DEDUP"
  fi
done
