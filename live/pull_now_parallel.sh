#!/usr/bin/env bash
# Parallel on-demand pull: 5 fetchers in parallel, then ranker
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TICKERS=(
    "AAPL" "MSFT" "AMZN" "GOOGL" "TSLA" "UNH" "JNJ" "XOM" "JPM" "V"
    "PG" "MA" "NVDA" "HD" "CVX" "MRK" "ABBV" "PEP" "KO" "AVGO"
    "PFE" "COST" "TMO" "WMT" "MCD" "ACN" "ABT" "DHR" "NEE" "LIN"
    "BMY" "ORCL" "TXN" "PM" "UPS" "MS" "RTX" "AMGN" "HON" "QCOM"
    "LOW" "SBUX" "GS" "BAC" "BLK" "MDT" "GILD" "AXP" "ISRG" "VRTX"
    "ADI" "REGN" "SYK" "ZTS" "CB" "BDX" "ADP" "MMC" "SCHW" "TJX"
    "CSX" "SO" "DUK" "ITW" "CME" "CL" "EOG" "USB" "EMR" "MO"
    "FCX" "AON" "PNC" "NSC" "CCI" "WM" "APD" "F" "GM" "GE"
    "BA" "CAT" "DE" "MMM" "IBM" "INTC" "CSCO" "VZ" "T" "DIS"
    "NFLX" "CRM" "NOW" "PYPL"
)

# Split into 10 groups of 10
GROUP_SIZE=10
NUM_GROUPS=10

echo "=== Parallel pull at $(date '+%Y-%m-%d %H:%M:%S') ==="
echo "Starting $NUM_GROUPS parallel fetchers..."

# Spawn 10 fetcher processes with unique client IDs
for g in $(seq 0 $((NUM_GROUPS - 1))); do
    START=$((g * GROUP_SIZE))
    END=$((START + GROUP_SIZE))
    GROUP_TICKERS=("${TICKERS[@]:$START:$GROUP_SIZE}")
    CLIENT_ID=$((100 + g))

    (
        echo "  [Group $((g+1))/$NUM_GROUPS] Fetching ${#GROUP_TICKERS[@]} tickers..."
        python3 -m live.fetcher --tickers "${GROUP_TICKERS[@]}" --client-id $CLIENT_ID 2>&1 | sed "s/^/  [G$((g+1))] /"
    ) &
done

# Wait for all fetchers to complete
wait
echo "✓ All fetchers completed"

# Run ranker once on the latest combined snapshot
echo "  Ranking all snapshots..."
python3 -m live.ranker 2>&1 | sed "s/^/  [Ranker] /"
echo "✓ Snapshot and rankings updated"
