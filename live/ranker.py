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

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config as backtest_config
import spreads
import ground
import empirical_runner as er
from live import live_config
from live.regime import current_regime


# ── Empirical pool: install latest window at module import ─────────────────
# Required for the canonical empirical-DKL scoring (DKL_REFERENCE="empirical_vs_delta").
# Without this, ground falls back to uniform DKL. Uses the same-day window
# cache so half-hourly firings skip the 15M-row pool load.
try:
    _asof = er.install_latest_cached()
    print(f"[ranker] empirical window installed (asof {_asof.date()})", flush=True)
except Exception as e:
    print(f"[ranker] WARN: empirical pool not available ({e}). "
          f"GROUND will use uniform-DKL fallback.", flush=True)


# ── Snapshot discovery ──────────────────────────────────────────────────────

def _latest_snapshot() -> Path | None:
    base = Path(live_config.SNAPSHOTS_DIR)
    if not base.exists():
        return None
    days = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    for d in days:
        files = sorted(d.glob("*.parquet"), reverse=True)
        if files:
            return files[0]
    return None


# ── Ranking pipeline ────────────────────────────────────────────────────────

def rank_snapshot(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full backtest-canonical ranking pipeline on a live snapshot."""
    if df.empty:
        return pd.DataFrame()

    # Liquidity gate. Live uses live_config.LIVE_MIN_OPEN_INTEREST (default 0,
    # since IBKR returns NaN for OI mid-session and the assembler defaults to 0).
    # Backtest path doesn't run this ranker so its MIN_OPEN_INTEREST=100 is fine.
    if "OpenInterest" in df.columns:
        before = len(df)
        df = df[df["OpenInterest"] >= live_config.LIVE_MIN_OPEN_INTEREST]
        print(f"  OI gate {live_config.LIVE_MIN_OPEN_INTEREST}: kept {len(df)}/{before} rows",
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
    spreads.GAP_FILTER        = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS    = 0.0

    # Override the OI gate inside spreads.build_candidates for live mode.
    # IBKR returns NaN for OI mid-session, so the canonical 100 floor would
    # drop every spread. live_config.LIVE_MIN_OPEN_INTEREST defaults to 0.
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
    candidates = spreads.build_candidates(df)
    print(f"  built {len(candidates)} candidate spreads", flush=True)

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
            def has_exdiv_in_window(row):
                if row['spread_type'] != 'bear_call':
                    return False
                tk = row['ticker']
                dates = dc_by_sym.get(tk, set())
                if not dates:
                    return False
                start = pd.Timestamp(row['entry_date']).date()
                end   = (pd.Timestamp(row['expiry_date']) + pd.Timedelta(days=1)).date()
                return any(start <= d <= end for d in dates)
            before = len(candidates)
            mask_exdiv = candidates.apply(has_exdiv_in_window, axis=1)
            candidates = candidates[~mask_exdiv].copy()
            dropped = before - len(candidates)
            if dropped > 0:
                print(f"  ex-div gate: dropped {dropped} bear-call(s)", flush=True)
    except Exception as e:
        print(f"  ex-div gate: ERR {type(e).__name__}: {e}", flush=True)
    if candidates.empty:
        return pd.DataFrame()

    # Score with GROUND. score_candidates adds {p, q, ro, w_star, G, DKL, EV}
    # per row; the intrinsic GROUND = E · exp(−k·DKL) is computed directly from
    # G and DKL on each row (no per-week reference needed under canonical form).
    scored = ground.score_candidates(candidates)

    import math
    def _intrinsic_ground(row):
        G = row.get("G")
        DKL = row.get("DKL")
        if G is None or pd.isna(G) or DKL is None or pd.isna(DKL):
            return float("nan")
        return (math.exp(G) - 1.0) * math.exp(-ground.DKL_K * DKL)

    scored["GROUND"] = scored.apply(_intrinsic_ground, axis=1)

    # Drop rows where GROUND couldn't be computed (e.g. extreme probabilities,
    # growth-negative candidates).
    ranked = scored.dropna(subset=["GROUND"]).copy()

    # One direction per ticker (2026-06-10): bull_put and bear_call on the same
    # name are contradictory bets; keep only the better-GROUND one.
    before = len(ranked)
    ranked = (ranked.sort_values("GROUND", ascending=False)
                    .groupby("ticker", as_index=False).head(1))
    if len(ranked) < before:
        print(f"  per-ticker dedupe: {before} -> {len(ranked)} rows", flush=True)

    # Canonical 2026-06-12 (corrected solver): DKL=rv_vs_iv, k=10 (via ground.DKL_K),
    # threshold 0.05 across all dows (config.GROUND_THRESHOLD). Top-5 per dow.
    thr = backtest_config.GROUND_THRESHOLD
    PER_DOW_THRESHOLDS = {0: thr, 1: thr, 2: thr, 3: thr}
    entry_dows = pd.to_datetime(ranked.get("entry_date")).dt.dayofweek
    thresholds = entry_dows.map(PER_DOW_THRESHOLDS).fillna(thr)
    ranked["qualified"] = ranked["GROUND"] >= thresholds

    # Sort by GROUND descending (qualified first, then below-threshold).
    ranked = ranked.sort_values("GROUND", ascending=False).reset_index(drop=True)
    print(f"  qualified: {ranked['qualified'].sum()}/{len(ranked)} above per-DOW thresholds "
          f"(all days {thr})", flush=True)
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


def _serialize(ranked: pd.DataFrame, snapshot_path: Path) -> dict:
    """Build the JSON payload consumed by the webapp."""
    snap_time = datetime.now()

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
    # ticker_rows is the full ranked candidate list (top TICKER_LIMIT by GROUND)
    # for the table below; template dims rows that are either rank>5 or below
    # threshold.
    # Canonical: top cards = top-5 QUALIFIED picks only (above per-DOW threshold).
    # On low-edge days you may see < 5 cards — that's intentional, only show
    # picks that actually meet the GROUND threshold. Full ranked list (including
    # below-threshold) lives in `ticker_rows` for the table below.
    qualified_only = ranked[ranked.get("qualified", False) == True] if not ranked.empty else ranked
    top = qualified_only.head(live_config.TOP_N_DISPLAY)
    ticker_rows = ranked.head(live_config.TICKER_LIMIT)

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
            "w_star":           _num(r.get("w_star")),
            "qualified":        bool(r.get("qualified", True)),
        }

    return {
        "snapshot_ts":   snap_time.isoformat(timespec="seconds"),
        "snapshot_file": str(snapshot_path.relative_to(ROOT)),
        "data_date":     snap_time.date().isoformat(),
        "n_candidates":  int(len(ranked)),
        "config": {
            "DTE_MIN":          live_config.LIVE_DTE_MIN,
            "DTE_MAX":          live_config.LIVE_DTE_MAX,
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
            "DKL_K":            getattr(ground, "DKL_K", 50.0),
            "ALPHA":            "(b-1)/(2b)",
        },
        "regime":    current_regime(),
        "vol_gate":  gate,
        "top_picks": [row_to_dict(r) for _, r in top.iterrows()],
        "ticker":    [row_to_dict(r) for _, r in ticker_rows.iterrows()],
    }


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
    args = parser.parse_args()

    if args.snapshot:
        snap_path = Path(args.snapshot).resolve()
    else:
        latest = _latest_snapshot()
        if latest is None:
            print("no snapshots found in live/snapshots/", flush=True)
            return 1
        snap_path = latest

    print(f"ranking snapshot: {snap_path}", flush=True)
    df = pd.read_parquet(snap_path)
    print(f"  {len(df)} option rows loaded", flush=True)

    ranked = rank_snapshot(df)
    print(f"  {len(ranked)} ranked candidates after filters + GROUND", flush=True)

    if ranked.empty:
        print("nothing ranked; writing empty payload anyway", flush=True)

    payload = _serialize(ranked, snap_path)

    latest_path = Path(live_config.RANKED_DIR) / "latest.json"
    archive_path = Path(live_config.RANKED_DIR) / f"{snap_path.parent.name}_{snap_path.stem}.json"
    _atomic_write_json(latest_path, payload)
    _atomic_write_json(archive_path, payload)
    print(f"wrote {latest_path}", flush=True)
    print(f"wrote {archive_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
