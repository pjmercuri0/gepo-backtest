"""
results.py
Saves CSVs, equity curve PNG, and a self-contained HTML report
with inline SVG charts and per-trade strike details.
"""

import os
import math
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import config


def _build_param_chips(trades_df: pd.DataFrame, weekly_df: pd.DataFrame) -> str:
    """
    Render the actual run parameters as a row of small "chips" under
    the report subtitle. Mirrors the terminal summary so the HTML
    stands alone as a record of exactly what was run.
    """
    chips = []

    # Date range from weekly_df (avoids relying on config strings)
    if not weekly_df.empty:
        d0 = pd.to_datetime(weekly_df["entry_date"].iloc[0]).date()
        d1 = pd.to_datetime(weekly_df["entry_date"].iloc[-1]).date()
        chips.append(("range", f"{d0} → {d1}"))
        chips.append(("weeks", f"{len(weekly_df)}"))

    # Trades
    if trades_df is not None and not trades_df.empty:
        chips.append(("trades", f"{len(trades_df)}"))

    # Core scoring params
    chips.append(("ground", f"≥ {config.GROUND_THRESHOLD}"))
    chips.append(("delta",  f"{config.DELTA_TARGET} [{config.DELTA_MIN}–{config.DELTA_MAX}]"))
    chips.append(("DTE",    f"{config.DTE_MIN}–{config.DTE_MAX}d"))

    # Credit ratio bounds
    cr_min = getattr(config, "MIN_CREDIT_RATIO", None)
    cr_max = getattr(config, "MAX_CREDIT_RATIO", float("inf"))
    cr_str = f"min {cr_min}" if cr_min is not None else ""
    if cr_max != float("inf"):
        cr_str += f", max {cr_max}" if cr_str else f"max {cr_max}"
    if cr_str:
        chips.append(("credit ratio", cr_str))

    # Max max_loss cap
    mml = getattr(config, "MAX_MAX_LOSS", float("inf"))
    chips.append(("max_loss cap",
                  f"${mml}/share" if mml != float("inf") else "none"))

    # Selection / sizing — pull from config if run.py set them
    top_n = getattr(config, "TOP_N", None)
    chips.append(("top-N", str(top_n) if top_n else "all"))

    sizing = getattr(config, "SIZING", None)
    if sizing is not None:
        chips.append(("sizing", str(sizing)))
    elif trades_df is not None and not trades_df.empty and "contracts" in trades_df.columns:
        contracts = trades_df["contracts"]
        if contracts.nunique() == 1:
            chips.append(("sizing", f"{int(contracts.iloc[0])}× flat"))
        else:
            chips.append(("sizing", f"Kelly (mean {contracts.mean():.1f}×)"))

    # Lookback (the diagnostic-only history window)
    lookback = getattr(config, "LOOKBACK", None)
    if lookback is not None:
        chips.append(("lookback", f"{lookback}d" if lookback > 0 else "off"))

    # Drift
    use_drift = getattr(config, "USE_DRIFT", False)
    if use_drift:
        dw = getattr(config, "DRIFT_WINDOW", 60)
        chips.append(("drift", f"on, {dw}d window"))
    else:
        chips.append(("drift", "off (pure Greek)"))

    # Regime filter
    regime_on = getattr(config, "REGIME_FILTER", False)
    if regime_on:
        rw  = getattr(config, "REGIME_WINDOW", 50)
        src = getattr(config, "REGIME_SOURCE", "spy").upper()
        chips.append(("regime filter", f"on, {src} {rw}d SMA"))
    else:
        chips.append(("regime filter", "off"))

    # Direction split (computed from trades)
    if trades_df is not None and not trades_df.empty and "decision" in trades_df.columns:
        dirs = trades_df["decision"].value_counts()
        bp = int(dirs.get("bull_put", 0))
        bc = int(dirs.get("bear_call", 0))
        if bp + bc > 0:
            chips.append(("direction", f"{bp} bull-put / {bc} bear-call"))

    rendered = "".join(
        f'<span class="chip"><span class="chip-k">{k}</span>'
        f'<span class="chip-v">{v}</span></span>'
        for k, v in chips
    )
    return rendered


def save_results(trades_df: pd.DataFrame,
                 weekly_df: pd.DataFrame) -> None:
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    trades_path = os.path.join(config.OUTPUT_DIR, config.TRADES_CSV)
    weekly_path = os.path.join(config.OUTPUT_DIR, config.RESULTS_CSV)
    trades_df.to_csv(trades_path, index=False)
    weekly_df.to_csv(weekly_path, index=False)
    print(f"\nSaved: {trades_path}")
    print(f"Saved: {weekly_path}")

    _plot_equity_curve(weekly_df)
    _generate_weekly_html(trades_df, weekly_df)
    print_summary(trades_df, weekly_df)


def print_summary(trades_df, weekly_df) -> None:
    if weekly_df.empty or trades_df.empty:
        print("No results to summarise.")
        return

    start_br  = config.STARTING_BANKROLL
    final_br  = weekly_df["bankroll_eow"].iloc[-1]
    total_pnl = final_br - start_br
    total_roi = total_pnl / start_br * 100

    n_weeks  = len(weekly_df)
    n_trades = len(trades_df)
    wins     = (trades_df["result"] == "WIN").sum()
    losses   = (trades_df["result"] == "LOSS").sum()
    partials = (trades_df["result"] == "PARTIAL").sum()
    win_rate = wins / n_trades * 100 if n_trades > 0 else 0

    weekly_returns = weekly_df["week_pnl"] / config.STARTING_BANKROLL
    avg_ret = weekly_returns.mean()
    std_ret = weekly_returns.std()
    sharpe  = (avg_ret / std_ret * math.sqrt(52)) if std_ret > 0 else 0

    running_max = weekly_df["bankroll_eow"].cummax()
    drawdown    = (weekly_df["bankroll_eow"] - running_max) / running_max
    max_dd      = drawdown.min() * 100

    direction = trades_df["decision"].value_counts()

    print("\n" + "=" * 60)
    print("  GEPO CREDIT SPREAD BACKTEST — SUMMARY")
    print("=" * 60)
    print(f"  Period:          {weekly_df['entry_date'].min().date()} "
          f"to {weekly_df['entry_date'].max().date()}")
    print(f"  Weeks traded:    {n_weeks}")
    print(f"  Total trades:    {n_trades:,}")
    print(f"  Starting:        ${start_br:>12,.2f}")
    print(f"  Final:           ${final_br:>12,.2f}")
    print(f"  Total P&L:       ${total_pnl:>12,.2f}")
    print(f"  Total ROI:       {total_roi:>11.2f}%")
    print(f"  Annualised ROI:  {total_roi / (n_weeks / 52):>10.2f}%")
    print(f"  Sharpe ratio:    {sharpe:>11.2f}")
    print(f"  Max drawdown:    {max_dd:>11.2f}%")
    print("-" * 60)
    print(f"  Win rate:        {win_rate:>11.1f}%")
    print(f"  Wins:            {wins:>11,}")
    print(f"  Losses:          {losses:>11,}")
    print(f"  Partials:        {partials:>11,}")
    print("-" * 60)
    print("  Direction split:")
    for k, v in direction.items():
        print(f"    {k:<20} {v:>6,} ({v/n_trades*100:.1f}%)")
    print("=" * 60)


def _plot_equity_curve(weekly_df: pd.DataFrame) -> None:
    if weekly_df.empty: return
    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8),
                                    gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    fig.patch.set_facecolor("#0f0f0f")

    dates    = pd.to_datetime(weekly_df["entry_date"])
    bankroll = weekly_df["bankroll_eow"]
    week_pnl = weekly_df["week_pnl"]
    GREEN, RED, GREY = "#1D9E75", "#E24B4A", "#888780"

    ax1.plot(dates, bankroll, linewidth=2, color=GREEN)
    ax1.axhline(config.STARTING_BANKROLL, linestyle="--", color=GREY, linewidth=0.8)
    ax1.fill_between(dates, config.STARTING_BANKROLL, bankroll,
                     where=bankroll >= config.STARTING_BANKROLL, alpha=0.20, color=GREEN)
    ax1.fill_between(dates, config.STARTING_BANKROLL, bankroll,
                     where=bankroll < config.STARTING_BANKROLL, alpha=0.20, color=RED)
    ax1.set_facecolor("#0f0f0f")
    ax1.set_ylabel("Portfolio value", color=GREY)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x/1000:.0f}k"))
    ax1.grid(axis="y", alpha=0.15, color=GREY); ax1.tick_params(colors=GREY)
    for s in ax1.spines.values(): s.set_color(GREY)

    colors = [GREEN if v >= 0 else RED for v in week_pnl]
    ax2.bar(dates, week_pnl, color=colors, width=5, alpha=0.85)
    ax2.axhline(0, color=GREY, linewidth=0.6)
    ax2.set_facecolor("#0f0f0f")
    ax2.set_ylabel("Weekly P&L", color=GREY)
    ax2.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax2.grid(axis="y", alpha=0.15, color=GREY); ax2.tick_params(colors=GREY)
    for s in ax2.spines.values(): s.set_color(GREY)

    plt.tight_layout()
    out_path = os.path.join(config.OUTPUT_DIR, config.EQUITY_CURVE_PNG)
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor="#0f0f0f")
    plt.close()
    print(f"Saved: {out_path}")


def _build_benchmark(weekly_df: pd.DataFrame, start_br: float):
    """
    Read a one-time-downloaded SPY (SPDR S&P 500 ETF) history CSV from
    data/spy_history.csv (or data/spy_us_d.csv) and rebase to start_br
    for overlay on the equity curve.

    Expected CSV columns (Yahoo or Stooq format both work):
      Date, Open, High, Low, Close [, Adj Close, Volume]

    Returns a list of length n+1 aligned to [start, week 1, week 2, ...].
    Returns None if the file is missing or unreadable, in which case
    the chart still renders without the overlay.

    To refresh: download fresh CSV from Stooq or Yahoo Finance
    and replace data/spy_history.csv.
    """
    # Try common locations and filename variants
    filenames = ["spy_history.csv", "spy_us_d.csv"]
    candidate_paths = []
    for fn in filenames:
        candidate_paths.extend([
            os.path.join(config.DATA_DIR, fn),
            os.path.join(os.path.dirname(__file__), "data", fn),
            os.path.join(os.path.dirname(__file__), fn),
        ])
    csv_path = next((p for p in candidate_paths if os.path.exists(p)), None)
    if csv_path is None:
        print("[benchmark] spy_history.csv not found in data/. "
              "Skipping S&P 500 overlay. Download from "
              "https://stooq.com/q/d/?s=spy.us&i=d and save as data/spy_history.csv")
        return None

    try:
        df = pd.read_csv(csv_path)
    except Exception as e:
        print(f"[benchmark] failed to read {csv_path}: {e}")
        return None

    # Normalize column names (Stooq uses 'Date,Open,High,Low,Close,Volume',
    # Yahoo uses 'Date,Open,High,Low,Close,Adj Close,Volume')
    df.columns = [c.strip() for c in df.columns]
    if "Date" not in df.columns or "Close" not in df.columns:
        print(f"[benchmark] {csv_path} missing required Date/Close columns")
        return None

    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").set_index("Date")
    prices = df["Close"].dropna()

    if prices.empty:
        return None

    weekly_dates = pd.to_datetime(weekly_df["entry_date"])

    # Base price = first SPY close at/before backtest start
    pre = prices.index[prices.index <= weekly_dates.min()]
    if len(pre) == 0:
        # Backtest starts before SPY data — use first available
        base_price = prices.iloc[0]
    else:
        base_price = prices.loc[pre[-1]]

    # Sample SPY at each weekly entry_date (latest available <= date)
    out = [start_br]
    for d in weekly_dates:
        avail = prices.index[prices.index <= d]
        if len(avail) == 0:
            out.append(start_br)
        else:
            ratio = prices.loc[avail[-1]] / base_price
            out.append(float(start_br * ratio))
    return out


def _build_equity_svg(weekly_df: pd.DataFrame, start_br: float,
                      benchmark=None) -> str:
    """Build an inline SVG equity curve."""
    if weekly_df.empty:
        return ""

    W, H, ML, MR, MT, MB = 960, 480, 56, 24, 24, 50
    plot_w, plot_h = W - ML - MR, H - MT - MB

    bankrolls = [start_br] + list(weekly_df["bankroll_eow"])
    n = len(bankrolls)

    # Compute y-range, expanding to include benchmark if provided
    all_vals = list(bankrolls)
    if benchmark is not None and len(benchmark) >= n:
        all_vals += list(benchmark[:n])
    y_min = min(min(all_vals), start_br) * 0.98
    y_max = max(max(all_vals), start_br) * 1.02
    y_range = y_max - y_min if y_max > y_min else 1

    def x_of(i): return ML + (i / max(n-1, 1)) * plot_w
    def y_of(v): return MT + (1 - (v - y_min) / y_range) * plot_h

    pts = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}" for i, v in enumerate(bankrolls))

    # Gridlines and y-axis labels
    grid_lines, y_labels = [], []
    for k in range(5):
        v = y_min + (k / 4) * y_range
        y = y_of(v)
        grid_lines.append(f'<line x1="{ML}" y1="{y:.1f}" x2="{W-MR}" y2="{y:.1f}" stroke="#888780" stroke-opacity="0.15"/>')
        y_labels.append(f'<text x="{ML-8}" y="{y+4:.1f}" fill="#888780" font-size="10" text-anchor="end" font-family="-apple-system,sans-serif">${v/1000:.1f}k</text>')

    # X-axis date labels
    dates = list(weekly_df["entry_date"])
    x_labels = []
    step = max(1, len(dates) // 8)
    x_labels.append(f'<text x="{ML}" y="{H-12}" fill="#888780" font-size="10" text-anchor="middle" font-family="-apple-system,sans-serif">Start</text>')
    for i, d in enumerate(dates):
        if i % step == 0 or i == len(dates) - 1:
            x = x_of(i + 1)
            label = pd.Timestamp(d).strftime("%b %d")
            x_labels.append(f'<text x="{x:.1f}" y="{H-12}" fill="#888780" font-size="10" text-anchor="middle" font-family="-apple-system,sans-serif">{label}</text>')

    # Start line
    start_y = y_of(start_br)
    start_line = f'<line x1="{ML}" y1="{start_y:.1f}" x2="{W-MR}" y2="{start_y:.1f}" stroke="#888780" stroke-opacity="0.5" stroke-dasharray="4 4"/>'

    # Filled area under curve, clipped to start line
    final_above = bankrolls[-1] >= start_br
    fill_color = "#1D9E75" if final_above else "#E24B4A"
    fill_pts = f"{ML},{start_y:.1f} " + pts + f" {W-MR},{start_y:.1f}"
    fill_path = f'<polygon points="{fill_pts}" fill="{fill_color}" fill-opacity="0.20"/>'

    line = f'<polyline points="{pts}" fill="none" stroke="{fill_color}" stroke-width="2.5"/>'

    # Benchmark overlay (grey dashed line)
    bench_line = ""
    legend = ""
    if benchmark is not None and len(benchmark) >= n:
        bpts = " ".join(f"{x_of(i):.1f},{y_of(v):.1f}"
                        for i, v in enumerate(benchmark[:n]))
        BENCH = "#888780"
        bench_line = (
            f'<polyline points="{bpts}" fill="none" stroke="{BENCH}" '
            f'stroke-width="1.5" stroke-dasharray="6 4" stroke-opacity="0.85"/>'
        )
        # Legend in top-left corner of plot area
        lx = ML + 12
        ly = MT + 16
        legend = (
            f'<g font-family="-apple-system,sans-serif" font-size="11">'
            f'<line x1="{lx}" y1="{ly-3}" x2="{lx+18}" y2="{ly-3}" stroke="{fill_color}" stroke-width="2.5"/>'
            f'<text x="{lx+24}" y="{ly}" fill="{fill_color}" font-weight="600">Strategy</text>'
            f'<line x1="{lx+90}" y1="{ly-3}" x2="{lx+108}" y2="{ly-3}" stroke="{BENCH}" stroke-width="1.5" stroke-dasharray="4 3"/>'
            f'<text x="{lx+114}" y="{ly}" fill="{BENCH}" font-weight="600">S&amp;P 500 (SPY)</text>'
            f'</g>'
        )

    return f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">
{"".join(grid_lines)}
{start_line}
{fill_path}
{bench_line}
{line}
{legend}
{"".join(y_labels)}
{"".join(x_labels)}
</svg>'''


def _build_weekly_pnl_svg(weekly_df: pd.DataFrame) -> str:
    """Build an inline SVG weekly P&L bar chart."""
    if weekly_df.empty:
        return ""

    W, H, ML, MR, MT, MB = 480, 480, 50, 20, 20, 40
    plot_w, plot_h = W - ML - MR, H - MT - MB

    pnls = list(weekly_df["week_pnl"])
    dates = list(weekly_df["entry_date"])
    n = len(pnls)

    y_max = max(max(pnls), 0) * 1.1 if pnls else 1
    y_min = min(min(pnls), 0) * 1.1 if pnls else -1
    y_range = y_max - y_min if y_max > y_min else 1

    def x_of(i): return ML + (i + 0.5) / n * plot_w
    def y_of(v): return MT + (1 - (v - y_min) / y_range) * plot_h
    bar_w = max(8, min(40, plot_w / n * 0.7))

    zero_y = y_of(0)
    zero_line = f'<line x1="{ML}" y1="{zero_y:.1f}" x2="{W-MR}" y2="{zero_y:.1f}" stroke="#888780" stroke-opacity="0.4"/>'

    bars = []
    for i, v in enumerate(pnls):
        x = x_of(i) - bar_w / 2
        y_top = y_of(max(v, 0))
        y_bot = y_of(min(v, 0))
        h = max(2, y_bot - y_top)
        color = "#1D9E75" if v >= 0 else "#E24B4A"
        bars.append(f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{color}" fill-opacity="0.85" rx="2"/>')

    # Y labels: 3 ticks
    y_labels = []
    for k in range(3):
        v = y_min + (k / 2) * y_range
        y = y_of(v)
        sign = "-" if v < 0 else ""
        y_labels.append(f'<text x="{ML-8}" y="{y+4:.1f}" fill="#888780" font-size="10" text-anchor="end" font-family="-apple-system,sans-serif">{sign}${abs(v):,.0f}</text>')

    # X labels
    x_labels = []
    step = max(1, n // 8)
    for i, d in enumerate(dates):
        if i % step == 0 or i == n - 1:
            x = x_of(i)
            label = pd.Timestamp(d).strftime("%b %d")
            x_labels.append(f'<text x="{x:.1f}" y="{H-10}" fill="#888780" font-size="10" text-anchor="middle" font-family="-apple-system,sans-serif">{label}</text>')

    return f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">
{zero_line}
{"".join(bars)}
{"".join(y_labels)}
{"".join(x_labels)}
</svg>'''


def _build_direction_svg(trades_df: pd.DataFrame,
                         weekly_df: pd.DataFrame) -> str:
    """
    Net direction bars per week, x-axis aligned with the equity curve.
    Positive = bull-put surplus, negative = bear-call surplus.
    """
    if trades_df.empty or weekly_df.empty:
        return ""

    df = trades_df[trades_df["decision"] != "PASS"].copy()
    if df.empty:
        return ""
    df["entry_date"] = pd.to_datetime(df["entry_date"])

    # Per-week net (bull_put count − bear_call count)
    counts = df.groupby("entry_date")["decision"].apply(
        lambda s: (s == "bull_put").sum() - (s == "bear_call").sum()
    )

    # Align to weekly_df entry dates so x-axis matches equity curve
    weekly_dates = pd.to_datetime(weekly_df["entry_date"]).reset_index(drop=True)
    nets = [int(counts.get(d, 0)) for d in weekly_dates]
    n = len(weekly_dates)

    # Match equity curve geometry: 960×240 with same horizontal margins
    W, H, ML, MR, MT, MB = 960, 200, 56, 24, 28, 50
    plot_w, plot_h = W - ML - MR, H - MT - MB

    n_total = n + 1  # +1 for the "Start" position used by equity curve
    def x_of(i): return ML + (i / max(n_total - 1, 1)) * plot_w

    max_abs = max(abs(min(nets, default=0)), abs(max(nets, default=0)), 1)
    y_max = max_abs * 1.20
    y_min = -max_abs * 1.20
    y_range = y_max - y_min
    def y_of(v): return MT + (1 - (v - y_min) / y_range) * plot_h

    bar_w = (plot_w / max(n_total - 1, 1)) * 0.7
    zero_y = y_of(0)

    # Background grid at integer net values
    grid = []
    for v in range(int(y_min) + 1, int(y_max) + 1):
        if v == 0:
            continue
        y = y_of(v)
        grid.append(
            f'<line x1="{ML}" y1="{y:.1f}" x2="{W-MR}" y2="{y:.1f}" '
            f'stroke="rgba(255,255,255,0.05)" stroke-width="1"/>'
        )

    # Y-axis labels at meaningful integers
    y_labels = []
    for v in [int(y_min) + 1, 0, int(y_max) - 1]:
        if v == 0 or abs(v) > max_abs:
            continue
        label = f"+{v}" if v > 0 else str(v)
        y_labels.append(
            f'<text x="{ML-8}" y="{y_of(v)+4:.1f}" fill="#888780" font-size="10" '
            f'text-anchor="end" font-family="-apple-system,sans-serif">{label}</text>'
        )
    # max and min labels
    y_labels.append(
        f'<text x="{ML-8}" y="{y_of(max_abs)+4:.1f}" fill="#1D9E75" font-size="10" '
        f'text-anchor="end" font-family="-apple-system,sans-serif" font-weight="600">+{max_abs}</text>'
    )
    y_labels.append(
        f'<text x="{ML-8}" y="{y_of(-max_abs)+4:.1f}" fill="#E24B4A" font-size="10" '
        f'text-anchor="end" font-family="-apple-system,sans-serif" font-weight="600">-{max_abs}</text>'
    )

    # Zero line
    zero_line = (
        f'<line x1="{ML}" y1="{zero_y:.1f}" x2="{W-MR}" y2="{zero_y:.1f}" '
        f'stroke="#888780" stroke-opacity="0.4" stroke-width="1"/>'
    )

    # Bars
    bars = []
    for i, net in enumerate(nets):
        if net == 0:
            continue
        x_center = x_of(i + 1)  # +1 because position 0 is "Start"
        x = x_center - bar_w / 2
        y_top = y_of(max(net, 0))
        y_bot = y_of(min(net, 0))
        h = max(2, y_bot - y_top)
        color = "#1D9E75" if net > 0 else "#E24B4A"
        bars.append(
            f'<rect x="{x:.1f}" y="{y_top:.1f}" width="{bar_w:.1f}" height="{h:.1f}" '
            f'fill="{color}" fill-opacity="0.85" rx="1.5"/>'
        )

    # X-axis labels (sparse, like equity curve)
    x_labels = [
        f'<text x="{ML}" y="{H-12}" fill="#888780" font-size="10" '
        f'text-anchor="middle" font-family="-apple-system,sans-serif">Start</text>'
    ]
    step = max(1, n // 8)
    for i, d in enumerate(weekly_dates):
        if i % step == 0 or i == n - 1:
            x = x_of(i + 1)
            label = pd.Timestamp(d).strftime("%b %d")
            x_labels.append(
                f'<text x="{x:.1f}" y="{H-12}" fill="#888780" font-size="10" '
                f'text-anchor="middle" font-family="-apple-system,sans-serif">{label}</text>'
            )

    # Header (counts of bull-skewed vs bear-skewed weeks)
    n_bull = sum(1 for v in nets if v > 0)
    n_bear = sum(1 for v in nets if v < 0)
    n_flat = sum(1 for v in nets if v == 0)
    header = (
        f'<text x="{ML}" y="{MT+12}" fill="#888780" font-size="11" '
        f'font-family="-apple-system,sans-serif">'
        f'<tspan fill="#1D9E75" font-weight="600">{n_bull}</tspan> bull-skewed · '
        f'<tspan fill="#E24B4A" font-weight="600">{n_bear}</tspan> bear-skewed · '
        f'{n_flat} balanced</text>'
    )

    return f'''<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;display:block">
{"".join(grid)}
{zero_line}
{"".join(bars)}
{header}
{"".join(y_labels)}
{"".join(x_labels)}
</svg>'''


def _generate_weekly_html(trades_df: pd.DataFrame,
                          weekly_df: pd.DataFrame) -> None:
    if trades_df.empty:
        return

    out_path = os.path.join(config.OUTPUT_DIR, "weekly_report.html")

    BG, CARD, TEXT, MUTED = "#0f0f0f", "#1a1a1a", "#e8e8e8", "#888780"
    GREEN, RED, AMBER, BORDER = "#1D9E75", "#E24B4A", "#EF9F27", "rgba(255,255,255,0.08)"

    weekly_df = weekly_df.copy()
    weekly_df["entry_date"] = pd.to_datetime(weekly_df["entry_date"])
    trades_df = trades_df.copy()
    trades_df["entry_date"] = pd.to_datetime(trades_df["entry_date"])

    start_br = config.STARTING_BANKROLL
    final_br = weekly_df["bankroll_eow"].iloc[-1]
    roi      = (final_br - start_br) / start_br * 100
    weekly_ret = weekly_df["week_pnl"] / start_br
    sharpe   = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(52)) if weekly_ret.std() > 0 else 0
    rmax = weekly_df["bankroll_eow"].cummax()
    max_dd = ((weekly_df["bankroll_eow"] - rmax) / rmax).min() * 100

    n_trades = len(trades_df)
    wins = (trades_df["result"] == "WIN").sum()
    win_rate = wins / n_trades * 100 if n_trades > 0 else 0

    eq_bench = _build_benchmark(weekly_df, start_br)
    eq_svg  = _build_equity_svg(weekly_df, start_br, benchmark=eq_bench)
    pnl_svg = _build_weekly_pnl_svg(weekly_df)
    dir_svg = _build_direction_svg(trades_df, weekly_df)

    # GROUND bar fill = trade's GROUND as a fraction of the run-wide maximum.
    # Bar fills 0%-100% based on the display value exp(GROUND), which is
    # positive and monotone-preserving for both canons (Γ_k → exp(Γ_k) and
    # J_k → exp(J_k)). The 95th percentile sets the 100% reference so a
    # few outliers don't squash the bulk of bars.
    if "GROUND" in trades_df.columns and trades_df["GROUND"].notna().any():
        disp = np.exp(trades_df["GROUND"].astype(float))
        disp_min = float(disp.min())
        disp_ref = float(disp.quantile(0.95))
        if disp_ref > disp_min:
            trades_df["GROUND_pct"] = (
                ((disp - disp_min) / (disp_ref - disp_min) * 100).clip(lower=0, upper=100)
            )
        else:
            trades_df["GROUND_pct"] = 100.0
    else:
        trades_df["GROUND_pct"] = 0.0

    # Build run-parameter chips for the report header — mirrors what's
    # printed in the terminal so the HTML stands alone as a record of
    # exactly what was run.
    params_chips = _build_param_chips(trades_df, weekly_df)

    parts = [f'''<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>GEPO Backtest</title>
<style>
  body {{ background: {BG}; color: {TEXT}; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         margin: 0; padding: 32px; }}
  h1 {{ font-size: 24px; font-weight: 600; margin: 0 0 4px; }}
  .subtitle {{ color: {MUTED}; font-size: 13px; margin-bottom: 14px; }}
  .params-row {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 28px; }}
  .chip {{ display: inline-flex; align-items: center; gap: 6px;
           background: {CARD}; border: 0.5px solid {BORDER};
           border-radius: 6px; padding: 4px 10px; font-size: 11px; }}
  .chip-k {{ color: {MUTED}; text-transform: lowercase; }}
  .chip-v {{ color: {TEXT}; font-weight: 500; }}
  .stats {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px; margin-bottom: 24px; }}
  .stat   {{ background: {CARD}; border: 0.5px solid {BORDER}; border-radius: 10px; padding: 14px 16px; }}
  .stat-label {{ font-size: 11px; color: {MUTED}; text-transform: lowercase; }}
  .stat-value {{ font-size: 24px; font-weight: 600; margin-top: 4px; }}
  .pos {{ color: {GREEN}; }} .neg {{ color: {RED}; }}
  .charts {{ display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 32px; }}
  .chart-card {{ background: {CARD}; border: 0.5px solid {BORDER}; border-radius: 12px; padding: 16px; }}
  .chart-label {{ font-size: 11px; color: {MUTED}; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 12px; }}
  .section-label {{ font-size: 11px; color: {MUTED}; text-transform: uppercase; letter-spacing: 0.5px; margin: 32px 0 16px; }}
  .week-card {{ background: {CARD}; border: 0.5px solid {BORDER}; border-radius: 12px;
                margin-bottom: 20px; padding: 20px 24px; }}
  .week-head {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 16px; }}
  .week-title {{ font-size: 18px; font-weight: 600; }}
  .week-sub {{ font-size: 13px; color: {MUTED}; margin-top: 4px; }}
  .week-pnl {{ font-size: 22px; font-weight: 700; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ text-align: left; color: {MUTED}; font-weight: 500; font-size: 11px;
        text-transform: lowercase; padding: 8px 6px; border-bottom: 1px solid {BORDER}; }}
  td {{ padding: 8px 6px; border-bottom: 1px solid {BORDER}; }}
  tr:last-child td {{ border-bottom: none; }}
  td.fw {{ font-weight: 600; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
  .badge-WIN     {{ background: rgba(29,158,117,0.15); color: {GREEN}; }}
  .badge-LOSS    {{ background: rgba(226,75,74,0.15); color: {RED}; }}
  .badge-PARTIAL {{ background: rgba(239,159,39,0.15); color: {AMBER}; }}
  .dir-bull   {{ color: {GREEN}; }}
  .dir-bear   {{ color: {RED};   }}
  .ground-bar {{ display: inline-block; width: 70px; height: 5px; background: {BORDER};
                 border-radius: 3px; vertical-align: middle; margin-right: 8px; }}
  .ground-bar-fill {{ display: block; height: 100%; background: #378ADD; border-radius: 3px; }}
  .summary-row {{ margin-top: 14px; padding-top: 14px; border-top: 1px solid {BORDER};
                  display: flex; gap: 28px; font-size: 12px; color: {MUTED}; }}
  .summary-row strong {{ color: {TEXT}; font-weight: 600; }}
</style></head><body>

<h1>GEPO Credit Spread Backtest</h1>
<div class="subtitle">Mercurio, Wu &amp; Xie (2020) — entropy-22-00805</div>
<div class="params-row">{params_chips}</div>

<div class="stats">
  <div class="stat"><div class="stat-label">starting bankroll</div><div class="stat-value">${start_br:,.0f}</div></div>
  <div class="stat"><div class="stat-label">final bankroll</div><div class="stat-value {'pos' if final_br>=start_br else 'neg'}">${final_br:,.0f}</div></div>
  <div class="stat"><div class="stat-label">total ROI</div><div class="stat-value {'pos' if roi>=0 else 'neg'}">{roi:.0f}%</div></div>
  <div class="stat"><div class="stat-label">win rate</div><div class="stat-value pos">{win_rate:.1f}%</div></div>
  <div class="stat"><div class="stat-label">sharpe ratio</div><div class="stat-value">{sharpe:.2f}</div></div>
  <div class="stat"><div class="stat-label">max drawdown</div><div class="stat-value neg">{max_dd:.1f}%</div></div>
</div>

<div class="charts">
  <div class="chart-card">
    <div class="chart-label">Equity curve</div>
    {eq_svg}
  </div>
  <div class="chart-card">
    <div class="chart-label">Weekly P&amp;L</div>
    {pnl_svg}
  </div>
</div>

<div class="charts" style="grid-template-columns: 1fr; margin-top: -8px;">
  <div class="chart-card">
    <div class="chart-label">Net direction · positive = bull-put surplus, negative = bear-call surplus</div>
    {dir_svg}
  </div>
</div>

<div class="section-label">Week-by-week trades &nbsp;<span style="color:#888780;font-weight:400;font-size:11px">(values shown at mid — no slippage haircut)</span></div>
''']

    # Build at-mid trajectory so the per-week section is internally consistent.
    # The aggregate stats / equity overlay at top of the report show the
    # slippage range; this section is the clean decision-view.
    trades_df = trades_df.copy()
    trades_df["dollar_pnl_mid"] = (
        trades_df["pnl_per_contract"] * trades_df["contracts"] * 100
    )
    weekly_pnl_mid = (
        trades_df.groupby("entry_date")["dollar_pnl_mid"].sum().to_dict()
    )
    bankroll_mid = config.STARTING_BANKROLL
    bankroll_eow_mid_lookup = {}
    for d in sorted(weekly_pnl_mid):
        bankroll_mid = max(bankroll_mid + weekly_pnl_mid[d], 0.01)
        bankroll_eow_mid_lookup[d] = bankroll_mid

    # Canonical (2026-05-13+): stored GROUND value is Γᵢ, the
    # risk-adjusted Kelly EV = Kelly EV · exp(−k·DKL) =
    # (exp(g) − 1) · exp(−k·DKL). Reads as "% per-trade expected wealth
    # gain after entropic ambiguity discount." No exp/log transform.
    score_label = "Γᵢ"

    for entry_date, week_trades in trades_df.groupby("entry_date"):
        week_trades = week_trades.sort_values("GROUND", ascending=False)
        week_row = weekly_df[weekly_df["entry_date"] == entry_date]
        if week_row.empty: continue
        # at-mid week numbers (Option B)
        pnl = float(weekly_pnl_mid.get(entry_date, 0.0))
        eow = float(bankroll_eow_mid_lookup.get(entry_date, config.STARTING_BANKROLL))
        bow = eow - pnl

        n_w = (week_trades["result"] == "WIN").sum()
        n_l = (week_trades["result"] == "LOSS").sum()
        n_p = (week_trades["result"] == "PARTIAL").sum()
        wr_pct = n_w / len(week_trades) * 100 if len(week_trades) > 0 else 0

        # Total dollars at risk this week, at mid (no slippage in max_loss)
        wagered = float((week_trades["contracts"] * week_trades["max_loss"] * 100).sum())

        # Weekly ROI as % of capital actually at risk
        week_roi = (pnl / wagered * 100) if wagered > 0 else 0
        roi_cls = 'pos' if week_roi >= 0 else 'neg'
        roi_sgn = '+' if week_roi >= 0 else ''

        pcl = 'pos' if pnl >= 0 else 'neg'
        psg = '+' if pnl >= 0 else ''
        date_str = pd.Timestamp(entry_date).strftime("%b %d, %Y")
        week_num = (pd.Timestamp(entry_date) - weekly_df["entry_date"].min()).days // 7 + 1

        parts.append(f'''
<div class="week-card">
  <div class="week-head">
    <div>
      <div class="week-title">Week {week_num} — {date_str}</div>
      <div class="week-sub">{len(week_trades)} trades · ${bow:,.2f} → ${eow:,.2f}</div>
    </div>
    <div style="text-align:right">
      <div class="week-pnl {pcl}">{psg}${pnl:,.2f}</div>
      <div class="week-sub" style="margin-top:2px">${wagered:,.0f} at risk · <span class="{roi_cls}">{roi_sgn}{week_roi:.1f}%</span></div>
    </div>
  </div>
  <table>
    <thead><tr>
      <th>ticker</th><th>direction</th><th>short / long</th><th>entry</th>
      <th>credit</th><th>max loss</th><th style="text-align:right">qty</th><th>{score_label}</th>
      <th>expiry</th>
      <th>result</th><th style="text-align:right">P&amp;L</th>
    </tr></thead><tbody>
''')

        for _, t in week_trades.iterrows():
            direction = "▲ bull put" if t["decision"] == "bull_put" else "▼ bear call"
            dclass = "dir-bull" if t["decision"] == "bull_put" else "dir-bear"
            # GROUND column stores Kelly EV · exp(−k·DKL) directly (positive
            # fractional return). Render as "+X.XX%" — no exp() transform.
            ground = float(t["GROUND"]) if pd.notna(t["GROUND"]) else 0.0
            gpct  = t["GROUND_pct"] if pd.notna(t["GROUND_pct"]) else 0
            g_str = f"{ground*100:+.2f}%"
            g_cls = "pos" if ground >= 0 else "neg"
            pnl_t = t["dollar_pnl_mid"]   # at-mid P&L (Option B)
            pcls = 'pos' if pnl_t >= 0 else 'neg'
            psgn = '+' if pnl_t >= 0 else ''

            parts.append(f'''      <tr>
        <td class="fw">{t["ticker"]}</td>
        <td><span class="{dclass}">{direction}</span></td>
        <td>${t["short_strike"]:.2f} / ${t["long_strike"]:.2f}</td>
        <td>${t["entry_price"]:.2f}</td>
        <td>${t["net_credit"]:.3f}</td>
        <td>${t["max_loss"]:.3f}</td>
        <td style="text-align:right">{int(t["contracts"]) if pd.notna(t.get("contracts")) else 1}×</td>
        <td class="{g_cls}"><span class="ground-bar"><span class="ground-bar-fill" style="width:{gpct:.0f}%"></span></span>{g_str}</td>
        <td>${t["expiry_price"]:.2f}</td>
        <td><span class="badge badge-{t["result"]}">{t["result"]}</span></td>
        <td class="fw {pcls}" style="text-align:right">{psgn}${pnl_t:,.2f}</td>
      </tr>
''')

        parts.append(f'''    </tbody></table>
  <div class="summary-row">
    <span><strong>{n_w}</strong> wins</span>
    <span><strong>{n_l}</strong> losses</span>
    <span><strong>{n_p}</strong> partials</span>
    <span><strong>{wr_pct:.0f}%</strong> win rate</span>
  </div>
</div>
''')

    parts.append("</body></html>")

    with open(out_path, "w") as f:
        f.write("".join(parts))
    print(f"Saved: {out_path}")
