"""
Patch yield-based metrics into existing weekly_report-{suffix}.html files.
Reads only the all_trades-{suffix}.csv — no backtest re-runs, no overlay,
no cache thrashing. Pure CSV math + HTML stat-tile rewrite.

For each variant, computes per-slippage:
  yield = total_pnl / total_wagered × 100   (sportsbook-style ROI)
  total_wagered = Σ contracts × (max_loss + 2·slip) × 100
  total_pnl     = Σ contracts × pnl_per_contract × 100  −  Σ contracts × 2·slip × 100

Then rewrites the .stats div + range chips in the HTML.
"""
import math
import os
import re

import pandas as pd

import config

# Use the shared OUTPUT_DIR from config (one level up from backtest_daily/)
# rather than backtest_daily/output/, which doesn't exist.
OUTPUT_DIR = config.OUTPUT_DIR

# Variants to patch
VARIANTS = ["daily-qty1", "daily-qty2", "daily-qtyx",
            "daily-qty1-oot", "daily-qty2-oot", "daily-qtyx-oot"]
SLIPPAGES = [0.00, 0.01, 0.02, 0.03]

# Palette
GREEN = "#1D9E75"
RED   = "#E24B4A"


def stats_for_csv(trades_df, slip):
    """Compute per-slippage stats from raw trade rows."""
    haircut_per_share = 2.0 * float(slip)
    contracts = trades_df["contracts"].astype(float)
    max_loss  = trades_df["max_loss"].astype(float) + haircut_per_share
    pnl_per_contract = trades_df["pnl_per_contract"].astype(float)

    wagered_per_trade = contracts * max_loss * 100.0
    pnl_per_trade     = (
        contracts * pnl_per_contract * 100.0
        - contracts * haircut_per_share * 100.0
    )
    total_wagered = float(wagered_per_trade.sum())
    total_pnl     = float(pnl_per_trade.sum())
    yield_pct     = (total_pnl / total_wagered * 100.0) if total_wagered > 0 else 0.0

    # Sharpe / DD from per-week aggregation
    t = trades_df.copy()
    t["pnl"] = pnl_per_trade
    t["entry_date"] = pd.to_datetime(t["entry_date"])
    weekly = t.groupby("entry_date")["pnl"].sum()
    starting = 10000.0
    bankroll = starting + weekly.cumsum()
    rmx = bankroll.cummax()
    dd  = ((bankroll - rmx) / rmx).min() * 100 if len(bankroll) > 0 else 0
    final = float(bankroll.iloc[-1]) if len(bankroll) > 0 else starting

    # Daily-cadence: 'weekly' here is actually grouped by entry_date (one row per day).
    weekly_ret = weekly / starting
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(252)) \
        if weekly_ret.std() > 0 else 0

    n_trades = len(trades_df)
    n_wins   = int((trades_df["result"] == "WIN").sum())
    win_rate = n_wins / n_trades * 100 if n_trades > 0 else 0

    return dict(
        yield_pct=yield_pct, total_wagered=total_wagered, total_pnl=total_pnl,
        sharpe=sharpe, dd=dd, win_rate=win_rate, final=final,
        n_trades=n_trades, n_wins=n_wins,
    )


def _pair(label, best_val, canon_val, color_best="", color_canon=""):
    return (f'<div class="stat">'
            f'<div class="stat-label">{label}'
            f'<span class="stat-tag">0¢ / 3¢</span></div>'
            f'<div class="stat-value-best {color_best}">{best_val}</div>'
            f'<div class="stat-value-canon {color_canon}">{canon_val}</div>'
            f'</div>')


def build_stats_html(stats_dict):
    s_best  = stats_dict[0.00]
    s_canon = stats_dict[0.03]
    yield_color = "pos" if s_best["yield_pct"] >= 0 else "neg"
    final_color = "pos" if s_best["final"] >= 10000 else "neg"

    starting_tile = (
        '<div class="stat">'
        '<div class="stat-label">starting bankroll</div>'
        '<div class="stat-value">$10,000</div>'
        '</div>'
    )

    return (
        '<div class="stats">'
        + starting_tile
        + _pair("final bankroll",
                f"${s_best['final']:,.0f}",
                f"${s_canon['final']:,.0f}",
                color_best=final_color, color_canon="")
        + _pair("yield (P&L / wagered)",
                f"{s_best['yield_pct']:.2f}%",
                f"{s_canon['yield_pct']:.2f}%",
                color_best=yield_color, color_canon="")
        + _pair("total wagered",
                f"${s_best['total_wagered']/1000:,.1f}k",
                f"${s_canon['total_wagered']/1000:,.1f}k")
        + _pair("win rate",
                f"{s_best['win_rate']:.1f}%",
                f"{s_canon['win_rate']:.1f}%",
                color_best="pos", color_canon="")
        + _pair("sharpe ratio",
                f"{s_best['sharpe']:.2f}",
                f"{s_canon['sharpe']:.2f}")
        + _pair("max drawdown",
                f"{s_best['dd']:.1f}%",
                f"{s_canon['dd']:.1f}%",
                color_best="neg", color_canon="")
        + '</div>\n'
    )


def build_range_chips(stats_dict):
    yields  = [s["yield_pct"]    for s in stats_dict.values()]
    sharpes = [s["sharpe"]       for s in stats_dict.values()]
    dds     = [s["dd"]           for s in stats_dict.values()]
    pnls    = [s["total_pnl"]    for s in stats_dict.values()]
    return (
        f'<span class="chip"><span class="chip-k">yield (high, low)</span>'
        f'<span class="chip-v">{max(yields):.2f}%, {min(yields):.2f}%</span></span>'
        f'<span class="chip"><span class="chip-k">P&amp;L (high, low)</span>'
        f'<span class="chip-v">${max(pnls)/1000:,.1f}k, ${min(pnls)/1000:,.1f}k</span></span>'
        f'<span class="chip"><span class="chip-k">sharpe (high, low)</span>'
        f'<span class="chip-v">{max(sharpes):.2f}, {min(sharpes):.2f}</span></span>'
        f'<span class="chip"><span class="chip-k">max DD (best, worst)</span>'
        f'<span class="chip-v">{max(dds):.1f}%, {min(dds):.1f}%</span></span>'
        f'<span class="chip" style="background:rgba(184,134,11,0.06)">'
        f'<span class="chip-k">slippage sweep</span>'
        f'<span class="chip-v">0¢ / 1¢ / 2¢ / 3¢ per leg</span></span>'
    )


def patch_html(html_path, stats_dict):
    with open(html_path, "r") as f:
        html = f.read()

    # Bump the stats grid to 7 columns to fit starting + final + yield + wagered
    # + win + sharpe + dd. Tile is replace-all-safe because the literal only
    # appears once in the inline CSS block.
    html = html.replace(
        ".stats { display: grid; grid-template-columns: repeat(6, 1fr);",
        ".stats { display: grid; grid-template-columns: repeat(7, 1fr);",
        1,
    )

    new_tiles = build_stats_html(stats_dict)
    range_chips = build_range_chips(stats_dict)

    # Replace the existing <div class="stats">...</div> block. Closes with
    # </div>\s*</div> followed by <div class="charts">.
    stats_pat = re.compile(
        r'<div class="stats">[\s\S]*?</div>\s*</div>\s*(?=<div class="charts">)',
        re.MULTILINE,
    )
    html, n = stats_pat.subn(new_tiles, html, count=1)
    if n != 1:
        print(f"  WARN: stats div not found in {html_path}")
        return False

    # Strip prior range chips (any chips after <chip>direction</chip> within params-row)
    # Then re-inject. Match: from <span class="chip"><span class="chip-k">yield/...
    # up to </div> closing params-row. To be safe, strip all chips after "direction"
    # and rebuild.
    direction_marker = re.compile(
        r'(<span class="chip"><span class="chip-k">direction</span>'
        r'<span class="chip-v">[^<]*</span></span>)([\s\S]*?)(</div>)'
    )
    def _replace_after_direction(m):
        return m.group(1) + range_chips + m.group(3)
    html, n2 = direction_marker.subn(_replace_after_direction, html, count=1)
    if n2 != 1:
        # Fallback: append before closing </div> of params-row (any trailing chips replaced)
        params_pat = re.compile(r'(<div class="params-row">[\s\S]*?)</div>', re.MULTILINE)
        html, n2 = params_pat.subn(r'\1' + range_chips + "</div>", html, count=1)

    with open(html_path, "w") as f:
        f.write(html)
    return True


def main():
    for variant in VARIANTS:
        trades_csv = os.path.join(OUTPUT_DIR, f"all_trades-{variant}.csv")
        html_path  = os.path.join(OUTPUT_DIR, f"weekly_report-{variant}.html")
        if not (os.path.exists(trades_csv) and os.path.exists(html_path)):
            print(f"  Skipping {variant}: missing csv or html")
            continue

        df = pd.read_csv(trades_csv)
        stats_dict = {slip: stats_for_csv(df, slip) for slip in SLIPPAGES}

        s_best = stats_dict[0.00]
        s_canon= stats_dict[0.03]
        ok = patch_html(html_path, stats_dict)
        flag = "✓" if ok else "✗"
        print(f"  {flag} {variant}: yield {s_best['yield_pct']:.2f}% (0¢) / "
              f"{s_canon['yield_pct']:.2f}% (3¢)  "
              f"wagered ${s_best['total_wagered']/1000:,.1f}k  "
              f"pnl 0¢=${s_best['total_pnl']:,.0f} 3¢=${s_canon['total_pnl']:,.0f}")


if __name__ == "__main__":
    main()
