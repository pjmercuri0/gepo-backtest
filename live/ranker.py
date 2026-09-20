"""Apply canonical filters + GROUND scoring to the latest live snapshot.

Reads the most recent snapshot from live/snapshots/, calls the *same*
spreads.build_candidates() and ground.compute_ground() the backtest uses,
applies the regime gate, then writes a ranked top-N to
live/ranked/latest.json (atomic) and an archival copy to
live/ranked/<timestamp>.json.

Also enforces the MIN_OPEN_INTEREST gate, which the backtest CSVs did not
need (preprocessed historical data was already liquidity-filtered upstream).

CLI:
  python -m live.ranker                   # rank latest snapshot
  python -m live.ranker --snapshot path/to/HHMM.parquet  # rank specific file
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config
import spreads
import ground
from live import live_config
from live.regime import current_regime
from live.preflight import PreflightError, run as run_preflight
from live.provenance import config_hash, git_sha, ranked_provenance


# ── D_ent canon (2026-09-13) ─────────────────────────────────────────────────
# Scoring no longer uses the spread_triple / empirical_runner windows. The belief is
# P_real (the name's realized DTE-matched moves vs the exact strikes, from daily closes),
# the credit is the smile-fit model credit, and the risk term is D_ent = ln3 - H(Q_bs),
# the paper's eq. 19. See SESSION_HANDOFF.md §0.33 and ent_canon.py.
import ent_canon as entc   # not "ec": the earnings block below binds a local `ec`
from live.closes import load_closes, closes_status

# ── Snapshot discovery ──────────────────────────────────────────────────────

def _latest_snapshot() -> Path | None:
    base = Path(live_config.SNAPSHOTS_DIR)
    if not base.exists():
        return None
    # Only date-named directories (YYYY-MM-DD). 2026-09-14: an "adhoc" directory left by a
    # manual Sunday run sorted lexically after every date and hijacked the 09:00 and 09:30
    # scans -- the ranker ranked the stale file and the live page showed Sunday's numbers.
    days = sorted([d for d in base.iterdir() if d.is_dir() and d.name[:1] == "2"], reverse=True)
    for d in days:
        files = sorted(d.glob("*.parquet"), reverse=True)
        if files:
            return files[0]
    return None


# ── Ranking pipeline ────────────────────────────────────────────────────────


# Own-gap drift coverage for the scan in progress, published on the payload so the
# live tab can warn that a 09:30 board was scored WITHOUT drift (the gap feed runs at
# 09:36). Reset per rank_snapshot call.
_GAP_STATE: dict = {"names": 0, "total": 0}


def _reprice_on_combos(candidates: pd.DataFrame) -> pd.DataFrame:
    """Replace leg-mid net_credit with IBKR's combo quote, then gate.

    Rows IBKR returns no market for keep their leg-mid credit and are marked
    combo_priced=False, so a thin complex-order book degrades to today's
    behaviour instead of silently emptying the scan.
    """
    from live import combo_quotes

    before = len(candidates)
    candidates = combo_quotes.attach_combo_quotes(candidates)

    # Credit priority (user 2026-09-15, replacing the 09-13 last-first order):
    #   combo MID   — midpoint of the combo book, when the book is narrower than
    #                 LIVE_COMBO_MAX_WIDTH x spread width. The current market; cannot be stale.
    #   combo LAST  — a real print, but on a 1-3 DTE spread it can be an hour old on a moved
    #                 underlying. Used only when there is no tight mid, and only if it sits
    #                 inside the current combo bid/ask (or there is no book to check against).
    #   leg mids    — mid(short) - mid(long) from the single-leg quotes, the fallback.
    max_w = float(getattr(live_config, "LIVE_COMBO_MAX_WIDTH", float("inf")))
    bid_c, ask_c = candidates["combo_bid"], candidates["combo_ask"]
    book_w = (ask_c - bid_c).abs()
    mid_c = candidates["combo_credit_mid"]
    last_c = candidates["combo_credit_last"]
    too_wide = mid_c.notna() & (book_w > max_w * candidates["spread_width"])
    mid_ok = mid_c.notna() & ~too_wide & (mid_c > 0)
    has_book = bid_c.notna() & ask_c.notna() & (ask_c > 0)
    lo_c = pd.concat([bid_c, ask_c], axis=1).min(axis=1)
    hi_c = pd.concat([bid_c, ask_c], axis=1).max(axis=1)
    last_fresh = last_c.notna() & (last_c > 0) & (~has_book | ((last_c >= lo_c) & (last_c <= hi_c)))
    last_used = last_fresh & ~mid_ok
    candidates["combo_too_wide"] = too_wide
    candidates["leg_mid_credit"] = candidates["net_credit"]
    candidates["credit_source"] = "leg_mid"
    candidates.loc[last_used, "credit_source"] = "combo_last"
    candidates.loc[mid_ok, "credit_source"] = "combo_mid"
    candidates["combo_priced"] = mid_ok | last_used
    candidates.loc[last_used, "net_credit"] = last_c[last_used]
    candidates.loc[mid_ok, "net_credit"] = mid_c[mid_ok]
    n_stale = int((last_c.notna() & (last_c > 0) & ~last_fresh & ~mid_ok).sum())
    if n_stale:
        print(f"  combo re-pricing: {n_stale} last print(s) outside the current book ignored as stale", flush=True)
    n_wide = int((too_wide & ~last_used).sum())
    if n_wide:
        print(f"  combo re-pricing: {n_wide} combo book(s) wider than {max_w:g}x spread width with no fresh last "
              f"→ leg mids used (order: combo mid > combo last > leg mid)", flush=True)
    basis = "mid>last>legs"
    candidates["max_loss"] = (candidates["spread_width"] - candidates["net_credit"]).round(4)

    # Same rejections build_candidates would have made, now on real prices.
    keep = (candidates["net_credit"].notna() & (candidates["net_credit"] > 0)
            & candidates["max_loss"].notna() & (candidates["max_loss"] > 0))
    dropped_nocredit = int((~keep).sum())
    candidates = candidates[keep].copy()
    if candidates.empty:
        print(f"  combo re-pricing: all {before} candidates fail credit>0", flush=True)
        return candidates

    candidates["credit_ratio"] = candidates["net_credit"] / candidates["max_loss"]
    gate = ((candidates["credit_ratio"] >= backtest_config.MIN_CREDIT_RATIO) &
            (candidates["credit_ratio"] <= getattr(backtest_config, "MAX_CREDIT_RATIO", float("inf"))) &
            (candidates["max_loss"] <= getattr(backtest_config, "MAX_MAX_LOSS", float("inf"))))
    dropped_gate = int((~gate).sum())
    candidates = candidates[gate].copy()

    shift = (candidates["net_credit"] - candidates["leg_mid_credit"])
    src = candidates["credit_source"].value_counts().to_dict()
    print(f"  combo re-pricing ({basis}): {src}", flush=True)
    print(f"  combo re-pricing: dropped {dropped_nocredit} (no credit) + "
          f"{dropped_gate} (ratio/max-loss) → {len(candidates)}/{before} survive", flush=True)
    if not shift.empty:
        print(f"  combo re-pricing: credit shift median {shift.median():+.3f}, "
              f"min {shift.min():+.3f}, max {shift.max():+.3f}", flush=True)
    return candidates


def _score_growth_negative(scored, *, k: float, credit_col: str):
    """Give growth-negative candidates a real (negative) GROUND instead of NaN.

    ent_canon.kelly() returns NaN when the optimal stake w* falls outside
    (0, 1). For these rows w* is NEGATIVE -- Kelly's answer is "take the other
    side" -- so log-growth is strictly decreasing in w and the best FEASIBLE
    stake is ent_canon's own floor, w = 0.01. Evaluating the same growth formula
    there gives the honest negative number the table should show, and keeps the
    definition intact: GROUND is growth at the growth-optimal feasible stake.

    Display-side only, and deliberately NOT in ent_canon: that module is shared
    with the backtest, where these rows are dropped by design. `qualified` is
    untouched -- a negative GROUND can never clear the threshold. Rows with no
    P_real keep NaN; there is nothing to evaluate.
    """
    import numpy as _np

    # Create the flag for EVERY row first. Left absent it comes back as NaN for
    # untouched rows, and bool(NaN) is True, which mislabels every ordinary
    # candidate as growth-negative.
    scored["growth_negative"] = False
    need = scored["GROUND"].isna() & scored["p"].notna()
    if not need.any():
        return scored
    p = scored.loc[need, "p"].astype(float).to_numpy()
    q = scored.loc[need, "q"].astype(float).to_numpy()
    ro = scored.loc[need, "ro"].astype(float).to_numpy()
    cr = scored.loc[need, credit_col].astype(float).to_numpy()
    wd = scored.loc[need, "width"].astype(float).to_numpy()
    ml = wd - cr
    with _np.errstate(invalid="ignore", divide="ignore"):
        b = _np.where(ml > 0, cr / ml, _np.nan)
        a = _np.where(b >= 1.0, 0.0, (b - 1.0) / (2.0 * _np.where(b == 0, 1, b)))
        w = 0.01  # ent_canon.kelly()'s lower clip; the best feasible stake here
        ell = (p * _np.log(_np.clip(1 + w * b, 1e-10, None))
               + ro * _np.log(_np.clip(1 + w * a * b, 1e-10, None))
               + q * _np.log(_np.clip(1 - w, 1e-10, None)))
    ev = _np.exp(ell) - 1.0
    dent = scored.loc[need, "D_ent"].astype(float).to_numpy()
    scored.loc[need, "w_star"] = w
    scored.loc[need, "G"] = ell
    scored.loc[need, "EV"] = ev
    scored.loc[need, "GROUND"] = ev * _np.exp(-k * dent)
    scored.loc[need, "growth_negative"] = True
    # ent_canon.kelly() clips w* into [0.01, 0.99]. A row whose optimum was
    # BELOW the floor comes back with w_star == 0.01 and negative growth -- the
    # same "Kelly says do not bet" verdict as the NaN rows above, reached by
    # clipping instead. Flag those too, or the table marks TSLA/CL as ordinary
    # candidates when Kelly wanted less than the minimum stake.
    clipped = (scored["w_star"].astype(float) <= 0.010000001) & (scored["G"].astype(float) < 0)
    scored.loc[clipped.fillna(False), "growth_negative"] = True
    return scored


def rank_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full backtest-canonical ranking pipeline on a live snapshot."""
    if df.empty:
        return pd.DataFrame()
    df_full = df.copy()   # parity/smile inputs are retained before candidate construction

    # Configure the live liquidity policy before any candidate construction.
    # This also covers snapshots that omit the OI column entirely.
    backtest_config.USE_OPEN_INTEREST_LIQUIDITY = getattr(
        live_config, "LIVE_USE_OPEN_INTEREST", True
    )
    backtest_config.MIN_OPEN_INTEREST = live_config.LIVE_MIN_OPEN_INTEREST
    backtest_config.ALLOW_VOLUME_LIQUIDITY_FALLBACK = getattr(
        live_config, "LIVE_ALLOW_VOLUME_FALLBACK", False
    )
    backtest_config.MIN_LIQUIDITY_VOLUME = getattr(live_config, "LIVE_MIN_VOLUME", 0)
    backtest_config.MIN_LIQUIDITY_BBO_SIZE = getattr(live_config, "LIVE_MIN_BBO_SIZE", 1)

    # Liquidity gate. OI is preferred; live mode may use the explicit
    # volume/BBO fallback when intraday OI is unavailable. Apply the same
    # predicate here and again during spread construction so neither stage can
    # accidentally bypass the policy.
    if "OpenInterest" in df.columns:
        before = len(df)
        df = df[df.apply(spreads.is_liquid_row, axis=1)]
        _oi_part = (f"OI>={live_config.LIVE_MIN_OPEN_INTEREST} or "
                    if backtest_config.USE_OPEN_INTEREST_LIQUIDITY else "")
        print(f"  liquidity gate ({_oi_part}"
              f"volume>={getattr(live_config, 'LIVE_MIN_VOLUME', 0)} + "
              f"BBO>={getattr(live_config, 'LIVE_MIN_BBO_SIZE', 1)}): "
              f"kept {len(df)}/{before} rows",
              flush=True)
    if df.empty:
        return pd.DataFrame()

    # Regime lookup (SPY 100d SMA). Refresh from disk on every rank — fast.
    spy_csv = os.path.join(backtest_config.DATA_DIR, "spy_us_d.csv")
    spreads.REGIME_LOOKUP     = spreads.build_regime_lookup(
        spy_csv, sma_window=backtest_config.REGIME_WINDOW
    )
    spreads.REGIME_FILTER     = backtest_config.REGIME_FILTER
    spreads.REGIME_PER_TICKER = False
    spreads.REGIME_BULL_ONLY  = getattr(backtest_config, "REGIME_BULL_ONLY", False)
    spreads.REGIME_LAG_SESSIONS = getattr(backtest_config, "REGIME_LAG_SESSIONS", 0)
    spreads.REGIME_FAIL_CLOSED = (
        getattr(backtest_config, "REGIME_FAIL_CLOSED", False)
        or getattr(live_config, "LIVE_FAIL_CLOSED_ON_MISSING_FEATURES", False)
    )
    spreads.REGIME_MAX_STALE_CALENDAR_DAYS = getattr(
        backtest_config, "REGIME_MAX_STALE_CALENDAR_DAYS", None
    )
    spreads.GAP_FILTER        = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS    = 0.0

    # Override the liquidity gate inside spreads.build_candidates for live mode.
    backtest_config.USE_OPEN_INTEREST_LIQUIDITY = getattr(
        live_config, "LIVE_USE_OPEN_INTEREST", True
    )
    backtest_config.MIN_OPEN_INTEREST = live_config.LIVE_MIN_OPEN_INTEREST

    # Live scores on mid, not clamped LAST: intraday LAST prints are
    # asynchronous across legs and fabricate credits (see live_config).
    backtest_config.CREDIT_BASIS = getattr(live_config, "LIVE_CREDIT_BASIS", "mid")
    backtest_config.CREDIT_SCALE = getattr(live_config, "LIVE_CREDIT_SCALE", 0.80)

    # Attach IV-rank + RV lookups so spreads.build_candidates carries them
    # through to each candidate row → ground.score_candidates uses them for
    # IV-rank-stratified empirical lookup and rv_vs_iv DKL.
    try:
        iv_rank = pd.read_parquet('output/iv_rank.parquet')
        iv_rank['DataDate'] = pd.to_datetime(iv_rank['DataDate'])
        df = df.merge(iv_rank[['Symbol', 'DataDate', 'iv_rank_bucket']],
                      on=['Symbol', 'DataDate'], how='left')
    except FileNotFoundError:
        pass
    try:
        rv = pd.read_parquet('output/rv_table.parquet')
        rv['DataDate'] = pd.to_datetime(rv['DataDate'])
        # Live snapshots have dates beyond what rv_table covers (vendor history
        # ends ~5-7 days behind today). Forward-fill: use the MOST RECENT rv
        # value per Symbol so live picks get a defined rv_30d. RV is sticky
        # (10d rolling) so a 5-day-old value is fine for DKL purposes.
        latest_rv = (rv.sort_values(['Symbol', 'DataDate'])
                       .groupby('Symbol').tail(1)
                       [['Symbol', 'rv_30d']])
        df = df.merge(latest_rv, on=['Symbol'], how='left')
    except FileNotFoundError:
        pass

    # Build candidates (one per ticker × direction × expiry).
    #
    # Strike PAIRING is price-independent (short leg = closest |delta| to
    # DELTA_TARGET, long leg = adjacent strike), but build_candidates also
    # applies the credit gates using leg-mid pricing. When combo pricing is on
    # we want those gates evaluated against the REAL spread quote, so neutralise
    # them here and re-apply after the combo pass. Otherwise a pair the leg mids
    # misprice is discarded before IBKR ever gets asked about it.
    # Selection basis for the BUILD step is mid (2026-09-16). build_candidates prices
    # with config.CREDIT_BASIS, which is "last_clamped" for the backtest; live that
    # both mispriced pairs off stale prints and dropped any pair whose legs had not
    # traded yet. The live credit is set properly downstream by _reprice_on_combos
    # (combo mid > fresh combo last > leg mids) and selection scores on model credit.
    _saved_basis = getattr(backtest_config, "CREDIT_BASIS", "last_clamped")
    backtest_config.CREDIT_BASIS = "mid"
    if live_config.LIVE_COMBO_ENABLED:
        _saved_gates = (backtest_config.MIN_CREDIT_RATIO,
                        getattr(backtest_config, "MAX_CREDIT_RATIO", float("inf")),
                        getattr(backtest_config, "MAX_MAX_LOSS", float("inf")))
        backtest_config.MIN_CREDIT_RATIO = float("-inf")
        backtest_config.MAX_CREDIT_RATIO = float("inf")
        backtest_config.MAX_MAX_LOSS = float("inf")
    candidates = spreads.build_candidates(df)
    backtest_config.CREDIT_BASIS = _saved_basis
    if live_config.LIVE_COMBO_ENABLED:
        (backtest_config.MIN_CREDIT_RATIO,
         backtest_config.MAX_CREDIT_RATIO,
         backtest_config.MAX_MAX_LOSS) = _saved_gates
    print(f"  built {len(candidates)} candidate spreads", flush=True)

    # Re-price on IBKR's complex-order book, then re-apply the credit gates.
    if live_config.LIVE_COMBO_ENABLED and not candidates.empty:
        candidates = _reprice_on_combos(candidates)
        if candidates.empty:
            return pd.DataFrame()

    # Earnings gate (canonical 2026-06-08): drop any candidate whose underlying
    # has earnings between entry_date and expiry_date (overnight gap risk).
    # Calendar source: data/earnings_calendar.csv via fetch_earnings.py.
    try:
        from pathlib import Path
        ec_path = Path(backtest_config.DATA_DIR) / "earnings_calendar.csv"
        if ec_path.exists() and not candidates.empty:
            ec = pd.read_csv(ec_path)
            ec['EarningsDate'] = pd.to_datetime(ec['EarningsDate']).dt.date
            ec_by_sym = ec.groupby('Symbol')['EarningsDate'].apply(set).to_dict()
            def has_earnings_in_window(row):
                tk = row['ticker']
                dates = ec_by_sym.get(tk, set())
                if not dates:
                    return False
                start = pd.Timestamp(row['entry_date']).date()
                end   = pd.Timestamp(row['expiry_date']).date()
                return any(start <= d <= end for d in dates)
            before = len(candidates)
            mask_earnings = candidates.apply(has_earnings_in_window, axis=1)
            candidates = candidates[~mask_earnings].copy()
            dropped = before - len(candidates)
            if dropped > 0:
                print(f"  earnings gate: dropped {dropped} candidate(s)", flush=True)
    except Exception as e:
        print(f"  earnings gate: ERR {type(e).__name__}: {e}", flush=True)

    # Ex-dividend gate (canonical 2026-06-09): drop BEAR-CALLS only where the
    # underlying has an ex-dividend date within the entry-to-expiry window.
    # Short calls can be early-assigned the day before ex-div for the dividend,
    # so we use a +1 day buffer past expiry. Bull-puts are unaffected (no
    # dividend-driven early-exercise incentive for puts).
    # Calendar source: data/dividend_calendar.csv via fetch_dividends.py.
    try:
        from pathlib import Path
        div_path = Path(backtest_config.DATA_DIR) / "dividend_calendar.csv"
        if div_path.exists() and not candidates.empty:
            dc = pd.read_csv(div_path)
            dc['ExDividendDate'] = pd.to_datetime(dc['ExDividendDate']).dt.date
            dc_by_sym = dc.groupby('Symbol')['ExDividendDate'].apply(set).to_dict()
            def exdiv_hits(row):
                """True if an ex-div date falls in this row's exposure window.

                Bear call: entry .. expiry+1. The +1 covers early assignment the
                day before ex-div, which is the call-specific risk.
                Bull put:  entry .. expiry. No assignment incentive, so no
                buffer — the exposure is the ex-div price drop itself, which
                only matters on or before expiry.
                """
                dates = dc_by_sym.get(row['ticker'], set())
                if not dates:
                    return False
                start = pd.Timestamp(row['entry_date']).date()
                end_ts = pd.Timestamp(row['expiry_date'])
                if row['spread_type'] == 'bear_call':
                    end_ts = end_ts + pd.Timedelta(days=1)
                return any(start <= d <= end_ts.date() for d in dates)

            is_call = candidates['spread_type'] == 'bear_call'
            hits = candidates.apply(exdiv_hits, axis=1)
            call_hits = int((hits & is_call).sum())
            put_hits  = int((hits & ~is_call).sum())

            # Calls always gated; puts only when LIVE_EXDIV_GATE_PUTS is on.
            mask_exdiv = hits & (is_call | live_config.LIVE_EXDIV_GATE_PUTS)
            candidates = candidates[~mask_exdiv].copy()

            if call_hits:
                print(f"  ex-div gate: dropped {call_hits} bear-call(s)", flush=True)
            if put_hits:
                if live_config.LIVE_EXDIV_GATE_PUTS:
                    print(f"  ex-div gate: dropped {put_hits} bull-put(s)", flush=True)
                else:
                    print(f"  ex-div gate: {put_hits} bull-put(s) would be dropped "
                          f"(LIVE_EXDIV_GATE_PUTS off — kept)", flush=True)
    except Exception as e:
        print(f"  ex-div gate: ERR {type(e).__name__}: {e}", flush=True)
    if candidates.empty:
        return pd.DataFrame()

    # ── D_ent canon scoring ───────────────────────────────────────────────────
    # 1) fit one IV smile per (Symbol, DataDate, ExpirationDate) on the FULL snapshot chain
    #    (liquid strikes only, robust), 2) price both legs off the fit -> model_credit, the
    #    market triple Q_bs and D_ent, 3) belief P_real from daily closes, 4) Kelly growth on
    #    the model credit, GROUND = (e^G - 1) * exp(-k * D_ent).
    # The IBKR quoted credit (net_credit, combo-priced when available) is KEPT for display
    # and for the tracker; selection and the fill targets use the model credit.
    fits = entc.fit_smiles(df_full)
    print(f"  smile fit: {len(fits)} chains", flush=True)
    candidates = candidates.rename(columns={"spread_width": "width"}) if "width" not in candidates.columns else candidates
    priced = entc.price_spreads(candidates, fits)
    n_unpriced = int(priced["model_credit"].isna().sum())
    if n_unpriced:
        print(f"  {n_unpriced} candidate(s) without a smile fit dropped", flush=True)
    priced = priced[priced["model_credit"].notna() & (priced["model_credit"] > 0.01)].copy()
    if priced.empty:
        return pd.DataFrame()
    parity = entc.chain_parity_signal(df_full).rename(columns={
        "Symbol": "ticker", "DataDate": "entry_date", "ExpirationDate": "expiry_date",
    })
    for c in ("entry_date", "expiry_date"):
        priced[c] = pd.to_datetime(priced[c]).dt.normalize()
        if not parity.empty:
            parity[c] = pd.to_datetime(parity[c]).dt.normalize()
    if not parity.empty:
        priced = priced.drop(columns=['parity_bull_raw', 'parity_pairs'], errors='ignore')
        priced = priced.merge(
            parity[["ticker", "entry_date", "expiry_date", "parity_bull_raw", "parity_pairs"]],
            on=["ticker", "entry_date", "expiry_date"], how="left",
        )
    else:
        priced["parity_bull_raw"] = np.nan
        priced["parity_pairs"] = 0
    priced = entc.add_parity_percentile(priced)
    priced = entc.add_bear_parity_percentile(priced)   # mirrored percentile for bear calls (0.57)
    print(f"  parity: {priced['parity_bull_raw'].notna().sum()}/{len(priced)} candidates "
          f"have matched-strike IV pairs; veto <= {backtest_config.PARITY_MIN_PCT:.0%}", flush=True)
    closes = load_closes()
    cs = closes_status()
    if cs["rows"] == 0:
        print("  WARNING: output/ibkr_closes.parquet is EMPTY -- P_real is running on snapshot prints only. "
              "Seed it: python3 -m live.fetch_ibkr_closes --years 2", flush=True)
        if getattr(live_config, "LIVE_FAIL_CLOSED_ON_MISSING_FEATURES", False) and getattr(
            live_config, "LIVE_REQUIRE_IBKR_CLOSES", False
        ):
            print("  FAIL CLOSED: no IBKR close history; no live candidates will be qualified", flush=True)
            return pd.DataFrame()
    else:
        print(f"  closes: IBKR store {cs['sessions']} sessions, {cs['tickers']} tickers, {cs['first']} -> {cs['last']}", flush=True)
    sel_credit = "net_credit" if getattr(live_config, "LIVE_SELECTION_CREDIT", "quoted") == "quoted" else "model_credit"
    print(f"  selection credit: {'IBKR quoted mid (uncapped)' if sel_credit == 'net_credit' else 'smile-fit model'}", flush=True)
    # §0.45 own-gap drift: each stock's own opening gap today (live/fetch_name_gaps.py, 09:36) shifts its P_real.
    gap_mu = None
    _GAP_STATE["names"] = 0; _GAP_STATE["total"] = 0
    if entc.GAP_GAMMA:
        from live.fetch_name_gaps import load_store as _load_gaps
        _gs = _load_gaps(); _today = pd.to_datetime(priced["entry_date"]).dt.normalize().max()
        _gt = _gs[(_gs.date == _today) & _gs.gap.notna()]
        if _today.year not in entc.GAP_FIT:
            print(f"  WARNING: ent_canon.GAP_FIT has no {_today.year} fit -- using the latest earlier year. "
                  f"Refit: python3 fit_gap.py {_today.year}", flush=True)
        _have = priced["ticker"].isin(set(_gt.ticker))
        if _gt.empty:
            _GAP_STATE["names"] = 0
            _GAP_STATE["total"] = int(priced["ticker"].nunique())
            print(f"  WARNING: no stock gaps stored for {_today.date()} -- P_real drift is 0 today "
                  "(run: python3 -m live.fetch_name_gaps)", flush=True)
        else:
            gap_mu = entc.gap_drift(priced, closes, _gt)
            _names = priced.loc[_have, "ticker"].nunique(); _all = priced["ticker"].nunique()
            _GAP_STATE["names"] = int(_names); _GAP_STATE["total"] = int(_all)
            print(f"  own gaps {_today.date()}: {_names}/{_all} candidate names have today's gap"
                  f"{'' if _names == _all else ' (the rest score with zero drift)'}; beta {entc.gap_fit(_today.year)[2]:.4f}; "
                  f"drift range {np.min(gap_mu) * 100:+.3f}%..{np.max(gap_mu) * 100:+.3f}% of price", flush=True)
    # Preserve feature coverage so missing own-gap data cannot be converted to
    # a neutral zero drift and silently qualify a trade.
    if entc.GAP_GAMMA and getattr(live_config, "LIVE_REQUIRE_OWN_GAP", False):
        priced["own_gap_available"] = priced["ticker"].isin(set(_gt.ticker)) if _gt is not None and not _gt.empty else False
    else:
        priced["own_gap_available"] = True

    scored = entc.score(priced, closes, k=ground.DKL_K, thr=backtest_config.GROUND_THRESHOLD, credit_col=sel_credit, mu=gap_mu)
    scored["quoted_credit"] = scored["net_credit"]
    scored["spread_width"] = scored["width"]
    # market triple for display (p_hat / q_hat / ro_hat = WIN / LOSS / PARTIAL under Q_bs)
    scored["p_hat"], scored["q_hat"], scored["ro_hat"] = scored["q_win"], scored["q_loss"], scored["q_part"]
    n_nobelief = int(scored["p"].isna().sum())
    n_neg = int(scored["EV"].isna().sum()) - n_nobelief
    if n_nobelief:
        print(f"  {n_nobelief} candidate(s) have NO close history (no P_real) and are unscored", flush=True)
    if n_neg:
        print(f"  {n_neg} candidate(s) growth-negative at the selection credit "
              f"(Kelly w* < 0) — scored at the minimum stake, never qualified", flush=True)
    scored = _score_growth_negative(scored, k=ground.DKL_K, credit_col=sel_credit)
    for i, r in scored.iterrows():
        tg = entc.credit_targets(r["dfit_short"], r["DTE"], width=r["width"], model_credit=r["model_credit"])
        for k_, v_ in tg.items():
            scored.at[i, f"tgt_{k_}"] = v_
    # Keep growth-negative candidates (user, 2026-09-20: "dont kill kelly with
    # <= 0"). Those rows have GROUND = NaN because kelly() finds no interior
    # optimum, not because they are slightly negative; they serialize as null and
    # the page renders "—" with an empty bar. They can never qualify, since
    # `GROUND >= thr` is False for NaN. Rows with NO close history are still
    # dropped: without P_real there is nothing to show at all.
    ranked = scored[scored["p"].notna()].copy()

    # One direction per ticker (2026-06-10): bull_put and bear_call on the same
    # name are contradictory bets; keep only the better-GROUND one.
    before = len(ranked)
    ranked = (ranked.sort_values("GROUND", ascending=False)
                    .groupby("ticker", as_index=False).head(1))
    if len(ranked) < before:
        print(f"  per-ticker dedupe: {before} -> {len(ranked)} rows", flush=True)

    # 0.57 canon (2026-09-19): two-sided.  Each sleeve has its OWN threshold, parity rule
    # and daily cap, matching research/report_bear_regime.py exactly:
    #   bull puts : GROUND >= GROUND_THRESHOLD (0.005), parity_pct > PARITY_MIN_PCT (0.12), top TOP_N (10)
    #   bear calls: GROUND >= BEAR_GROUND_THRESHOLD (0.001), bear_parity_pct > BEAR_PARITY_MIN_PCT (0.25), top BEAR_TOP_N (5)
    # The regime gate in spreads.py means only one side is present on any day.
    thr = backtest_config.GROUND_THRESHOLD
    bear_thr = getattr(backtest_config, "BEAR_GROUND_THRESHOLD", thr)
    is_bear = ranked["spread_type"].eq("bear_call").to_numpy() if not ranked.empty else np.zeros(0, dtype=bool)
    thr_side = np.where(is_bear, bear_thr, thr)
    # Execution gate (user 2026-09-13, revised same day): the IBKR credit on the table (combo mid,
    # or combo last on a too-wide book, else leg mids) must sit at or above the WALK-AWAY line,
    # 1.00 x model (ent_canon.MULT_WALKAWAY) -- fair value. Below fair the spread is sold for less
    # than it is worth, whatever its GROUND. MIN is the same 1.00x; the 1.04-1.10x target is the ask. Gating at 1.04x on the May-Sep IBKR replay killed 80% of
    # model-ranked spreads (54 trades / $309); at 1.00x it is 118 trades / $950, best per-trade
    # and lowest drawdown of the four selection x gate combinations (§0.38).
    # Compare at the precision the page shows (2dp, half-up). 2026-09-14: INTC quoted
    # 0.275 (shown 0.28) against a walk-away of 0.28 (model 0.2805) read as a tie on
    # screen and failed underneath. A tie at 2dp qualifies.
    _r2 = lambda x: np.floor(x * 100 + 0.5) / 100
    ranked["above_min"] = _r2(ranked["net_credit"].astype(float)) >= ranked["tgt_walkaway_credit"].astype(float)
    bull_parity_ok = (ranked["parity_pct"] > getattr(backtest_config, "PARITY_MIN_PCT", 0.12))
    bear_parity_ok = (ranked["bear_parity_pct"] > getattr(backtest_config, "BEAR_PARITY_MIN_PCT", 0.25))
    parity_ok = pd.Series(np.where(is_bear, bear_parity_ok, bull_parity_ok), index=ranked.index, dtype=bool)
    if getattr(live_config, "LIVE_REQUIRE_PARITY", False):
        parity_ok &= ranked["parity_bull_raw"].notna()
        parity_ok &= ranked["parity_pairs"].fillna(0) >= getattr(live_config, "LIVE_MIN_PARITY_PAIRS", 1)
    feature_ok = ranked["own_gap_available"].astype(bool)
    above_thr = ranked["GROUND"] >= thr_side
    ranked["qualified"] = above_thr & parity_ok & ranked["above_min"] & feature_ok
    # Per-side daily cap, applied to the QUALIFIED set in GROUND order so every downstream
    # consumer (latest.json, freeze_snapshot top-up, snapshot_picks) inherits it.
    if not ranked.empty:
        cap_side = np.where(is_bear, getattr(backtest_config, "BEAR_TOP_N", 5), backtest_config.TOP_N)
        order = ranked.sort_values("GROUND", ascending=False)
        pos = order[order["qualified"]].groupby("spread_type").cumcount()
        over = pos.index[pos.values >= cap_side[pos.index]]
        ranked.loc[over, "qualified"] = False
    n_below = int((above_thr & ~ranked["above_min"]).sum())
    if n_below:
        print(f"  execution gate: {n_below} candidate(s) above GROUND {thr} but quoted BELOW fair value (1.00x model) — not qualified", flush=True)
    n_parity = int((above_thr & ~parity_ok).sum())
    if n_parity:
        print(f"  parity veto: dropped {n_parity} candidate(s) (bull <= {backtest_config.PARITY_MIN_PCT:.0%}, "
              f"bear <= {getattr(backtest_config, 'BEAR_PARITY_MIN_PCT', 0.25):.0%} daily percentile)", flush=True)

    # Sort by GROUND descending (qualified first, then below-threshold).
    ranked = ranked.sort_values("GROUND", ascending=False).reset_index(drop=True)
    n_bull_q = int((ranked["qualified"] & ranked["spread_type"].eq("bull_put")).sum()) if not ranked.empty else 0
    n_bear_q = int((ranked["qualified"] & ranked["spread_type"].eq("bear_call")).sum()) if not ranked.empty else 0
    print(f"  qualified: {n_bull_q} bull puts (GROUND >= {thr}, parity > {backtest_config.PARITY_MIN_PCT:.0%}, cap {backtest_config.TOP_N}) + "
          f"{n_bear_q} bear calls (GROUND >= {bear_thr}, bear parity > {getattr(backtest_config, 'BEAR_PARITY_MIN_PCT', 0.25):.0%}, "
          f"cap {getattr(backtest_config, 'BEAR_TOP_N', 5)}) of {len(ranked)}; quote >= fair", flush=True)
    return ranked


# ── Output serialization ────────────────────────────────────────────────────

def _vol_gate_status(snap_time: datetime) -> dict:
    """Decide whether to suppress picks based on SPY 20d realized vol.

    Backtest 2022-2026 showed non-Monday DTE 1-3 picks have negative edge
    in high-vol regimes (Sept-Oct 2022 lost $9.6k in 2 months). Gate rule:
        - Monday: always trade
        - Tue-Thu: only if SPY rv_20 < live_config.RV_GATE_THRESHOLD
        - Threshold = None → gate disabled, always trade
    Returns dict with 'gated' bool + diagnostic fields (rv_20, threshold,
    dow, reason) for the webapp to render.
    """
    threshold = getattr(live_config, "RV_GATE_THRESHOLD", None)
    is_monday = snap_time.weekday() == 0
    out = {
        "gated":       False,
        "rv_20":       None,
        "threshold":   threshold,
        "dow":         snap_time.strftime("%A"),
        "is_monday":   is_monday,
        "reason":      None,
    }
    if threshold is None or is_monday:
        out["reason"] = "monday-always" if is_monday else "gate-disabled"
        return out
    spy_path = Path(live_config.RANKED_DIR) / "spy_intraday.json"
    if not spy_path.exists():
        out["reason"] = "no-spy-tick"
        return out
    try:
        with open(spy_path) as f:
            tick = json.load(f)
    except (OSError, json.JSONDecodeError):
        out["reason"] = "spy-read-error"
        return out
    rv = tick.get("rv_20")
    if rv is None:
        out["reason"] = "no-rv-in-tick"
        return out
    out["rv_20"] = float(rv)
    if rv >= threshold:
        out["gated"] = True
        out["reason"] = f"rv_20 {rv:.1f} ≥ {threshold:.0f} (high-vol regime, Tue-Thu suppressed)"
    else:
        out["reason"] = f"rv_20 {rv:.1f} < {threshold:.0f} (normal regime)"
    return out


def _serialize(ranked: pd.DataFrame, snapshot_path: Path, provenance: dict | None = None) -> dict:
    """Build the JSON payload consumed by the webapp."""
    snap_time = datetime.now()
    dte_min, dte_max = live_config.live_dte_window(snap_time.date())

    # Ensure qualified column exists (fallback if missing from ranker).
    # Skip on empty ranked: no GROUND column exists either, so the
    # comparison would KeyError. Top-N / ticker slices below are still
    # safe on an empty frame.
    if not ranked.empty and "qualified" not in ranked.columns:
        ranked["qualified"] = ranked["GROUND"] >= backtest_config.GROUND_THRESHOLD

    # Vol gate: high-RV non-Monday → suppress picks entirely.
    gate = _vol_gate_status(snap_time)
    if gate["gated"]:
        ranked = ranked.iloc[0:0]  # empty, preserve columns
        print(f"  [vol-gate] {gate['reason']} — picks suppressed", flush=True)

    # Top-N picks (canonical): top-N from QUALIFIED only (GROUND ≥ per-DOW
    # threshold). Cards rendered are exactly these — no dimmed extras. May
    # yield <N picks on low-edge days.
    # ticker_rows contains every positive-GROUND candidate for the table below;
    # the table still dims rows that are either rank>5 or below threshold.
    # Canonical: top cards = top-N QUALIFIED picks only.
    # On low-edge days you may see fewer than N cards — that's intentional; only show
    # picks that actually meet the GROUND threshold. Full ranked list (including
    # below-threshold) lives in `ticker_rows` for the table below.
    qualified_only = ranked[ranked.get("qualified", False) == True] if not ranked.empty else ranked
    top = qualified_only.head(live_config.TOP_N_DISPLAY)
    # Every ranked candidate goes in the table, negative GROUND included (user,
    # 2026-09-20). This used to filter to GROUND > 0, which hid negative-edge
    # rows and made the payload's n_candidates disagree with the rendered row
    # count. The table still dims anything below threshold or outside the top N.
    # No GROUND lookup here any more, which also retires the empty-frame
    # KeyError: an empty `ranked` has NO columns, so ranked["GROUND"] raised and
    # crashed _serialize on exactly the "nothing ranked" path, leaving
    # latest.json unwritten and the live page frozen on the last good scan.
    ticker_rows = ranked

    def row_to_dict(r):
        # JSON-safe rendering of one ranked spread.
        return {
            "ticker":           r.get("ticker"),
            "spread_type":      r.get("spread_type"),
            "entry_date":       _isodate(r.get("entry_date")),
            "expiry_date":      _isodate(r.get("expiry_date")),
            "entry_price":      _num(r.get("entry_price")),
            "short_strike":     _num(r.get("short_strike")),
            "long_strike":      _num(r.get("long_strike")),
            "short_delta":      _num(r.get("short_delta")),
            "long_delta":       _num(r.get("long_delta")),
            "net_credit":       _num(r.get("net_credit")),
            # Combo pricing provenance. net_credit above is the IBKR combo
            # book's mid when combo_priced is true; leg_mid_credit is what the
            # old mid(short)-mid(long) arithmetic would have said. MO Sep04
            # 70/69 on 2026-09-01 12:31: leg mids 0.620, combo book 0.485.
            "leg_mid_credit":   _num(r.get("leg_mid_credit")),
            "combo_priced":     bool(r.get("combo_priced")) if r.get("combo_priced") is not None else None,
            "credit_source":    r.get("credit_source"),
            "combo_too_wide":   bool(r.get("combo_too_wide")) if r.get("combo_too_wide") is not None else None,
            "combo_bid":        _num(r.get("combo_bid")),
            "combo_ask":        _num(r.get("combo_ask")),
            "combo_last":       _num(r.get("combo_last")),
            "combo_credit_mid":   _num(r.get("combo_credit_mid")),
            "combo_credit_touch": _num(r.get("combo_credit_touch")),
            "spread_width":     _num(r.get("spread_width")),
            "max_loss":         _num(r.get("max_loss")),
            "credit_ratio":     _num(_ratio(r.get("net_credit"), r.get("max_loss"))),
            "short_bid":        _num(r.get("short_bid")),
            "short_ask":        _num(r.get("short_ask")),
            "long_bid":         _num(r.get("long_bid")),
            "long_ask":         _num(r.get("long_ask")),
            "short_last":       _num(r.get("short_last")),
            "long_last":        _num(r.get("long_last")),
            "short_oi":         (None if r.get("short_oi") is None or (isinstance(r.get("short_oi"), float) and pd.isna(r.get("short_oi"))) else int(r["short_oi"])),
            "long_oi":          (None if r.get("long_oi")  is None or (isinstance(r.get("long_oi"),  float) and pd.isna(r.get("long_oi")))  else int(r["long_oi"])),
            "short_volume":     (None if r.get("short_volume") is None or (isinstance(r.get("short_volume"), float) and pd.isna(r.get("short_volume"))) else int(r["short_volume"])),
            "long_volume":      (None if r.get("long_volume")  is None or (isinstance(r.get("long_volume"),  float) and pd.isna(r.get("long_volume")))  else int(r["long_volume"])),
            "short_bid_size":   _int(r.get("short_bid_size")),
            "short_ask_size":   _int(r.get("short_ask_size")),
            "long_bid_size":    _int(r.get("long_bid_size")),
            "long_ask_size":    _int(r.get("long_ask_size")),
            "IV":               _num(r.get("IV")),
            # D_ent canon fields (2026-09-13)
            "model_credit":     _num(r.get("model_credit")),
            "quoted_credit":    _num(r.get("quoted_credit")),
            "iv_fit_short":     _num(r.get("iv_fit_short")),
            "iv_fit_long":      _num(r.get("iv_fit_long")),
            "dfit_short":       _num(r.get("dfit_short")),
            "dfit_long":        _num(r.get("dfit_long")),
            "D_ent":            _num(r.get("D_ent")),
            "credit_targets":   {k_[4:]: (_num(r.get(k_)) if not isinstance(r.get(k_), str) else r.get(k_))
                                 for k_ in r.index if k_.startswith("tgt_")},
            "DTE":              _int(r.get("DTE")),
            "p":                _num(r.get("p")),
            "q":                _num(r.get("q")),
            "ro":               _num(r.get("ro")),
            "p_hat":            _num(r.get("p_hat")),
            "q_hat":            _num(r.get("q_hat")),
            "ro_hat":           _num(r.get("ro_hat")),
            "G":                _num(r.get("G")),
            "EV":               _num(r.get("EV")),
            "DKL":              _num(r.get("DKL")),
            "GROUND":           _num(r.get("GROUND")),
            # True when GROUND was evaluated at the minimum stake because
            # Kelly's optimum is negative — a real number, but a "do not
            # take this" number. Lets the table mark it apart from a
            # small positive edge.
            "growth_negative":  _flag(r.get("growth_negative")),
            "w_star":           _num(r.get("w_star")),
            "parity_bull_raw":  _num(r.get("parity_bull_raw")),
            "parity_bull_signed": _num(r.get("parity_bull_signed")),
            "parity_pct":       _num(r.get("parity_pct")),
            "parity_pairs":     _num(r.get("parity_pairs")),
            "qualified":        bool(r.get("qualified", True)),
            "above_min":        (None if r.get("above_min") is None else bool(r.get("above_min"))),
        }

    return {
        "snapshot_ts":   snap_time.isoformat(timespec="seconds"),
        "snapshot_file": str(snapshot_path.relative_to(ROOT)),
        "data_date":     snap_time.date().isoformat(),
        "git_sha":       git_sha(),
        "config_hash":   config_hash(),
        "provenance":    provenance or ranked_provenance(snapshot_path, len(ranked), int(ranked.get("qualified", pd.Series(dtype=bool)).sum()) if not ranked.empty else 0),
        "n_candidates":  int(len(ranked)),
        "config": {
            "DTE_MIN":          dte_min,
            "DTE_MAX":          dte_max,
            "DELTA_TARGET":     backtest_config.DELTA_TARGET,
            "DELTA_MIN":        backtest_config.DELTA_MIN,
            "DELTA_MAX":        backtest_config.DELTA_MAX,
            "MIN_CREDIT_RATIO": backtest_config.MIN_CREDIT_RATIO,
            # inf is JSON-invalid (Python emits literal `Infinity`, strict
            # parsers reject) — serialize as null when no cap.
            "MAX_CREDIT_RATIO": (
                None if backtest_config.MAX_CREDIT_RATIO == float("inf")
                else backtest_config.MAX_CREDIT_RATIO
            ),
            "MIN_OPEN_INTEREST": backtest_config.MIN_OPEN_INTEREST,
            "LIQUIDITY_VOLUME_FALLBACK": getattr(
                live_config, "LIVE_ALLOW_VOLUME_FALLBACK", False
            ),
            "MIN_LIQUIDITY_VOLUME": getattr(live_config, "LIVE_MIN_VOLUME", 0),
            "MIN_LIQUIDITY_BBO_SIZE": getattr(live_config, "LIVE_MIN_BBO_SIZE", 1),
            "FAIL_CLOSED_ON_MISSING_FEATURES": getattr(
                live_config, "LIVE_FAIL_CLOSED_ON_MISSING_FEATURES", False
            ),
            "REQUIRE_PARITY": getattr(live_config, "LIVE_REQUIRE_PARITY", False),
            "REQUIRE_IBKR_CLOSES": getattr(live_config, "LIVE_REQUIRE_IBKR_CLOSES", False),
            "REQUIRE_OWN_GAP": getattr(live_config, "LIVE_REQUIRE_OWN_GAP", False),
            "CREDIT_BASIS":     getattr(backtest_config, "CREDIT_BASIS", "last_clamped"),
            "CREDIT_SCALE":     getattr(backtest_config, "CREDIT_SCALE", 1.0),
            "MAX_MAX_LOSS":     backtest_config.MAX_MAX_LOSS,
            # -inf canonically means rank-only; emit null so the JSON is
            # strict (Python's allow_nan=True emits literal Infinity which
            # the browser's JSON.parse rejects).
            "GROUND_THRESHOLD": (
                None if (backtest_config.GROUND_THRESHOLD is None or
                         backtest_config.GROUND_THRESHOLD == float("-inf"))
                else backtest_config.GROUND_THRESHOLD
            ),
            "TOP_N":            live_config.TOP_N_DISPLAY,
            "DKL_K":            getattr(ground, "DKL_K", 1.0),
            "ALPHA":            "(b-1)/(2b)",
            "DKL_REF":          "D_ent = ln3 − H(Q_bs) (paper eq. 19)",
            "BELIEF":           f"P_real: {entc.WINDOW} sessions of realized moves vs the strikes",
            "GAP_DRIFT":        entc.CANON_LABELS["gap"],
            "GAP_NAMES":        _GAP_STATE.get("names", 0),
            "GAP_TOTAL":        _GAP_STATE.get("total", 0),
            "GAP_APPLIED":      bool(entc.GAP_GAMMA) and _GAP_STATE.get("names", 0) > 0,
            "CREDIT_MODEL":     ("selection on IBKR credit: combo mid > fresh combo last > leg mids (uncapped); model credit for targets" if getattr(live_config, "LIVE_SELECTION_CREDIT", "quoted") == "quoted" else "smile-fit model credit (selection); IBKR quote shown"),
            "FILL_MULT":        entc.FILL_MULT,
            "COMMISSION":       entc.COMMISSION,
            "TARGETS":          entc.CANON_LABELS["targets"],
            "EXEC_GATE":        "qualified only if the IBKR credit ≥ fair value (1.00×model, the walk-away line)",
            "REGIME_GATE":      "bull puts only when prior-session SPY close > 100d SMA; cash otherwise",
            "PARITY_GATE":      f"same-strike call-IV minus put-IV daily percentile > {backtest_config.PARITY_MIN_PCT:.0%}",
        },
        "regime":    current_regime(),
        "vol_gate":  gate,
        "top_picks": [row_to_dict(r) for _, r in top.iterrows()],
        "ticker":    [row_to_dict(r) for _, r in ticker_rows.iterrows()],
    }


def _flag(v) -> bool:
    """NaN-safe truthiness. bool(float("nan")) is True, which silently turns a
    missing flag into a set one."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    return bool(v)


def _num(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return float(v)


def _int(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return int(v)


def _isodate(v):
    if v is None:
        return None
    if isinstance(v, str):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat() if hasattr(v, "year") else v.date().isoformat()
    return str(v)


def _ratio(num, denom):
    if num is None or denom is None or denom == 0:
        return None
    return num / denom


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: write to a temp file in the same dir, then rename.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", default=None, help="Specific snapshot parquet to rank")
    parser.add_argument("--allow-out-of-hours", action="store_true",
                        help="Explicitly bypass the production market-hours check")
    parser.add_argument("--allow-stale-snapshot", action="store_true",
                        help="Explicitly allow a snapshot older than 30 minutes")
    args = parser.parse_args()

    if args.snapshot:
        snap_path = Path(args.snapshot).resolve()
    else:
        latest = _latest_snapshot()
        if latest is None:
            print("no snapshots found in live/snapshots/", flush=True)
            return 1
        snap_path = latest

    try:
        run_preflight(
            "rank", snap_path,
            allow_out_of_hours=args.allow_out_of_hours,
            allow_stale_snapshot=args.allow_stale_snapshot,
        )
    except PreflightError as exc:
        print(exc, flush=True)
        return 1

    print(f"ranking snapshot: {snap_path}", flush=True)
    df = pd.read_parquet(snap_path)
    print(f"  {len(df)} option rows loaded", flush=True)

    ranked = rank_snapshot(df)
    print(f"  {len(ranked)} ranked candidates after filters + GROUND", flush=True)

    if ranked.empty:
        print("nothing ranked; writing empty payload anyway", flush=True)

    qualified_count = int(ranked["qualified"].sum()) if not ranked.empty and "qualified" in ranked else 0
    payload = _serialize(
        ranked,
        snap_path,
        provenance=ranked_provenance(snap_path, len(ranked), qualified_count),
    )

    latest_path = Path(live_config.RANKED_DIR) / "latest.json"
    archive_path = Path(live_config.RANKED_DIR) / f"{snap_path.parent.name}_{snap_path.stem}.json"
    _atomic_write_json(latest_path, payload)
    _atomic_write_json(archive_path, payload)
    print(f"wrote {latest_path}", flush=True)
    print(f"wrote {archive_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
