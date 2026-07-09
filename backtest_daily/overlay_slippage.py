"""
Re-run the strategy at 4 slippage levels (0¢, 1¢, 2¢, 3¢ per leg) without
touching disk, then build a 4-line overlay equity chart and patch it into
the existing weekly_report.html (in place of the current single curve).
The 3¢ canonical run remains the source of all other report content.
"""
import math
import os
import re
import sys

import numpy as np
import pandas as pd

import config
import data_loader
import backtest
import spreads


CANONICAL_HTML = os.path.join(config.OUTPUT_DIR, "weekly_report.html")

# Match the canonical: full 2020-2026 OOT, qty 2, no theta filter
START      = "2020-01-01"
END        = "2024-12-30"
TOP_N      = 5
SIZING     = "1"
# Theta filter: set to float("-inf") to disable (GROUND-only ranking).
# Set to e.g. 0.20 to keep theta-density filter on.
THETA_MIN  = float("-inf")
GAP_MIN    = -0.01
LOWVIX     = 15.0

SLIPPAGES  = [0.00, 0.01, 0.02, 0.03]   # in dollars per leg
COLORS     = {0.00: "#1D9E75", 0.01: "#378ADD",
              0.02: "#B8860B", 0.03: "#E24B4A"}
LABEL      = {0.00: "0¢ (mid-mid)", 0.01: "1¢/leg",
              0.02: "2¢/leg", 0.03: "3¢/leg (canonical)"}


def setup_config():
    """Window + theta-filter overrides only. Rest from config.py canonical defaults."""
    config.START_DATE = START
    config.END_DATE   = END
    if THETA_MIN != float("-inf"):
        config.MIN_THETA_CREDIT_RATIO = THETA_MIN


def load_data():
    df_full       = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df_backtest   = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    return df_full, expiry_prices, df_backtest


def setup_filters():
    """Clean baseline: regime + max-loss cap + θ/credit ≥ 0.20.
    Gap and low-VIX bull_put filters dropped — found to be redundant
    with the theta filter or actively reducing return."""
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP     = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER     = True
    spreads.REGIME_PER_TICKER = False

    # Explicitly disable the discarded filters (in case prior state lingers)
    spreads.GAP_FILTER             = False
    spreads.GAP_LOOKUP             = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.VIX_LOOKUP             = {}


def run_baseline_once(df_backtest, expiry_prices, use_cache=True):
    """Run the strategy ONCE at 0¢ slippage (no haircut). Trade selection
    is identical regardless of slippage now (it's post-hoc), so this gives
    us the canonical trade list. We derive each slippage trajectory by
    applying a flat haircut to dollar_pnl in the trade dataframe."""
    theta_tag = "off" if THETA_MIN == float("-inf") else f"min{int(THETA_MIN*100):02d}"
    cache_t = os.path.join(config.OUTPUT_DIR,
                           f"_overlay_v9_oot_qty{SIZING}_theta_{theta_tag}_trades.parquet")
    cache_w = os.path.join(config.OUTPUT_DIR,
                           f"_overlay_v9_oot_qty{SIZING}_theta_{theta_tag}_weekly.parquet")
    if use_cache and os.path.exists(cache_t) and os.path.exists(cache_w):
        print(f"[overlay] cache hit → {cache_t}, {cache_w}")
        return pd.read_parquet(cache_t), pd.read_parquet(cache_w)

    spreads.SLIPPAGE_CENTS = 0.0
    print("\n[overlay] running baseline at 0¢ (selection-canonical) ...")
    trades_df, weekly_df = backtest.run_backtest(
        df_backtest, expiry_prices, pd.DataFrame(), 0,
        top_n=TOP_N, sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )
    if not trades_df.empty:
        trades_df.to_parquet(cache_t, index=False)
    if not weekly_df.empty:
        weekly_df.to_parquet(cache_w, index=False)
    return trades_df, weekly_df


def derive_weekly_at_slippage(trades_df, slip):
    """Apply post-hoc slippage haircut to a baseline (0¢) trade list and
    rebuild the weekly bankroll trajectory. Returns a weekly_df with the
    same shape as backtest.run_backtest produces."""
    t = trades_df.copy()
    t["entry_date"] = pd.to_datetime(t["entry_date"])
    haircut = 2.0 * slip * 100  # 2 legs × cents × 100 multiplier
    t["dollar_pnl_adj"] = (
        t["dollar_pnl"] - t["contracts"] * haircut
    )

    br = config.STARTING_BANKROLL
    rows = []
    for entry_date, grp in t.groupby("entry_date", sort=True):
        wpnl = float(grp["dollar_pnl_adj"].sum())
        br = max(br + wpnl, 0.01)
        wins  = int((grp["result"] == "WIN").sum())
        loss  = int((grp["result"] == "LOSS").sum())
        part  = int((grp["result"] == "PARTIAL").sum())
        n     = len(grp)
        rows.append({
            "entry_date":   entry_date,
            "n_trades":     n,
            "wins":         wins,
            "losses":       loss,
            "partials":     part,
            "win_rate":     wins / n if n > 0 else None,
            "week_pnl":     round(wpnl, 2),
            "bankroll_eow": round(br, 2),
        })
    return pd.DataFrame(rows)


def stats_for(weekly_df, trades_df=None, slip=0.0):
    """Match results.py print_summary plus bankroll-independent wager-yield.

    yield_pct = total_pnl / total_wagered × 100  — sportsbook-style ROI
    that's invariant to starting bankroll. wagered = max_loss × contracts × 100
    (with slippage added to max_loss for true execution risk).
    """
    if weekly_df.empty:
        return dict(final=10000, total_roi=0, ann=0, sharpe=0, dd=0,
                    win_rate=0, n_trades=0, n_wins=0,
                    total_wagered=0, total_pnl=0, yield_pct=0)
    final     = float(weekly_df["bankroll_eow"].iloc[-1])
    total_roi = (final / config.STARTING_BANKROLL - 1) * 100
    n_weeks   = len(weekly_df)
    # Daily-cadence: each row is one trading day. Annualize with 252.
    ann       = total_roi / (n_weeks / 252) if n_weeks > 0 else 0
    weekly_ret = weekly_df["week_pnl"] / config.STARTING_BANKROLL
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(252)) \
        if weekly_ret.std() > 0 else 0
    rmx = weekly_df["bankroll_eow"].cummax()
    dd  = ((weekly_df["bankroll_eow"] - rmx) / rmx).min() * 100
    n_trades = int(weekly_df["n_trades"].sum())
    n_wins   = int(weekly_df["wins"].sum())
    win_rate = n_wins / n_trades * 100 if n_trades > 0 else 0

    # Yield: sum of realized $ P&L / sum of dollars wagered.
    # Invariant to starting bankroll; only depends on per-trade economics.
    total_wagered = 0.0
    total_pnl     = 0.0
    if trades_df is not None and not trades_df.empty:
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
    yield_pct = (total_pnl / total_wagered * 100.0) if total_wagered > 0 else 0.0

    return dict(final=final, total_roi=total_roi, ann=ann,
                sharpe=sharpe, dd=dd,
                win_rate=win_rate, n_trades=n_trades, n_wins=n_wins,
                total_wagered=total_wagered, total_pnl=total_pnl,
                yield_pct=yield_pct)


def load_spy_for_overlay(start, end):
    """SPY benchmark series, normalized to start at $10k."""
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    df = pd.read_csv(spy_csv)
    df.columns = [c.strip() for c in df.columns]
    df["Date"] = pd.to_datetime(df["Date"])
    df = df[(df["Date"] >= pd.Timestamp(start)) & (df["Date"] <= pd.Timestamp(end))]
    df = df[["Date", "Close"]].sort_values("Date").reset_index(drop=True)
    if df.empty:
        return None
    df["norm"] = df["Close"] / df["Close"].iloc[0] * config.STARTING_BANKROLL
    return df


def build_overlay_svg(weekly_dict, stats_dict, spy_df, w=960, h=480):
    """Build a 4-line overlay equity SVG. Uses same viewBox as the original."""
    pad_l, pad_r, pad_t, pad_b = 56, 24, 24, 50
    pw = w - pad_l - pad_r
    ph = h - pad_t - pad_b

    # Common date axis: union of all weekly dates + SPY dates
    all_dates = []
    for slip, wdf in weekly_dict.items():
        all_dates.extend(pd.to_datetime(wdf["entry_date"]).tolist())
    if spy_df is not None and not spy_df.empty:
        all_dates.extend(spy_df["Date"].tolist())
    if not all_dates:
        return ""
    dmin = min(all_dates); dmax = max(all_dates)
    span = (dmax - dmin).total_seconds()

    # Y range: union of all bankroll_eow + SPY normalized
    ymin = config.STARTING_BANKROLL
    ymax = config.STARTING_BANKROLL
    for wdf in weekly_dict.values():
        if not wdf.empty:
            ymin = min(ymin, float(wdf["bankroll_eow"].min()))
            ymax = max(ymax, float(wdf["bankroll_eow"].max()))
    if spy_df is not None and not spy_df.empty:
        ymin = min(ymin, float(spy_df["norm"].min()))
        ymax = max(ymax, float(spy_df["norm"].max()))
    margin = 0.04 * (ymax - ymin) if ymax > ymin else 1
    ymin -= margin; ymax += margin

    def sx(d):
        return pad_l + (d - dmin).total_seconds() / span * pw
    def sy(v):
        return pad_t + ph - (v - ymin) / (ymax - ymin) * ph

    parts = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;display:block">']

    # Y gridlines
    for frac in [0, 0.25, 0.5, 0.75, 1.0]:
        v = ymin + frac * (ymax - ymin)
        y = sy(v)
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w - pad_r}" '
                     f'y2="{y:.1f}" stroke="#888780" stroke-opacity="0.15"/>')
        label_v = v / 1000
        parts.append(f'<text x="{pad_l - 8}" y="{y + 4:.1f}" fill="#888780" '
                     f'font-size="10" text-anchor="end" '
                     f'font-family="-apple-system,sans-serif">${label_v:.1f}k</text>')

    # Starting bankroll dashed line
    y_start = sy(config.STARTING_BANKROLL)
    parts.append(f'<line x1="{pad_l}" y1="{y_start:.1f}" x2="{w-pad_r}" '
                 f'y2="{y_start:.1f}" stroke="#888780" stroke-opacity="0.5" '
                 f'stroke-dasharray="4 4"/>')

    # SPY benchmark (subtle gray dashed)
    if spy_df is not None and not spy_df.empty:
        spy_pts = " ".join(
            f"{sx(d):.1f},{sy(v):.1f}"
            for d, v in zip(spy_df["Date"], spy_df["norm"])
        )
        parts.append(f'<polyline points="{spy_pts}" fill="none" '
                     f'stroke="#888780" stroke-width="1.5" '
                     f'stroke-dasharray="6 4" stroke-opacity="0.7"/>')

    # 4 strategy curves — draw 0¢ first (back), 3¢ last (front)
    for slip in [0.00, 0.01, 0.02, 0.03]:
        wdf = weekly_dict[slip]
        if wdf.empty:
            continue
        pts = " ".join(
            f"{sx(pd.Timestamp(d)):.1f},{sy(v):.1f}"
            for d, v in zip(wdf["entry_date"], wdf["bankroll_eow"])
        )
        color = COLORS[slip]
        width = 2.5 if slip == 0.03 else 1.6
        opacity = 1.0 if slip == 0.03 else 0.78
        parts.append(f'<polyline points="{pts}" fill="none" '
                     f'stroke="{color}" stroke-width="{width}" '
                     f'stroke-opacity="{opacity}"/>')

    # X-axis date labels (8 evenly spaced)
    for i in range(9):
        frac = i / 8.0
        t  = dmin + (dmax - dmin) * frac
        x  = sx(t)
        lbl = "Start" if i == 0 else t.strftime("%b %d ’%y")
        parts.append(f'<text x="{x:.1f}" y="{h - pad_b + 22}" fill="#888780" '
                     f'font-size="10" text-anchor="middle" '
                     f'font-family="-apple-system,sans-serif">{lbl}</text>')

    # Legend (top-left area)
    parts.append('<g font-family="-apple-system,sans-serif" font-size="11">')
    legend_x = pad_l + 8
    legend_y = pad_t + 12
    for j, slip in enumerate([0.00, 0.01, 0.02, 0.03]):
        s = stats_dict[slip]
        ly = legend_y + j * 18
        color = COLORS[slip]
        parts.append(f'<line x1="{legend_x}" y1="{ly}" '
                     f'x2="{legend_x + 22}" y2="{ly}" stroke="{color}" '
                     f'stroke-width="{2.5 if slip == 0.03 else 1.6}"/>')
        # text: slippage label, final, ann, sharpe, dd
        txt = (f'{LABEL[slip]:<22}  '
               f'final ${s["final"]/1000:5.1f}k  '
               f'ann {s["ann"]:5.1f}%  '
               f'Sharpe {s["sharpe"]:.2f}  '
               f'DD {s["dd"]:.1f}%')
        parts.append(f'<text x="{legend_x + 30}" y="{ly + 4}" fill="{color}" '
                     f'font-weight="600" '
                     f'style="font-feature-settings:&apos;tnum&apos;">{txt}</text>')
    # SPY entry
    sy_legend = legend_y + 4 * 18
    parts.append(f'<line x1="{legend_x}" y1="{sy_legend}" '
                 f'x2="{legend_x + 22}" y2="{sy_legend}" stroke="#888780" '
                 f'stroke-width="1.5" stroke-dasharray="4 3"/>')
    parts.append(f'<text x="{legend_x + 30}" y="{sy_legend + 4}" fill="#888780" '
                 f'font-weight="600">S&amp;P 500 (SPY) benchmark</text>')
    parts.append('</g>')

    parts.append("</svg>")
    return "\n".join(parts)


def patch_html(html_path, new_svg, stats_dict):
    """Replace the equity SVG and the stats tiles. Stats tiles get the
    0¢ best-case value stacked above the 3¢ canonical (worst) value."""
    with open(html_path, "r") as f:
        html = f.read()

    # 1. Replace equity SVG
    pattern = re.compile(
        r'(<div class="chart-label">Equity curve</div>\s*)<svg[\s\S]*?</svg>',
        re.MULTILINE,
    )
    html, count = pattern.subn(lambda m: m.group(1) + new_svg, html, count=1)
    if count != 1:
        raise RuntimeError("Could not locate the existing equity SVG to replace")

    # 2. Inject CSS for the stacked-value tiles (additive, doesn't break existing)
    css_inject = (
        "\n  .stat-value-best { font-size: 22px; font-weight: 600; "
        "margin-top: 4px; line-height: 1.05; }"
        "\n  .stat-value-canon { font-size: 14px; font-weight: 500; "
        "color: #888780; margin-top: 4px; line-height: 1.0; }"
        "\n  .stat-tag { display: inline-block; font-size: 9px; "
        "color: #888780; margin-left: 4px; vertical-align: 2px; "
        "letter-spacing: 0.4px; text-transform: uppercase; }"
        "\n  .stat .pos { color: #1D9E75; }  .stat .neg { color: #E24B4A; }"
    )
    html = html.replace(
        ".pos { color: #1D9E75; } .neg { color: #E24B4A; }",
        ".pos { color: #1D9E75; } .neg { color: #E24B4A; }" + css_inject,
        1,
    )

    # 3. Build new stats tiles HTML
    s_best = stats_dict[0.00]   # 0¢ — best case
    s_canon= stats_dict[0.03]   # 3¢ — canonical / worst case

    def _pair(label, best_val, canon_val, color_best="", color_canon=""):
        return (f'<div class="stat">'
                f'<div class="stat-label">{label}'
                f'<span class="stat-tag">0¢ / 3¢</span></div>'
                f'<div class="stat-value-best {color_best}">{best_val}</div>'
                f'<div class="stat-value-canon {color_canon}">{canon_val}</div>'
                f'</div>')

    final_color  = "pos" if s_best["final"] >= config.STARTING_BANKROLL else "neg"
    yield_color  = "pos" if s_best["yield_pct"] >= 0 else "neg"

    new_tiles = (
        '<div class="stats">'
        + _pair("yield (P&L / wagered)",
                f"{s_best['yield_pct']:.2f}%",
                f"{s_canon['yield_pct']:.2f}%",
                color_best=yield_color, color_canon="")
        + _pair("total wagered",
                f"${s_best['total_wagered']/1000:,.1f}k",
                f"${s_canon['total_wagered']/1000:,.1f}k")
        + _pair("realized P&L",
                f"${s_best['total_pnl']:,.0f}",
                f"${s_canon['total_pnl']:,.0f}",
                color_best=yield_color, color_canon="")
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
        + '</div>'
    )

    # Replace the existing <div class="stats">...</div> block. The closing
    # </div> of the .stats container is followed by a blank line and then
    # <div class="charts">. Use that as a deterministic terminator.
    stats_pat = re.compile(
        r'<div class="stats">[\s\S]*?</div>\s*</div>\s*(?=<div class="charts">)',
        re.MULTILINE,
    )
    html, count = stats_pat.subn(new_tiles + "\n", html, count=1)
    if count != 1:
        raise RuntimeError("Could not locate the existing stats tiles to replace")

    # 4. Append (high, low) range chips to the params-row.
    # Yield (P&L / wagered) is bankroll-independent — the more honest metric.
    yields  = [s["yield_pct"]    for s in stats_dict.values()]
    sharpes = [s["sharpe"]       for s in stats_dict.values()]
    dds     = [s["dd"]           for s in stats_dict.values()]
    pnls    = [s["total_pnl"]    for s in stats_dict.values()]

    range_chips = (
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
    # Inject just before the closing </div> of params-row
    params_pat = re.compile(r'(<div class="params-row">[\s\S]*?)</div>', re.MULTILINE)
    html, count = params_pat.subn(r'\1' + range_chips + "</div>", html, count=1)
    if count != 1:
        print("[overlay] warning: couldn't locate params-row to inject range chips")

    with open(html_path, "w") as f:
        f.write(html)
    print(f"[overlay] patched {html_path}")


def main():
    setup_config()
    df_full, expiry_prices, df_backtest = load_data()
    setup_filters()

    # Run backtest once, then derive 4 trajectories via post-hoc haircut
    trades_df, _ = run_baseline_once(df_backtest, expiry_prices)
    weekly_dict = {}
    stats_dict  = {}
    for slip in SLIPPAGES:
        wdf = derive_weekly_at_slippage(trades_df, slip)
        weekly_dict[slip] = wdf
        stats_dict[slip]  = stats_for(wdf, trades_df=trades_df, slip=slip)

    print("\n[overlay] summary:")
    for slip in SLIPPAGES:
        s = stats_dict[slip]
        print(f"  {LABEL[slip]:>20}: final ${s['final']:,.0f}  "
              f"ann {s['ann']:.2f}%  Sharpe {s['sharpe']:.2f}  DD {s['dd']:.2f}%")

    spy_df = load_spy_for_overlay(START, END)
    new_svg = build_overlay_svg(weekly_dict, stats_dict, spy_df)
    patch_html(CANONICAL_HTML, new_svg, stats_dict)


if __name__ == "__main__":
    main()
