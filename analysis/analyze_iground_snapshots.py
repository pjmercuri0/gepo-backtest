"""Analyze intraday snapshot performance by iGROUND score.

Reads live/intraday_picks/YYYY-MM-DD.json, where each scan stores the
threshold-qualified top-5 picks captured by live.snapshot_picks.

Definitions:
- iGROUND: the intraday snapshot pick's stored GROUND score.
- settled pick: archived pick with non-null pnl and enough fields to compute
  defined-risk capital.
- P&L %: capital-weighted return on defined risk,
  sum(pnl) / sum((spread_width - entry_credit) * 100).

This script is read-only.
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PICKS_DIR = ROOT / "live" / "intraday_picks"


def _load_rows():
    rows = []
    total = open_count = 0
    dates = []
    for fp in sorted(PICKS_DIR.glob("*.json")):
        try:
            payload = json.loads(fp.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        day = payload.get("date") or fp.stem
        dates.append(day)
        for scan in payload.get("scans", []):
            for pick in scan.get("picks", []):
                ground = pick.get("GROUND")
                if ground is None:
                    continue
                total += 1
                pnl = pick.get("pnl")
                if pnl is None:
                    open_count += 1
                    continue
                width = pick.get("spread_width")
                if width is None:
                    try:
                        width = float(pick.get("net_credit")) + float(pick.get("max_loss"))
                    except (TypeError, ValueError):
                        continue
                entry_credit = pick.get("entry_credit")
                if entry_credit is None:
                    continue
                risk = (float(width) - float(entry_credit)) * 100.0
                if risk <= 0:
                    continue
                rows.append(
                    {
                        "date": day,
                        "hhmm": scan.get("hhmm"),
                        "ticker": pick.get("ticker"),
                        "spread_type": pick.get("spread_type"),
                        "iGROUND": float(ground),
                        "pnl": float(pnl),
                        "risk": risk,
                        "ret_pct": float(pnl) / risk * 100.0,
                        "win": float(pnl) > 0,
                        "outcome": pick.get("outcome"),
                    }
                )
    return rows, total, open_count, dates


def _stats(rows):
    if not rows:
        return None
    pnl = sum(r["pnl"] for r in rows)
    risk = sum(r["risk"] for r in rows)
    rets = [r["ret_pct"] for r in rows]
    return {
        "n": len(rows),
        "win_pct": 100.0 * sum(r["win"] for r in rows) / len(rows),
        "pnl": pnl,
        "risk": risk,
        "capital_pnl_pct": 100.0 * pnl / risk if risk else math.nan,
        "avg_trade_pnl_pct": sum(rets) / len(rets),
        "median_trade_pnl_pct": statistics.median(rets),
        "avg_pnl": pnl / len(rows),
        "avg_iground": sum(r["iGROUND"] for r in rows) / len(rows),
    }


def _print_row(label, s):
    print(
        f"{label:<13} {s['n']:>5d} "
        f"{s['win_pct']:>6.1f} "
        f"{s['pnl']:>10.1f} "
        f"{s['risk']:>10.1f} "
        f"{s['capital_pnl_pct']:>8.1f} "
        f"{s['avg_trade_pnl_pct']:>8.1f} "
        f"{s['median_trade_pnl_pct']:>8.1f} "
        f"{s['avg_pnl']:>8.2f} "
        f"{s['avg_iground']:>10.6f}"
    )


def _rank(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        rank = (i + 1 + j) / 2.0
        for k in range(i, j):
            out[order[k]] = rank
        i = j
    return out


def _pearson(a, b):
    if len(a) < 2:
        return math.nan
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return math.nan
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


def _spearman(a, b):
    return _pearson(_rank(a), _rank(b))


def main():
    rows, total, open_count, dates = _load_rows()
    if not rows:
        raise SystemExit("No settled intraday snapshot picks found.")

    settled_dates = [r["date"] for r in rows]
    print("iGROUND snapshot analysis")
    print(f"source: {PICKS_DIR.relative_to(ROOT)}")
    print(f"archive date range: {min(dates)} to {max(dates)}")
    print(f"settled date range: {min(settled_dates)} to {max(settled_dates)}")
    print(f"all captured picks: {total}")
    print(f"settled analyzed picks: {len(rows)}")
    print(f"open/unsettled excluded: {open_count}")
    print()
    print(
        "bucket             n   win%        pnl       risk   capP&L% "
        " avgRet%   medRet%    avg$   avg_iGROUND"
    )
    _print_row("ALL", _stats(rows))

    print("\nFixed iGROUND bins")
    for lo, hi in [
        (0.05, 0.075),
        (0.075, 0.10),
        (0.10, 0.15),
        (0.15, 0.20),
        (0.20, 0.30),
        (0.30, 0.50),
        (0.50, math.inf),
    ]:
        bucket = [r for r in rows if lo <= r["iGROUND"] < hi]
        if bucket:
            label = f"{lo:.3f}-{hi:.3f}" if hi < math.inf else "0.500+"
            _print_row(label, _stats(bucket))

    print("\nThreshold sweep: keep picks with iGROUND >= threshold")
    for thr in [0.05, 0.06, 0.07, 0.075, 0.08, 0.09, 0.10, 0.125, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50]:
        bucket = [r for r in rows if r["iGROUND"] >= thr]
        if bucket:
            _print_row(f">={thr:.3f}", _stats(bucket))

    print("\nDeciles by iGROUND")
    ordered = sorted(rows, key=lambda r: r["iGROUND"])
    for i in range(10):
        a = math.floor(i * len(ordered) / 10)
        z = math.floor((i + 1) * len(ordered) / 10)
        bucket = ordered[a:z]
        s = _stats(bucket)
        label = f"D{i + 1} {bucket[0]['iGROUND']:.3f}-{bucket[-1]['iGROUND']:.3f}"
        _print_row(label, s)

    g = [r["iGROUND"] for r in rows]
    ret = [r["ret_pct"] for r in rows]
    win = [1.0 if r["win"] else 0.0 for r in rows]
    print("\nCorrelation")
    print(f"pearson(iGROUND, P&L%)  = {_pearson(g, ret):.4f}")
    print(f"spearman(iGROUND, P&L%) = {_spearman(g, ret):.4f}")
    print(f"pearson(iGROUND, win)   = {_pearson(g, win):.4f}")
    print(f"spearman(iGROUND, win)  = {_spearman(g, win):.4f}")


if __name__ == "__main__":
    main()
