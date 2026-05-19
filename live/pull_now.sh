#!/usr/bin/env bash
# Manual on-demand pull: fetcher + ranker + gunicorn restart
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== On-demand pull at $(date '+%Y-%m-%d %H:%M:%S') ==="
python3 -m live.fetcher 2>&1
python3 -m live.ranker  2>&1
echo "✓ Snapshot and rankings updated"
