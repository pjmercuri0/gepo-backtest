#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TEMPLATE="$SCRIPT_DIR/crontab.template"
BEGIN="# BEGIN GEPO MAC MINI"
END="# END GEPO MAC MINI"
DRY_RUN=0

case "${1:-}" in
  "")
    ;;
  --dry-run)
    DRY_RUN=1
    ;;
  *)
    echo "usage: $0 [--dry-run]" >&2
    exit 2
    ;;
esac

if [ ! -f "$TEMPLATE" ]; then
  echo "ERROR: missing template: $TEMPLATE" >&2
  exit 1
fi

mkdir -p "$ROOT/live/logs"

CURRENT="$(mktemp)"
FILTERED="$(mktemp)"
RENDERED="$(mktemp)"
NEWCRON="$(mktemp)"
trap 'rm -f "$CURRENT" "$FILTERED" "$RENDERED" "$NEWCRON"' EXIT

crontab -l > "$CURRENT" 2>/dev/null || true
cp "$CURRENT" "$ROOT/live/logs/crontab.backup.$(date '+%Y%m%d-%H%M%S').txt"

awk -v begin="$BEGIN" -v end="$END" '
  $0 == begin { skip=1; next }
  $0 == end { skip=0; next }
  /gepo-backtest\/live\/cron_[^ ]*\.sh/ { next }
  !skip { print }
' "$CURRENT" > "$FILTERED"

sed "s#__REPO__#$ROOT#g" "$TEMPLATE" > "$RENDERED"

cat "$FILTERED" > "$NEWCRON"
if [ -s "$NEWCRON" ] && [ "$(tail -c 1 "$NEWCRON" | wc -l | tr -d ' ')" = "0" ]; then
  printf "\n" >> "$NEWCRON"
fi
printf "\n" >> "$NEWCRON"
cat "$RENDERED" >> "$NEWCRON"

echo "Installing GEPO crontab block for repo:"
echo "  $ROOT"
echo ""
cat "$RENDERED"
echo ""

if [ "$DRY_RUN" -eq 1 ]; then
  echo "Dry run only. Full resulting crontab would be:"
  echo ""
  cat "$NEWCRON"
  exit 0
fi

crontab "$NEWCRON"
echo "Installed. Verify with: crontab -l"
