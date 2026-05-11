"""Compute Lo (2002) AND Mertens (2002) Sharpe CIs from raw weekly returns.

Lo:      Var(SR_w) ≈ (1 + (1/2)·SR_w²) / T                 [iid + Gaussian]
Mertens: Var(SR_w) ≈ (1 + (1/2)·SR_w² − γ₃·SR_w + (γ₄/4)·SR_w²) / T
                                                            [iid, finite 4th moment]

γ₃ = sample skewness,  γ₄ = sample excess kurtosis (kurtosis − 3).

For a short-vol/credit-spread strategy with bounded P&L, γ₃ is materially
negative and γ₄ is positive, so Mertens widens the confidence intervals
relative to Lo. SPY weekly returns are mildly non-normal but closer to
Gaussian, so the gap between Lo and Mertens is smaller.

Annualization: weekly Sharpe scaled by sqrt(52). The variance formula is
applied at the weekly Sharpe scale and then annualized by sqrt(52).
"""
import math
import pandas as pd
import numpy as np

TRADES_CSV = "/Users/mercurio/Downloads/gepo-backtest/output/all_trades-qty1-oot.csv"
SPY_CSV = "/Users/mercurio/Downloads/gepo-backtest/data/spy_us_d.csv"

INITIAL_BANKROLL = 10000.0
ANN_FACTOR = 52


def sharpe_ann(returns, ann=ANN_FACTOR):
    if len(returns) < 2: return float("nan")
    mu, sd = returns.mean(), returns.std(ddof=1)
    return (mu / sd) * math.sqrt(ann) if sd > 0 else float("nan")


def lo_se(sr_ann, T, ann=ANN_FACTOR):
    """Lo (2002) iid-Gaussian standard error of annualized Sharpe."""
    sr_w = sr_ann / math.sqrt(ann)
    var_w = (1.0 + 0.5 * sr_w * sr_w) / T
    return math.sqrt(ann) * math.sqrt(var_w)


def mertens_se(sr_ann, gamma3, gamma4, T, ann=ANN_FACTOR):
    """Mertens (2002) higher-moment-adjusted SE of annualized Sharpe.

    γ₃ = skewness, γ₄ = excess kurtosis. Returns SE of the *annualized*
    Sharpe. For γ₃=0, γ₄=0 this reduces to lo_se.
    """
    sr_w = sr_ann / math.sqrt(ann)
    var_w = (1.0 + 0.5 * sr_w * sr_w - gamma3 * sr_w + 0.25 * gamma4 * sr_w * sr_w) / T
    var_w = max(var_w, 0.0)  # guard against pathological skew/kurt that drives variance negative
    return math.sqrt(ann) * math.sqrt(var_w)


def report(label, returns):
    T = len(returns)
    if T < 4:
        return None
    sr = sharpe_ann(returns)
    g3 = float(returns.skew())
    g4 = float(returns.kurt())   # pandas .kurt() returns *excess* kurtosis (Fisher's definition)
    se_lo  = lo_se(sr, T)
    se_mer = mertens_se(sr, g3, g4, T)
    lo_95_l = sr - 1.96 * se_lo
    lo_95_h = sr + 1.96 * se_lo
    mer_95_l = sr - 1.96 * se_mer
    mer_95_h = sr + 1.96 * se_mer
    print(f"{label:30s}  T={T:4d}  SR={sr:5.2f}  γ₃={g3:+.2f}  γ₄={g4:+.2f}  "
          f"Lo SE={se_lo:.2f}  Lo CI=[{lo_95_l:5.2f}, {lo_95_h:5.2f}]  "
          f"Mertens SE={se_mer:.2f}  Mertens CI=[{mer_95_l:5.2f}, {mer_95_h:5.2f}]")
    return {
        "label": label, "T": T, "sharpe": sr, "gamma3": g3, "gamma4": g4,
        "se_lo": se_lo, "lo_lo": lo_95_l, "lo_hi": lo_95_h,
        "se_mer": se_mer, "mer_lo": mer_95_l, "mer_hi": mer_95_h,
    }


# --- Strategy weekly returns ---
trades = pd.read_csv(TRADES_CSV, parse_dates=["entry_date", "expiry_date"])
print(f"Loaded {len(trades)} trades, dates {trades.entry_date.min().date()} to {trades.entry_date.max().date()}")

weekly_pnl = trades.groupby("entry_date")["dollar_pnl"].sum().sort_index()
weekly_ret = weekly_pnl / INITIAL_BANKROLL
weekly_ret.index = pd.to_datetime(weekly_ret.index)
print(f"Weeks with trades: {len(weekly_ret)}")

in_sample = weekly_ret[(weekly_ret.index >= "2020-01-01") & (weekly_ret.index < "2025-01-01")]
holdout   = weekly_ret[weekly_ret.index >= "2025-01-01"]
extended  = weekly_ret[weekly_ret.index >= "2020-01-01"]

print("\n=== STRATEGY (qty=1) ===")
strat_rows = []
strat_rows.append(report("in-sample 2020-2024", in_sample))
strat_rows.append(report("holdout 2025-2026", holdout))
strat_rows.append(report("extended 2020-2026", extended))

# --- SPY weekly returns ---
spy = pd.read_csv(SPY_CSV, parse_dates=["Date"]).set_index("Date").sort_index()
spy = spy[spy.index >= "2019-12-01"]
spy_weekly_close = spy["Close"].resample("W-FRI").last().dropna()
spy_weekly_ret   = spy_weekly_close.pct_change().dropna()

spy_in   = spy_weekly_ret[(spy_weekly_ret.index >= "2020-01-01") & (spy_weekly_ret.index < "2025-01-01")]
spy_hold = spy_weekly_ret[spy_weekly_ret.index >= "2025-01-01"]
spy_ext  = spy_weekly_ret[spy_weekly_ret.index >= "2020-01-01"]

print("\n=== SPY (W-FRI close-to-close) ===")
spy_rows = []
spy_rows.append(report("SPY 2020-2024", spy_in))
spy_rows.append(report("SPY 2025-2026", spy_hold))
spy_rows.append(report("SPY 2020-2026", spy_ext))

# --- LaTeX table rows ---
def latex_row(r):
    return (f"{r['label']:30s} & {r['T']:3d} & {r['sharpe']:.2f} & "
            f"{r['gamma3']:+.2f} & {r['gamma4']:+.2f} & "
            f"{r['se_lo']:.2f} & $[{r['lo_lo']:.2f},\\,{r['lo_hi']:.2f}]$ & "
            f"{r['se_mer']:.2f} & $[{r['mer_lo']:.2f},\\,{r['mer_hi']:.2f}]$ \\\\")

print("\n=== LaTeX table rows (Lo + Mertens side-by-side) ===")
print("% header: window & T & SR & γ₃ & γ₄ & Lo SE & Lo 95% CI & Mertens SE & Mertens 95% CI \\\\")
for r in strat_rows:
    if r: print(latex_row(r))
print("\\midrule")
for r in spy_rows:
    if r: print(latex_row(r))
