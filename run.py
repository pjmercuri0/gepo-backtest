"""
run.py
Single entry point for the GEPO credit spread backtest.

Usage:
    python run.py

Make sure your CSV files are in the data/ folder first.
"""

import os
import sys
import time

import config
import data_loader
import backtest
import results


def main():
    print("=" * 60)
    print("  GEPO CREDIT SPREAD BACKTEST")
    print("  Mercurio, Wu & Xie (2020) — entropy-22-00805")
    print("=" * 60)
    print(f"  Data dir:    {config.DATA_DIR}")
    print(f"  Date range:  {config.START_DATE} to {config.END_DATE}")
    print(f"  Tickers:     {len(config.SP100_TICKERS)}")
    print(f"  Bankroll:    ${config.STARTING_BANKROLL:,.0f}")
    print(f"  GROUND min:  {config.GROUND_THRESHOLD}")
    print("=" * 60)

    # ── Check data dir exists ─────────────────────────────────────────────
    os.makedirs(config.DATA_DIR, exist_ok=True)
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    csv_files = [f for f in os.listdir(config.DATA_DIR) if f.endswith(".csv")]
    if not csv_files:
        print(f"\n ERROR: No CSV files found in {config.DATA_DIR}/")
        print("  Please download the data from discountoptiondata.com")
        print("  and place the CSV files in the data/ folder.")
        sys.exit(1)

    # ── Load data ─────────────────────────────────────────────────────────
    t0 = time.time()
    df = data_loader.load_options_data()

    # Build expiry price lookup from the same data
    # (DataDate == ExpirationDate rows give us closing prices on expiry day)
    print("\nBuilding expiry price lookup...")
    expiry_prices = data_loader.get_expiry_prices(df)
    print(f"Expiry prices available for {len(expiry_prices):,} (ticker, date) pairs")

    # ── Run backtest ──────────────────────────────────────────────────────
    trades_df, weekly_df = backtest.run_backtest(df, expiry_prices)

    if trades_df.empty:
        print("\n No trades were generated. Check your data and config.")
        sys.exit(1)

    # ── Save and print results ────────────────────────────────────────────
    results.save_results(trades_df, weekly_df)

    elapsed = time.time() - t0
    print(f"\n Backtest complete in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
