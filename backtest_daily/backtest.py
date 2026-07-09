"""
backtest.py
Runs the weekly backtest loop:
  1. For each entry date, build spread candidates
  2. Score with GROUND
  3. Select best side per ticker (or PASS)
  4. Look up expiry price
  5. Calculate outcome and P&L
  6. Track bankroll week by week
"""

import pandas as pd
import numpy as np
from tqdm import tqdm

import config
import spreads
import ground


def run_backtest(df: pd.DataFrame,
                 expiry_prices: pd.DataFrame,
                 history: pd.DataFrame,
                 lookback_days: int,
                 top_n: int = None,
                 sizing: str = "kelly",
                 use_drift: bool = False,
                 drift_lookup: dict = None,
                 use_rv_blend: bool = False,
                 rv_lookup: dict = None,
                 iv_weight: float = 0.5,
                 use_skew_adj: bool = False,
                 skew_alpha: float = 0.5) -> tuple:
    """
    Run the full backtest over all entry dates.

    Parameters
    ----------
    df            : filtered options DataFrame from data_loader
    expiry_prices : DataFrame with columns [Symbol, ExpirationDate, ExpiryPrice]
    history       : pre-built historical outcomes (diagnostic only under
                    the Greek-based estimator)
    lookback_days : kept for back-compat
    top_n         : keep best N trades per week by GROUND, or None for all
    sizing        : 'one'    – flat 1 contract per trade.
                  : 'dyn10k' – step-function sizing: floor(bankroll/$10k) contracts, min 1.
                  : 'kelly'  – full-Kelly equal-dollar sizing.
                  : '1kelly' – alias for 'kelly'.
                  : '2kelly' – half-Kelly equal-dollar sizing.
                  : '4kelly' – quarter-Kelly equal-dollar sizing.

                  For Kelly variants, all selected trades that week get
                  equal dollar exposure:
                    deploy = (1/k) * mean(w_star) * STARTING_BANKROLL
                    per_trade_dollars = deploy / n_trades
                    contracts = round(per_trade_dollars / (max_loss * 100))
                  Contracts floored at 1, capped at config.MAX_CONTRACTS.
                  Sizing is against fixed STARTING_BANKROLL (no compounding).

    Returns
    -------
    trades_df, weekly_df
    """
    entry_dates = sorted(df["DataDate"].unique())
    print(f"\nRunning backtest over {len(entry_dates)} weeks...")

    # Configure ground.py module-level state for this run
    ground.USE_DRIFT    = use_drift
    ground.DRIFT_LOOKUP = drift_lookup if drift_lookup is not None else {}
    ground.USE_RV_BLEND = use_rv_blend
    ground.RV_LOOKUP    = rv_lookup if rv_lookup is not None else {}
    ground.IV_WEIGHT    = iv_weight
    ground.USE_SKEW_ADJ = use_skew_adj
    ground.SKEW_ALPHA   = skew_alpha
    if use_skew_adj:
        print(f"  Using skew-adjusted probabilities (α={skew_alpha})")
    elif use_rv_blend:
        print(f"  Using IV-RV blended vol "
              f"(IV weight={iv_weight}, {len(ground.RV_LOOKUP):,} RV entries"
              f"{', drift on' if use_drift else ', drift off'})")
    elif use_drift:
        print(f"  Using drift-adjusted probabilities ({len(ground.DRIFT_LOOKUP):,} drift entries)")
    else:
        print(f"  Using Greek-based probabilities (no drift adjustment)")

    all_trades  = []
    weekly_rows = []
    bankroll    = config.STARTING_BANKROLL

    # Build expiry price lookup: (Symbol, ExpirationDate) -> price
    ep_lookup = (
        expiry_prices
        .set_index(["Symbol", "ExpirationDate"])["ExpiryPrice"]
        .to_dict()
    )

    for entry_date in tqdm(entry_dates, desc="Weeks"):

        # ── 1. Get this week's options data ───────────────────────────────
        week_df = df[df["DataDate"] == entry_date]
        if week_df.empty:
            continue

        # ── 2. Build candidates ───────────────────────────────────────────
        candidates = spreads.build_candidates(week_df)
        if candidates.empty:
            continue

        # ── 3. Score with GROUND ──────────────────────────────────────────
        scored = ground.score_candidates(candidates, history, lookback_days)

        # ── 4. Select best trade per ticker ──────────────────────────────
        selected = ground.select_trades(scored, top_n=top_n)
        if selected.empty:
            continue

        trades_this_week = selected[selected["decision"] != "PASS"]
        passes_this_week = selected[selected["decision"] == "PASS"]

        if trades_this_week.empty:
            weekly_rows.append(_weekly_summary(entry_date, bankroll, 0, 0, 0, 0, 0))
            continue

        # ── 5. Attach expiry prices ───────────────────────────────────────
        trades_this_week = trades_this_week.copy()
        trades_this_week["expiry_price"] = trades_this_week.apply(
            lambda r: ep_lookup.get(
                (r["ticker"], pd.Timestamp(r["expiry_date"])), np.nan
            ),
            axis=1,
        )

        # Drop trades where we don't have expiry price
        trades_this_week = trades_this_week.dropna(subset=["expiry_price"])
        if trades_this_week.empty:
            weekly_rows.append(_weekly_summary(entry_date, bankroll, 0, 0, 0, 0, 0))
            continue

        # ── 6. Calculate outcomes and P&L ────────────────────────────────
        trades_this_week["outcome"] = trades_this_week.apply(
            lambda r: spreads.calc_outcome(
                r["expiry_price"], r["short_strike"],
                r["long_strike"], r["decision"]
            ),
            axis=1,
        )

        trades_this_week["pnl_per_contract"] = trades_this_week.apply(
            lambda r: spreads.calc_pnl(
                r["expiry_price"], r["short_strike"], r["long_strike"],
                r["net_credit"], r["max_loss"], r["decision"]
            ),
            axis=1,
        )

        trades_this_week["result"] = trades_this_week["outcome"].apply(
            lambda x: "WIN" if x == 1.0 else ("LOSS" if x == -1.0 else "PARTIAL")
        )

        # ── 7. Position sizing ───────────────────────────────────────────
        # Each contract = 100 shares. Dollar P&L per contract = pnl_per_contract * 100.
        # Dollar at risk per contract = (max_loss + slippage_haircut) * 100,
        # since slippage is a real cost on entry that adds to total possible loss.
        n = len(trades_this_week)
        slip_per_share = 2.0 * float(spreads.SLIPPAGE_CENTS or 0.0)
        true_max_loss  = trades_this_week["max_loss"] + slip_per_share

        # Try integer flat-contract sizing first (e.g. '1', '2', '5')
        try:
            flat_n = int(sizing)
            if flat_n < 1:
                raise ValueError(f"flat contract count must be >= 1, got {flat_n}")
            trades_this_week["contracts"]      = flat_n
            trades_this_week["bankroll_alloc"] = flat_n * true_max_loss * 100

        except (ValueError, TypeError):
            # Not an integer — must be 'one', 'dyn10k', or a Kelly variant
            if sizing == "one":
                trades_this_week["contracts"]      = 1
                trades_this_week["bankroll_alloc"] = true_max_loss * 100

            elif sizing == "dyn10k":
                # Step-function sizing: 1 contract per $10k of bankroll.
                # 10k–20k → qty 1, 20k–30k → qty 2, etc. Floor at 1 so the
                # strategy keeps trading even after a drawdown that pushes
                # bankroll below the starting $10k.
                qty = max(1, int(bankroll // 10000))
                trades_this_week["contracts"]      = qty
                trades_this_week["bankroll_alloc"] = qty * true_max_loss * 100

            else:
                # Fractional-Kelly EQUAL-DOLLAR sizing against fixed reference bankroll.
                # All selected trades get equal dollar exposure that week.
                kelly_divisors = {"kelly": 1, "1kelly": 1, "2kelly": 2, "4kelly": 4}
                k = kelly_divisors.get(sizing)
                if k is None:
                    raise ValueError(
                        f"Unknown sizing '{sizing}'. "
                        f"Use 'one', 'kelly'/'1kelly', '2kelly', '4kelly', "
                        f"or a positive integer like '1', '2', '5'."
                    )

                ref_bankroll = config.STARTING_BANKROLL
                w_avg = trades_this_week["w_star"].mean()
                if w_avg <= 0 or n == 0:
                    trades_this_week["contracts"]      = 1
                    trades_this_week["bankroll_alloc"] = true_max_loss * 100
                else:
                    deploy = (1.0 / k) * w_avg * ref_bankroll
                    per_trade = deploy / n

                    contracts = (per_trade / (true_max_loss * 100)).round().astype(int)
                    contracts = contracts.clip(lower=1)

                    max_c = getattr(config, "MAX_CONTRACTS", 50)
                    contracts = contracts.clip(upper=max_c)

                    trades_this_week["contracts"]      = contracts
                    trades_this_week["bankroll_alloc"] = contracts * true_max_loss * 100

        # Dollar P&L per trade = contracts * pnl_per_contract * 100
        # Slippage is applied as a flat per-leg, per-contract execution
        # cost — it never affected selection, only realized P&L. 2 legs
        # per spread, $100 multiplier per contract.
        trades_this_week["dollar_pnl"] = (
            trades_this_week["contracts"]
            * trades_this_week["pnl_per_contract"]
            * 100
        )
        slip_per_spread = 2.0 * float(spreads.SLIPPAGE_CENTS or 0.0)
        if slip_per_spread > 0:
            trades_this_week["dollar_pnl"] -= (
                trades_this_week["contracts"] * slip_per_spread * 100
            )

        # ── 8. Update bankroll ────────────────────────────────────────────
        week_pnl    = trades_this_week["dollar_pnl"].sum()
        bankroll   += week_pnl
        bankroll    = max(bankroll, 0.01)   # can't go below zero

        # ── 9. Record ─────────────────────────────────────────────────────
        trades_this_week["entry_date"]  = entry_date
        trades_this_week["bankroll_eow"] = bankroll
        all_trades.append(trades_this_week)

        wins    = (trades_this_week["result"] == "WIN").sum()
        losses  = (trades_this_week["result"] == "LOSS").sum()
        partial = (trades_this_week["result"] == "PARTIAL").sum()

        weekly_rows.append(_weekly_summary(
            entry_date, bankroll, week_pnl, wins, losses, partial, n
        ))

    trades_df = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    weekly_df = pd.DataFrame(weekly_rows)

    return trades_df, weekly_df


def _weekly_summary(entry_date, bankroll, week_pnl,
                    wins, losses, partial, n_trades) -> dict:
    return {
        "entry_date":   entry_date,
        "n_trades":     n_trades,
        "wins":         wins,
        "losses":       losses,
        "partials":     partial,
        "win_rate":     wins / n_trades if n_trades > 0 else None,
        "week_pnl":     round(week_pnl, 2),
        "bankroll_eow": round(bankroll, 2),
    }
