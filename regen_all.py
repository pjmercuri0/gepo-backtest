# -*- coding: utf-8 -*-
"""
Regenerate all canonical weekly reports under today's canon.

Variants:
  - qty1     : in-sample 2020-2024, qty=1
  - qty2     : in-sample 2020-2024, qty=2
  - qty1-oot : extended 2020-2026, qty=1
  - qty2-oot : extended 2020-2026, qty=2
  - qtyx     : in-sample 2020-2024, qty=dyn10k (floor(bankroll/$10k))
  - qtyx-oot : extended 2020-2026, qty=dyn10k

For each variant: runs the backtest at 0¢ slippage (selection-canonical),
saves all_trades-{suffix}.csv, builds the multi-slippage equity overlay,
and writes weekly_report-{suffix}.html. Finishes by running patch_yield
to inject the 7-tile stats grid (starting + final + yield + wagered + win
+ sharpe + dd).
"""
import os
import sys

import pandas as pd

import config
import data_loader
import backtest
import spreads
import results
import overlay_slippage as ov
import patch_yield


VARIANTS = [
    # (suffix,     start,        end,          sizing)
    ("qty1",     "2020-01-01", "2024-12-30", "1"),
    ("qty2",     "2020-01-01", "2024-12-30", "2"),
    ("qty1-oot", "2020-01-01", "2026-05-04", "1"),
    ("qty2-oot", "2020-01-01", "2026-05-04", "2"),
    ("qtyx",     "2020-01-01", "2024-12-30", "dyn10k"),
    ("qtyx-oot", "2020-01-01", "2026-05-04", "dyn10k"),
]

OOT_DATE = pd.Timestamp("2026-05-04")


def setup_canonical_config(start, end, sizing):
    """Per-variant overrides only. All other knobs come from config.py canonical defaults."""
    config.START_DATE = start
    config.END_DATE   = end
    config.SIZING     = sizing


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
    # Apply canonical MAX_CREDIT_RATIO (no cap on b).
    config.MAX_CREDIT_RATIO        = config.BACKTEST_MAX_CREDIT_RATIO


def run_variant(suffix, start, end, sizing, df_full, expiry_prices):
    print(f"\n========= {suffix}  ({start} → {end}, qty={sizing}) =========",
          flush=True)
    setup_canonical_config(start, end, sizing)
    setup_filters()

    df = df_full[
        (df_full["DataDate"] >= pd.Timestamp(start)) &
        (df_full["DataDate"] <= pd.Timestamp(end))
    ]
    print(f"   data rows: {len(df):,}", flush=True)

    trades_df, weekly_df = backtest.run_backtest(
        df, expiry_prices, pd.DataFrame(), 0,
        top_n=config.TOP_N, sizing=sizing,
        use_drift=False, drift_lookup=None,
    )
    if trades_df.empty:
        print(f"   ! no trades for {suffix}")
        return

    # results.save_results writes to config.TRADES_CSV / RESULTS_CSV / EQUITY_CURVE_PNG
    # plus a hardcoded weekly_report.html — we rename it after.
    config.TRADES_CSV       = f"all_trades-{suffix}.csv"
    config.RESULTS_CSV      = f"results-{suffix}.csv"
    config.EQUITY_CURVE_PNG = f"equity_curve-{suffix}.png"
    results.save_results(trades_df, weekly_df)

    src_html = os.path.join(config.OUTPUT_DIR, "weekly_report.html")
    dst_html = os.path.join(config.OUTPUT_DIR, f"weekly_report-{suffix}.html")
    os.replace(src_html, dst_html)

    # Multi-slippage overlay (post-hoc haircut on the same trade list)
    weekly_dict = {0.00: weekly_df}
    stats_dict  = {0.00: ov.stats_for(weekly_df, trades_df=trades_df, slip=0.0)}
    for slip in [0.01, 0.02, 0.03]:
        wdf = ov.derive_weekly_at_slippage(trades_df, slip)
        weekly_dict[slip] = wdf
        stats_dict[slip]  = ov.stats_for(wdf, trades_df=trades_df, slip=slip)

    spy_df  = ov.load_spy_for_overlay(start, end)
    new_svg = ov.build_overlay_svg(weekly_dict, stats_dict, spy_df)
    ov.patch_html(dst_html, new_svg, stats_dict)

    s = stats_dict[0.00]
    print(f"   ✓ {suffix}: final ${s['final']:,.0f}  ann {s['ann']:.1f}%  "
          f"Sharpe {s['sharpe']:.2f}  DD {s['dd']:.1f}%  "
          f"yield {s['yield_pct']:.2f}%  trades {s['n_trades']}", flush=True)


def main():
    print("Loading options data once...", flush=True)
    df_full       = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()
    print(f"   loaded {len(df_full):,} rows", flush=True)

    for suffix, start, end, sizing in VARIANTS:
        run_variant(suffix, start, end, sizing, df_full, expiry_prices)

    print("\n========= patching yield/wagered stat tiles =========", flush=True)
    patch_yield.main()


if __name__ == "__main__":
    main()
