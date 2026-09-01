#!/usr/bin/env bash
# Shared environment for cron/launchd wrappers. Cron does not inherit the
# interactive shell's venv or PATH, so prefer the repo venv explicitly.

[ -f "$HOME/.gepo_env" ] && . "$HOME/.gepo_env"

# Cron/launchd can inherit a stale DEVELOPER_DIR. If it points at a removed
# full Xcode app, Apple's python can fail via xcrun before our code runs.
unset DEVELOPER_DIR

GEPO_REPO_ROOT="${GEPO_REPO_ROOT:-$(pwd)}"

if [ -x "$GEPO_REPO_ROOT/.venv/bin/python3" ]; then
  export VIRTUAL_ENV="$GEPO_REPO_ROOT/.venv"
  export PATH="$VIRTUAL_ENV/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  export GEPO_PYTHON="$VIRTUAL_ENV/bin/python3"
else
  export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"
  export GEPO_PYTHON="${GEPO_PYTHON:-$(command -v python3)}"
fi
