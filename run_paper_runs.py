"""
Run the paper's outstanding numerical artifacts under base-3 canon:

  1. k-sweep: DKL_K ∈ {10, 15, 20, 25, 30} on in-sample 2020-2024 qty=1
  2. Single-factor baselines: G_only and DKL_only on the same window

Saves all 7 rows to output/paper_runs.csv. In-sample only — that's
sufficient to evaluate the GROUND ranker against its components and
to show the k-sweep plateau (or lack thereof).
"""
import math
import os

import pandas as pd

import config
import data_loader
import backtest
import spreads
import ground


START   = "2020-01-01"
END     = "2024-12-30"
SIZING  = "1"
TOP_N   = 5

OUT_CSV = os.path.join(config.OUTPUT_DIR, "paper_runs.csv")


def setup_canonical():
    """Window-only override; all other knobs come from config.py canonical defaults."""
    config.START_DATE = START
    config.END_DATE   = END


def setup_filters():
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP          = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER          = True
    spreads.REGIME_PER_TICKER      = False
    spreads.GAP_FILTER             = False
    spreads.GAP_LOOKUP             = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.VIX_LOOKUP             = {}
    spreads.SLIPPAGE_CENTS         = 0.0
    config.MAX_CREDIT_RATIO        = config.BACKTEST_MAX_CREDIT_RATIO


def stats_for(weekly_df, trades_df):
    if weekly_df.empty:
        return None
    final = float(weekly_df["bankroll_eow"].iloc[-1])
    n_weeks = len(weekly_df)
    total_roi = (final / config.STARTING_BANKROLL - 1) * 100
    ann = total_roi / (n_weeks / 52) if n_weeks > 0 else 0
    weekly_ret = weekly_df["week_pnl"] / config.STARTING_BANKROLL
    sharpe = (weekly_ret.mean() / weekly_ret.std() * math.sqrt(52)) \
        if weekly_ret.std() > 0 else 0
    rmx = weekly_df["bankroll_eow"].cummax()
    dd = ((weekly_df["bankroll_eow"] - rmx) / rmx).min() * 100
    n_trades = len(trades_df)
    n_wins = int((trades_df["result"] == "WIN").sum())
    win_rate = n_wins / n_trades * 100 if n_trades > 0 else 0
    pnl = trades_df["contracts"] * trades_df["pnl_per_contract"] * 100
    wagered = trades_df["contracts"] * trades_df["max_loss"] * 100
    yield_pct = pnl.sum() / wagered.sum() * 100 if wagered.sum() > 0 else 0
    return dict(n_trades=n_trades, win_rate=win_rate, final=final, ann=ann,
                sharpe=sharpe, dd=dd, yield_pct=yield_pct,
                total_wagered=wagered.sum(), total_pnl=pnl.sum())


def run_one(label, df, expiry_prices):
    print(f"\n>>> {label}", flush=True)
    trades_df, weekly_df = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=TOP_N, sizing=SIZING,
        use_drift=False, drift_lookup=None,
    )
    s = stats_for(weekly_df, trades_df)
    if s is None:
        print(f"   ! no trades")
        return None
    print(f"   ✓ trades {s['n_trades']:,}  Sharpe {s['sharpe']:.2f}  "
          f"ann {s['ann']:.1f}%  DD {s['dd']:.1f}%  yield {s['yield_pct']:.2f}%",
          flush=True)
    return s


def main():
    setup_canonical()
    print("Loading data...", flush=True)
    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    setup_filters()
    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(START)) &
        (df_full["DataDate"] <= pd.Timestamp(END))
    ]
    print(f"   {len(df):,} rows over {START} → {END}", flush=True)

    rows = []

    # 1) k-sweep with full GROUND
    ground.RANKING_MODE = "GROUND"
    config.GROUND_THRESHOLD = 0.0
    for k in [10, 15, 20, 25, 30]:
        ground.DKL_K = float(k)
        s = run_one(f"GROUND k={k}", df, expiry_prices)
        if s:
            s["mode"] = "GROUND"; s["k"] = k
            rows.append(s)
            pd.DataFrame(rows).to_csv(OUT_CSV, index=False)

    # 2) Single-factor baselines (no k, no threshold)
    config.GROUND_THRESHOLD = float("-inf")  # take all valid candidates

    ground.RANKING_MODE = "G_only"
    s = run_one("G_only baseline", df, expiry_prices)
    if s:
        s["mode"] = "G_only"; s["k"] = None
        rows.append(s)
        pd.DataFrame(rows).to_csv(OUT_CSV, index=False)

    ground.RANKING_MODE = "DKL_only"
    s = run_one("DKL_only baseline", df, expiry_prices)
    if s:
        s["mode"] = "DKL_only"; s["k"] = None
        rows.append(s)

    pd.DataFrame(rows).to_csv(OUT_CSV, index=False)
    print(f"\n>>> wrote {OUT_CSV}", flush=True)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
