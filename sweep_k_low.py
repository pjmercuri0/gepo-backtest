"""Sweep k from 1 to 10 at canonical clean filters."""
from __future__ import annotations
import math, os, sys
import pandas as pd
import config, data_loader, backtest, spreads, ground

START, END, SIZING, TOP_N = "2020-01-01", "2024-12-30", "1", 5

def setup():
    config.START_DATE, config.END_DATE, config.SIZING = START, END, SIZING
    spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(spy_csv, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False; spreads.GAP_LOOKUP = {}
    spreads.LOW_VIX_BULLPUT_FILTER = False; spreads.VIX_LOOKUP = {}
    spreads.SLIPPAGE_CENTS = 0.0

def stats(label, weekly, trades):
    if weekly.empty or trades.empty:
        print(f"  {label}: no trades"); return
    final = float(weekly["bankroll_eow"].iloc[-1])
    wret = weekly["week_pnl"] / config.STARTING_BANKROLL
    sr = (wret.mean()/wret.std()*math.sqrt(52)) if wret.std()>0 else 0
    rmx = weekly["bankroll_eow"].cummax()
    dd = ((weekly["bankroll_eow"]-rmx)/rmx).min()*100
    pnl = (trades["contracts"]*trades["pnl_per_contract"]*100).sum()
    wagered = (trades["contracts"]*trades["max_loss"]*100).sum()
    yld = pnl/wagered*100 if wagered>0 else 0
    print(f"  {label:18s}  trades={len(trades):4d}  final=${final:>9,.0f}  Sharpe={sr:.2f}  yield={yld:5.2f}%  DD={dd:5.1f}%")

def main():
    print(f"\n== k-sweep k∈{{1..10}} at clean filters, qty=1 in-sample {START}→{END} ==\n")
    setup()
    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    df = df_full[(df_full["DataDate"]>=pd.Timestamp(START)) & (df_full["DataDate"]<=pd.Timestamp(END))]
    print(f"  {len(df):,} option rows\n")
    ground.RANKING_MODE = "GROUND"
    for k in range(1, 11):
        ground.DKL_K = float(k)
        trades, weekly = backtest.run_backtest(
            df, expiry_prices, pd.DataFrame(), 0,
            top_n=TOP_N, sizing=SIZING, use_drift=False, drift_lookup=None,
        )
        stats(f"actual-b k={k}", weekly, trades)
    return 0

if __name__ == "__main__":
    sys.exit(main())
