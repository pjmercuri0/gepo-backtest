"""Compute Lo (2002) Sharpe CIs from raw weekly returns.

Aggregates trades to weekly P&L and computes weekly returns relative to
starting bankroll. Annualizes Sharpe at sqrt(52). For SPY, uses weekly
close-to-close returns aligned to the same week endings.
"""
import math
import pandas as pd
import numpy as np

TRADES_CSV = "/Users/mercurio/Downloads/gepo-backtest/output/all_trades-qty1-oot.csv"
SPY_CSV = "/Users/mercurio/Downloads/gepo-backtest/data/spy_us_d.csv"

INITIAL_BANKROLL = 10000.0  # standard convention; verified by reading bankroll_eow column

def lo_se(sr_ann, T, ann=52):
    """Lo (2002) standard error of annualized Sharpe under iid returns."""
    sr_w = sr_ann / math.sqrt(ann)
    var_sr_w = (1.0 + 0.5 * sr_w * sr_w) / T
    se_sr_w = math.sqrt(var_sr_w)
    return math.sqrt(ann) * se_sr_w

def sharpe_ann(returns, ann=52):
    if len(returns) < 2:
        return float("nan")
    mu = returns.mean()
    sd = returns.std(ddof=1)
    if sd == 0:
        return float("nan")
    return (mu / sd) * math.sqrt(ann)

def report(label, returns):
    T = len(returns)
    sr = sharpe_ann(returns)
    se = lo_se(sr, T)
    lo95 = sr - 1.96 * se
    hi95 = sr + 1.96 * se
    print(f"{label:30s}  T={T:4d}  Sharpe={sr:5.2f}  SE={se:5.2f}  95% CI=[{lo95:5.2f}, {hi95:5.2f}]")
    return {"label": label, "T": T, "sharpe": sr, "se": se, "lo": lo95, "hi": hi95}

# --- Strategy weekly returns ---
trades = pd.read_csv(TRADES_CSV, parse_dates=["entry_date", "expiry_date"])
print(f"Loaded {len(trades)} trades, dates {trades.entry_date.min().date()} to {trades.entry_date.max().date()}")

# aggregate to weekly P&L by entry_date
weekly_pnl = trades.groupby("entry_date")["dollar_pnl"].sum().sort_index()
print(f"Number of weeks with trades: {len(weekly_pnl)}")

# Project convention (results.py:138, 585): weekly returns are dollar P&L
# divided by FIXED starting bankroll, not rolling. Sharpe = mean/std * sqrt(52)
# with pandas default ddof=1.
weekly_ret = weekly_pnl / INITIAL_BANKROLL
weekly_ret.index = pd.to_datetime(weekly_ret.index)

# windows
in_sample = weekly_ret[(weekly_ret.index >= "2020-01-01") & (weekly_ret.index < "2025-01-01")]
holdout = weekly_ret[(weekly_ret.index >= "2025-01-01")]
extended = weekly_ret[(weekly_ret.index >= "2020-01-01")]

print("\n=== STRATEGY (qty=1) ===")
strat_results = {}
strat_results["in_sample"] = report("in-sample 2020-2024", in_sample)
strat_results["holdout"]   = report("holdout 2025-2026", holdout)
strat_results["extended"]  = report("extended 2020-2026", extended)

# --- SPY weekly returns (paper convention: daily resampled to weekly) ---
spy = pd.read_csv(SPY_CSV, parse_dates=["Date"]).set_index("Date").sort_index()
spy = spy[spy.index >= "2019-12-01"]
spy_weekly_close = spy["Close"].resample("W-FRI").last().dropna()
spy_weekly_ret = spy_weekly_close.pct_change().dropna()

spy_in   = spy_weekly_ret[(spy_weekly_ret.index >= "2020-01-01") & (spy_weekly_ret.index < "2025-01-01")]
spy_hold = spy_weekly_ret[(spy_weekly_ret.index >= "2025-01-01")]
spy_ext  = spy_weekly_ret[(spy_weekly_ret.index >= "2020-01-01")]

print("\n=== SPY (weekly close-to-close, aligned to strategy week markers) ===")
spy_results = {}
spy_results["in_sample"] = report("SPY 2020-2024", spy_in)
spy_results["holdout"]   = report("SPY 2025-2026", spy_hold)
spy_results["extended"]  = report("SPY 2020-2026", spy_ext)

# --- print LaTeX-ready table rows ---
def latex_row(label, T, sr, se, lo, hi):
    return f"{label:30s} & {T:3d} & {sr:.2f} & {se:.2f} & $[{lo:.2f},\\,{hi:.2f}]$ \\\\"

print("\n=== LaTeX table rows ===")
for k, v in strat_results.items():
    print(latex_row(v["label"], v["T"], v["sharpe"], v["se"], v["lo"], v["hi"]))
print("\\midrule")
for k, v in spy_results.items():
    print(latex_row(v["label"], v["T"], v["sharpe"], v["se"], v["lo"], v["hi"]))
