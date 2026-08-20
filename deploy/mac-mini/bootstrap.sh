#!/usr/bin/env bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT"

echo "== GEPO Mac mini bootstrap =="
echo "repo: $ROOT"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "ERROR: this bootstrap is intended for macOS." >&2
  exit 1
fi

mkdir -p live/logs live/notifications live/ranked live/frozen live/intraday_picks live/data

if ! xcode-select -p >/dev/null 2>&1; then
  echo "Xcode Command Line Tools are missing."
  echo "Run: xcode-select --install"
  exit 1
fi

for cmd in git ssh rsync python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: missing required command: $cmd" >&2
    exit 1
  fi
done

install_for_python() {
  py="$1"
  echo ""
  echo "Installing Python dependencies for: $py"
  "$py" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$py" -m pip install --user --upgrade pip
  "$py" -m pip install --user -r requirements.txt -r live/requirements.txt
}

PY_PATH="$(command -v python3)"
install_for_python "$PY_PATH"

if [ -x /usr/bin/python3 ] && [ "$PY_PATH" != "/usr/bin/python3" ]; then
  install_for_python /usr/bin/python3
fi

if [ ! -f "$HOME/.gepo_env" ]; then
  echo ""
  echo "Creating $HOME/.gepo_env from template."
  cp deploy/mac-mini/gepo.env.example "$HOME/.gepo_env"
  chmod 600 "$HOME/.gepo_env"
  echo "Edit $HOME/.gepo_env before running production jobs."
fi

echo ""
echo "Bootstrap complete. Next:"
echo "  1. Edit ~/.gepo_env"
echo "  2. Log into IB Gateway"
echo "  3. Run: bash deploy/mac-mini/smoke_test.sh"
echo "  4. Run: bash deploy/mac-mini/smoke_test.sh --ibkr"
