"""
run.py
Entry point for the GEPO credit spread backtest.
Per-ticker empirical (p, q, ro) from past resolved trade outcomes.

Usage:
    python3 run.py -s 2022-04-01 -e 2022-04-30 -g 0.5 --lookback 30
"""

import os
import sys
import time
import argparse

import pandas as pd

import config
import data_loader
import backtest
import results
import historical_probs
import spreads


def parse_args():
    p = argparse.ArgumentParser(description="GEPO credit spread backtest")
    p.add_argument("-s", "--start",        default=config.START_DATE)
    p.add_argument("-e", "--end",          default=config.END_DATE)
    p.add_argument("-g", "--ground",       type=float, default=config.GROUND_THRESHOLD)
    p.add_argument("-b", "--bankroll",     type=float, default=config.STARTING_BANKROLL)
    p.add_argument("--lookback",           type=int,   default=30,
                   help="Lookback window (days) for empirical probabilities")
    p.add_argument("--delta-target",       type=float, default=config.DELTA_TARGET)
    p.add_argument("--delta-min",          type=float, default=config.DELTA_MIN)
    p.add_argument("--delta-max",          type=float, default=config.DELTA_MAX)
    p.add_argument("--dte-min",            type=int,   default=config.DTE_MIN)
    p.add_argument("--dte-max",            type=int,   default=config.DTE_MAX)
    p.add_argument("--top-n",              type=int,   default=None,
                   help="Keep best N trades per week by GROUND (default: all)")
    p.add_argument("--sizing",             type=str,   default="2kelly",
                   help="'kelly'/'1kelly' = full-Kelly equal-dollar; "
                        "'2kelly' = half-Kelly (default); '4kelly' = quarter-Kelly; "
                        "any positive integer N = flat N contracts per trade "
                        "(e.g. '1', '2', '5')")
    p.add_argument("--max-credit-ratio",   type=float, default=config.MAX_CREDIT_RATIO,
                   help="Reject candidates with net_credit/max_loss above this. "
                        "Default: no cap. Use e.g. 3.0 to exclude extreme high-ratio spreads.")
    p.add_argument("--min-theta-credit",   type=float, default=float("-inf"),
                   help="Minimum (net_theta * DTE / net_credit). Quality filter "
                        "ensuring expected theta decay covers a meaningful fraction "
                        "of the credit collected. 0.5 is a typical threshold; "
                        "default (−∞) disables the filter entirely (lets through "
                        "candidates with negative theta_credit_ratio too).")
    p.add_argument("--max-max-loss",       type=float,
                   default=getattr(config, "MAX_MAX_LOSS", float("inf")),
                   help="Reject candidates with max_loss above this ($/share). "
                        "Default: no cap. Use e.g. 5.0 to exclude wide-strike spreads "
                        "on expensive underlyings.")
    p.add_argument("--use-drift",          action="store_true",
                   help="Use drift-adjusted real-world probabilities (per-ticker "
                        "trailing return as drift μ in BS d2). Default: off "
                        "(uses pure Greek-based estimator p=1-delta_short).")
    p.add_argument("--drift-window",       type=int, default=60,
                   help="Trailing window in business days for per-ticker drift "
                        "estimation. Default: 60.")
    p.add_argument("--ground-k",           type=float, default=20.0,
                   help="Amplification factor in GROUND v3 = G / 3 ** (k * DKL). "
                        "Default: 20. Higher k makes the divergence penalty more "
                        "aggressive; lower k makes GROUND closer to pure G. "
                        "k is in trits (log-base-3); see config.LOG_BASE.")
    p.add_argument("--use-rv-blend",       action="store_true",
                   help="Use IV-RV blended vol in the BS d2 probability "
                        "estimator (σ_eff = w*IV + (1-w)*RV). Captures the "
                        "vol risk premium effect. Implies real-world drift "
                        "adjustment if --use-drift is also set.")
    p.add_argument("--rv-window",          type=int, default=30,
                   help="Trailing window in business days for per-ticker "
                        "realized vol estimation. Default: 30.")
    p.add_argument("--iv-weight",          type=float, default=0.5,
                   help="Weight on IV in the blended vol (0.5 = equal mix; "
                        "1.0 = IV only; 0.0 = RV only). Default: 0.5.")
    p.add_argument("--use-skew-adj",       action="store_true",
                   help="Adjust delta-implied p_win by the put-skew slope "
                        "(IV_long - IV_short)/IV_short. Steep skew → market "
                        "overpaying for OTM fear → real p_win > delta-implied.")
    p.add_argument("--skew-alpha",         type=float, default=0.5,
                   help="Scale factor for skew adjustment. Default: 0.5. "
                        "Higher = stronger skew effect on p.")
    p.add_argument("--regime-filter",      action="store_true",
                   help="Restrict trades by market regime: bull (SPY > SMA) → "
                        "only bull_put; bear (SPY < SMA) → only bear_call. "
                        "Default: off (both directions allowed).")
    p.add_argument("--regime-window",      type=int, default=50,
                   help="Trailing SMA window in trading days for regime "
                        "classification. Default: 50.")
    p.add_argument("--slippage-cents",     type=float, default=None,
                   help="Per-leg fixed-dollar slippage from mid. 0 = pure "
                        "mid-mid. 0.03 = give up 3¢ per leg (≈6¢/spread). "
                        "Default (unset) uses the quartile-of-bid-ask model.")
    p.add_argument("--gap-filter",         action="store_true",
                   help="Reject any candidate when SPY's overnight gap_pct on "
                        "entry_date is below --gap-threshold. Reads SPY daily "
                        "history from data/spy_us_d.csv.")
    p.add_argument("--gap-threshold",      type=float, default=-0.01,
                   help="Lower-bound gap_pct (decimal). Default: -0.01 (-1%%).")
    p.add_argument("--low-vix-bullput-filter", action="store_true",
                   help="Skip bull_put candidates when VIX < --low-vix-threshold "
                        "on entry_date. Reads VIX from analysis/vix_daily.parquet.")
    p.add_argument("--low-vix-threshold",  type=float, default=15.0,
                   help="VIX level below which bull_puts are rejected. Default: 15.0.")
    p.add_argument("--holiday-filter",     action="store_true",
                   help="Reject any spread whose holding window contains an "
                        "NYSE full-close holiday. Short trading weeks have "
                        "less theta capture and bigger gap risk.")
    p.add_argument("--earnings-filter",    action="store_true",
                   help="Reject any spread whose holding window contains an "
                        "earnings announcement for the ticker. Reads "
                        "data/earnings_calendar.csv (cols: Symbol, EarningsDate). "
                        "Run fetch_earnings.py once to build the file.")
    p.add_argument("--regime-source",      type=str, default="spy",
                   choices=["spy", "oef", "per_ticker"],
                   help="Benchmark used for regime classification. "
                        "'spy' (default) reads data/spy_us_d.csv or "
                        "data/spy_history.csv; 'oef' reads data/oef_history.csv; "
                        "'per_ticker' uses each ticker's own Monday-sampled "
                        "price vs. its own rolling SMA (no external file).")
    return p.parse_args()


def main():
    args = parse_args()

    config.START_DATE        = args.start
    config.END_DATE          = args.end
    config.GROUND_THRESHOLD  = args.ground
    config.STARTING_BANKROLL = args.bankroll
    config.DELTA_TARGET      = args.delta_target
    config.DELTA_MIN         = args.delta_min
    config.DELTA_MAX         = args.delta_max
    config.DTE_MIN           = args.dte_min
    config.DTE_MAX           = args.dte_max
    config.MAX_CREDIT_RATIO  = args.max_credit_ratio
    config.MAX_MAX_LOSS      = args.max_max_loss
    config.MIN_THETA_CREDIT_RATIO = args.min_theta_credit

    # Pass through the GROUND amplification factor (v3 formula)
    import ground
    ground.DKL_K = args.ground_k

    print("=" * 60)
    print("  GEPO BACKTEST (per-ticker empirical)")
    print("=" * 60)
    print(f"  Date range:    {config.START_DATE} to {config.END_DATE}")
    print(f"  Lookback:      {args.lookback} days")
    print(f"  GROUND min:    {config.GROUND_THRESHOLD}")
    print(f"  Delta target:  {config.DELTA_TARGET} [{config.DELTA_MIN}-{config.DELTA_MAX}]")
    print(f"  DTE range:     {config.DTE_MIN}-{config.DTE_MAX} days")
    print(f"  Credit ratio:  min {config.MIN_CREDIT_RATIO}, "
          f"max {config.MAX_CREDIT_RATIO if config.MAX_CREDIT_RATIO != float('inf') else 'none'}")
    print(f"  Max max_loss:  "
          f"{('$' + str(config.MAX_MAX_LOSS) + '/share') if config.MAX_MAX_LOSS != float('inf') else 'no cap'}")
    print(f"  Min θ/credit:  "
          f"{config.MIN_THETA_CREDIT_RATIO if config.MIN_THETA_CREDIT_RATIO > 0 else 'off'}")
    print(f"  Top-N/week:    {args.top_n if args.top_n else 'all'}")
    print(f"  Sizing:        {args.sizing}")
    print(f"  Drift:         "
          f"{'on (window=' + str(args.drift_window) + 'd)' if args.use_drift else 'off (pure Greek)'}")
    print(f"  SPY gap:       "
          f"{'on (skip < ' + str(args.gap_threshold*100) + '%)' if args.gap_filter else 'off'}")
    print(f"  Low-VIX bull:  "
          f"{'on (skip bull_put when VIX<' + str(args.low_vix_threshold) + ')' if args.low_vix_bullput_filter else 'off'}")
    print(f"  Holiday:       {'on' if args.holiday_filter else 'off'}")
    print(f"  Earnings:      {'on' if args.earnings_filter else 'off'}")
    print(f"  Regime filter: "
          f"{'on (' + args.regime_source.upper() + ', SMA=' + str(args.regime_window) + 'd)' if args.regime_filter else 'off'}")
    print("=" * 60)

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)
    t0 = time.time()

    print("\nLoading data from parquet...")
    df_full = data_loader.load_options_data()
    expiry_prices = data_loader.load_all_data_raw()

    ep_lookup = (
        expiry_prices
        .set_index(["Symbol", "ExpirationDate"])["ExpiryPrice"]
        .to_dict()
    )

    backtest_start = pd.Timestamp(config.START_DATE)
    backtest_end   = pd.Timestamp(config.END_DATE)

    # Build historical outcomes — diagnostic only under the Greek-based
    # estimator. Skipped entirely when --lookback 0; non-fatal if empty.
    if args.lookback > 0:
        history_start  = backtest_start - pd.Timedelta(days=args.lookback + 7)
        df_history_input = df_full[
            (df_full["DataDate"] >= history_start) &
            (df_full["DataDate"] <  backtest_start)
        ]
        print(f"\nHistory input: {len(df_history_input):,} rows from "
              f"{history_start.date()} to {(backtest_start - pd.Timedelta(days=1)).date()}")

        history = historical_probs.build_historical_outcomes(df_history_input, ep_lookup)
        if history.empty:
            print("Note: No historical data available before start date "
                  "(diagnostic only, scoring uses Greeks).")
            history = pd.DataFrame()
    else:
        print("\nHistory: skipped (--lookback 0, diagnostic-only feature).")
        history = pd.DataFrame()

    df_backtest = df_full[
        (df_full["DataDate"] >= backtest_start) &
        (df_full["DataDate"] <= backtest_end)
    ]
    print(f"Backtest rows: {len(df_backtest):,}")

    if df_backtest.empty:
        print("\nERROR: No data in backtest range.")
        sys.exit(1)

    # Build per-ticker drift lookup if enabled. Uses df_full (incl. pre-backtest
    # data) so warmup days are available even when the backtest starts at the
    # first row of the parquet.
    drift_lookup = None
    if args.use_drift or args.use_rv_blend:
        drift_lookup = historical_probs.build_drift_table(
            df_full, window_days=args.drift_window
        )

    # Build per-ticker RV lookup if RV-blend is enabled.
    rv_lookup = None
    if args.use_rv_blend:
        rv_lookup = historical_probs.build_rv_table(
            df_full, window_days=args.rv_window
        )

    # Fill model: pass through to spreads module
    spreads.SLIPPAGE_CENTS = args.slippage_cents

    # SPY gap filter
    if args.gap_filter:
        spy_csv = os.path.join(config.DATA_DIR, "spy_us_d.csv")
        spreads.GAP_LOOKUP = spreads.load_spy_gap_lookup(spy_csv)
        if not spreads.GAP_LOOKUP:
            print(f"\nERROR: --gap-filter requested but no SPY data at {spy_csv}.")
            sys.exit(1)
        spreads.GAP_FILTER    = True
        spreads.GAP_THRESHOLD = args.gap_threshold
    else:
        spreads.GAP_FILTER = False
        spreads.GAP_LOOKUP = {}

    # Low-VIX bull_put filter
    if args.low_vix_bullput_filter:
        vix_path = os.path.join(os.path.dirname(__file__), "analysis", "vix_daily.parquet")
        spreads.VIX_LOOKUP = spreads.load_vix_lookup(vix_path)
        if not spreads.VIX_LOOKUP:
            print(f"\nERROR: --low-vix-bullput-filter requested but no VIX data at {vix_path}.")
            sys.exit(1)
        spreads.LOW_VIX_BULLPUT_FILTER = True
        spreads.LOW_VIX_THRESHOLD      = args.low_vix_threshold
    else:
        spreads.LOW_VIX_BULLPUT_FILTER = False
        spreads.VIX_LOOKUP             = {}

    # Holiday filter (NYSE-closed days during holding window)
    spreads.HOLIDAY_FILTER = args.holiday_filter

    # Load earnings calendar if enabled.
    if args.earnings_filter:
        earnings_csv = os.path.join(config.DATA_DIR, "earnings_calendar.csv")
        spreads.EARNINGS_LOOKUP = spreads.load_earnings_lookup(earnings_csv)
        if not spreads.EARNINGS_LOOKUP:
            print(f"\nERROR: --earnings-filter requested but no usable data at "
                  f"{earnings_csv}. Run fetch_earnings.py first.")
            sys.exit(1)
        spreads.EARNINGS_FILTER = True
    else:
        spreads.EARNINGS_FILTER = False
        spreads.EARNINGS_LOOKUP = {}

    # Build regime lookup if enabled.
    if args.regime_filter:
        if args.regime_source == "per_ticker":
            spreads.REGIME_LOOKUP = spreads.build_per_ticker_regime_lookup(
                df_full, sma_window_days=args.regime_window
            )
            spreads.REGIME_PER_TICKER = True
        else:
            if args.regime_source == "spy":
                candidate_paths = [
                    os.path.join(config.DATA_DIR, "spy_history.csv"),
                    os.path.join(config.DATA_DIR, "spy_us_d.csv"),
                ]
            else:  # oef
                candidate_paths = [
                    os.path.join(config.DATA_DIR, "oef_history.csv"),
                ]
            regime_csv = next((p for p in candidate_paths if os.path.exists(p)), None)
            if regime_csv is None:
                print(f"\nERROR: regime filter enabled but no benchmark file found "
                      f"for source '{args.regime_source}'. Tried: {candidate_paths}")
                sys.exit(1)
            spreads.REGIME_LOOKUP = spreads.build_regime_lookup(
                regime_csv, sma_window=args.regime_window
            )
            spreads.REGIME_PER_TICKER = False
        spreads.REGIME_FILTER = True
    else:
        spreads.REGIME_FILTER     = False
        spreads.REGIME_LOOKUP     = None
        spreads.REGIME_PER_TICKER = False

    # Mirror runtime settings into config so results.py can render them
    config.REGIME_FILTER = args.regime_filter
    config.REGIME_WINDOW = args.regime_window
    config.REGIME_SOURCE = args.regime_source
    config.USE_DRIFT     = args.use_drift
    config.DRIFT_WINDOW  = args.drift_window
    config.TOP_N         = args.top_n
    config.SIZING        = args.sizing
    config.LOOKBACK      = args.lookback

    trades_df, weekly_df = backtest.run_backtest(
        df_backtest, expiry_prices, history, args.lookback,
        top_n=args.top_n, sizing=args.sizing,
        use_drift=args.use_drift, drift_lookup=drift_lookup,
        use_rv_blend=args.use_rv_blend, rv_lookup=rv_lookup,
        iv_weight=args.iv_weight,
        use_skew_adj=args.use_skew_adj, skew_alpha=args.skew_alpha,
    )

    if trades_df.empty:
        print("\nNo trades generated.")
        sys.exit(1)

    results.save_results(trades_df, weekly_df)

    elapsed = time.time() - t0
    print(f"\nBacktest complete in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
