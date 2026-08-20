#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

RUN_IBKR=0
RUN_FULL=0
for arg in "$@"; do
  case "$arg" in
    --ibkr) RUN_IBKR=1 ;;
    --full) RUN_FULL=1; RUN_IBKR=1 ;;
    *)
      echo "usage: $0 [--ibkr] [--full]" >&2
      exit 2
      ;;
  esac
done

[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

echo "== GEPO Mac mini smoke test =="
echo "repo: $ROOT"
echo "date: $(date '+%Y-%m-%d %H:%M:%S %Z')"

check_python() {
  py="$1"
  [ -x "$py" ] || return 0
  echo ""
  echo "-- Python import check: $py"
  "$py" - <<'PY'
import importlib
mods = ["pandas", "numpy", "pyarrow", "flask", "ib_insync"]
missing = []
for name in mods:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{name}: {exc}")
if missing:
    raise SystemExit("missing imports:\n" + "\n".join(missing))
print("imports ok")
PY
}

check_python "$(command -v python3)"
if [ -x /usr/bin/python3 ] && [ "$(command -v python3)" != "/usr/bin/python3" ]; then
  check_python /usr/bin/python3
fi

echo ""
echo "-- Repo files"
for path in \
  live/live_config.py \
  live/pull_now_parallel.sh \
  live/upload_to_mya.sh \
  live/ranked \
  live/frozen \
  live/intraday_picks \
  live/data
do
  if [ ! -e "$path" ]; then
    echo "missing: $path" >&2
    exit 1
  fi
  echo "ok: $path"
done

echo ""
echo "-- Config"
if [ -f "$HOME/.gepo_env" ]; then
  echo "ok: ~/.gepo_env exists"
else
  echo "WARN: ~/.gepo_env missing"
fi
echo "IB_PORT=${IB_PORT:-4001}"
echo "MYA_SSH_HOST=${MYA_SSH_HOST:-unset}"
echo "MYA_REMOTE_BASE=${MYA_REMOTE_BASE:-unset}"

echo ""
echo "-- Health check forced alert"
python3 -m live.health_check --force

if [ "$RUN_IBKR" -eq 1 ]; then
  echo ""
  echo "-- IB Gateway socket"
  IB_PORT="${IB_PORT:-4001}"
  if nc -z 127.0.0.1 "$IB_PORT"; then
    echo "ok: 127.0.0.1:$IB_PORT is listening"
  else
    echo "ERROR: IB Gateway is not listening on 127.0.0.1:$IB_PORT" >&2
    exit 1
  fi

  echo ""
  echo "-- SPY intraday fetch"
  python3 -m live.fetch_spy_intraday
fi

if [ "$RUN_FULL" -eq 1 ]; then
  echo ""
  echo "-- Full live cron pipeline"
  bash live/cron_parallel.sh
fi

echo ""
echo "Smoke test complete."
