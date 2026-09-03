"""Build the euro-profile backtest payload from isolated euro parquets.

Inputs:
  output/euro_parquets/<year>_euro_last*.parquet
  output/euro_parquets/euro_pool.parquet
  output/euro_parquets/euro_iv_rank.parquet

Outputs:
  live/data/euro/backtest_equity.json
  output/euro_parquets/euro_picks_cache_*.parquet

The parquet cache is write-once by default. Use --cache-path or --cache-suffix
for a new run instead of replacing an existing parquet.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
import config as bt_config
import empirical_runner as er
import ground
import spreads


START_BANKROLL = 10_000.0
ACTIVE_DOWS = [0, 1, 2, 3]  # entry weekdays; overridden by --entry-dows
DOW_LONG = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
SPY_CSV = "data/spy_us_d.csv"

# Two-leg open-interest floor, overridden by --min-oi. Module-level so score_year
# does not need it threaded through every caller.
MIN_OI = 100
# Per-share max-loss cap, overridden by --max-max-loss. inf disables. Written for
# 5-point strikes: NDX lists 10-point strikes, so its spreads are 10 wide and the
# $5 cap rejects any of them collecting under $5 credit.
MAX_ML = 5.0
DTE_MIN, DTE_MAX = 1, 4  # overridden by --dte-min/--dte-max


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build euro cash-settled index backtest JSON")
    p.add_argument("--years", default=None,
                   help="Comma-separated years. Default: all euro year parquets found.")
    p.add_argument("--symbols", default=None,
                   help="Comma-separated root override. Default: config.EURO_INDEX_ROOTS.")
    p.add_argument("--thr", type=float, default=bt_config.GROUND_THRESHOLD)
    p.add_argument("--dte-min", type=int, default=1)
    p.add_argument("--dte-max", type=int, default=4,
                   help="Scoring DTE range. The pool must cover the same range "
                        "(build_euro_pool.py --dte-max); historical_probs matches DTE exactly.")
    p.add_argument("--entry-dows", default="0,1,2,3",
                   help="Entry weekdays, 0=Mon. Default Mon-Thu. Add 4 to enter Fridays "
                        "(reaches Mon expiry at DTE 3 and Tue at DTE 4).")
    p.add_argument("--max-max-loss", type=float, default=5.0,
                   help="Per-share max-loss cap. Pass inf to disable. Binds only where "
                        "strike spacing exceeds it (NDX, and 64 of 467 equities).")
    p.add_argument("--min-oi", type=int, default=100,
                   help="Two-leg open-interest floor. 0 disables. NDX never clears "
                        "100 (best two-leg OI all year is 49).")
    p.add_argument("--pool", default="output/euro_parquets/euro_pool.parquet")
    p.add_argument("--iv-rank", default="output/euro_parquets/euro_iv_rank.parquet")
    p.add_argument("--rv-table", default="output/euro_parquets/euro_rv_table.parquet",
                   help="RV lookup. Defaults to the euro table: output/rv_table.parquet "
                        "covers only SPXW/RUTW, so SPX/NDX/RUT score nothing against it.")
    p.add_argument("--out", default="live/data/euro/backtest_equity.json")
    p.add_argument("--cache-path", default=None)
    p.add_argument("--cache-suffix", default="")
    p.add_argument("--no-cache", action="store_true",
                   help="Re-score picks. Refuses to overwrite an existing cache path.")
    return p.parse_args()


def _suffix(raw: str) -> str:
    raw = (raw or "").strip()
    if raw and not raw.startswith("_"):
        raw = "_" + raw
    return raw


def _safe_write_parquet(df: pd.DataFrame, path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"{path} already exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists():
        raise FileExistsError(f"{tmp} already exists; remove stale temp manually")
    try:
        df.to_parquet(tmp, index=False, compression="snappy")
        if path.exists():
            raise FileExistsError(f"{path} appeared during write; refusing to overwrite")
        tmp.rename(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _find_year_files(years: list[int] | None) -> dict[int, str]:
    files = sorted(glob.glob("output/euro_parquets/[0-9][0-9][0-9][0-9]_euro_last*.parquet"))
    out: dict[int, str] = {}
    for fp in files:
        year = int(Path(fp).name[:4])
        out.setdefault(year, fp)
    if years is not None:
        missing = [y for y in years if y not in out]
        if missing:
            raise FileNotFoundError(f"Missing euro year parquets for: {missing}")
        out = {y: out[y] for y in years}
    if not out:
        raise FileNotFoundError("No euro year parquets found under output/euro_parquets")
    return dict(sorted(out.items()))


def _parse_years(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _parse_symbols(raw: str | None) -> set[str]:
    if not raw:
        return set(bt_config.EURO_INDEX_ROOTS)
    return {x.strip().upper() for x in raw.split(",") if x.strip()}


def load_spy_daily() -> pd.DataFrame:
    frames = [pd.read_csv(SPY_CSV, parse_dates=["Date"])]
    spy = pd.concat(frames, ignore_index=True)
    spy["Date"] = pd.to_datetime(spy["Date"], errors="coerce")
    spy = spy.dropna(subset=["Date"])
    for col in ["Open", "High", "Low", "Close"]:
        if col in spy.columns:
            spy[col] = pd.to_numeric(spy[col], errors="coerce")
    return (spy.dropna(subset=["Close"])
               .drop_duplicates(subset="Date", keep="last")
               .sort_values("Date")
               .reset_index(drop=True))


def _lookup_frame(path: str, cols: list[str]) -> pd.DataFrame | None:
    try:
        df = pd.read_parquet(path)
    except FileNotFoundError:
        print(f"WARNING: {path} not found")
        return None
    if df.empty:
        return None
    df["DataDate"] = pd.to_datetime(df["DataDate"], errors="coerce")
    return df[cols]


def score_year(path: str, symbols: set[str], pool: pd.DataFrame,
               iv_lookup: pd.DataFrame | None, rv_lookup: pd.DataFrame | None) -> tuple[pd.DataFrame, dict]:
    df_full = pd.read_parquet(path)
    df_full["Symbol"] = df_full["Symbol"].astype(str).str.upper().str.strip()
    df_full = df_full[df_full["Symbol"].isin(symbols)].copy()
    if df_full.empty:
        return pd.DataFrame(), {}

    df_full["DataDate"] = pd.to_datetime(df_full["DataDate"], errors="coerce")
    df_full["ExpirationDate"] = pd.to_datetime(df_full["ExpirationDate"], errors="coerce")
    # Known-bad vendor spots would otherwise become both candidate entry prices
    # and expiry_close settlement values.
    df_full = bt_config.drop_bad_spot_days(df_full)
    df_full["PutCall"] = df_full["PutCall"].astype(str).str.lower().str.strip()
    for col in [
        "DTE", "LastPrice", "BidPrice", "AskPrice", "Delta", "StrikePrice",
        "UnderlyingPrice", "OpenInterest", "ImpliedVolatility",
    ]:
        if col in df_full.columns:
            df_full[col] = pd.to_numeric(df_full[col], errors="coerce")
    expiry_close = (df_full[df_full["DataDate"] == df_full["ExpirationDate"]]
                    .groupby(["Symbol", "ExpirationDate"])["UnderlyingPrice"]
                    .first().to_dict())

    spy_dates = set(load_spy_daily()["Date"].dt.normalize())
    df = df_full.copy()
    df["dow"] = df["DataDate"].dt.dayofweek
    df = df[df["dow"].isin(ACTIVE_DOWS)]
    df = df[df["DTE"].between(DTE_MIN, DTE_MAX)]
    df = df[df["LastPrice"].astype(float) > 0]
    df = df[df["DataDate"].dt.normalize().isin(spy_dates)]
    df["AbsDelta"] = df["Delta"].abs()
    df["MidPrice"] = (df["BidPrice"] + df["AskPrice"]) / 2.0

    if iv_lookup is not None:
        df = df.merge(iv_lookup, on=["Symbol", "DataDate"], how="left")
    if rv_lookup is not None:
        df = df.merge(rv_lookup, on=["Symbol", "DataDate"], how="left")

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = False
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = MIN_OI
    bt_config.MAX_MAX_LOSS = MAX_ML
    bt_config.CREDIT_BASIS = "last_clamped"
    bt_config.CREDIT_SCALE = 1.0

    candidates = spreads.build_candidates(df)
    if candidates.empty or "entry_date" not in candidates.columns:
        return pd.DataFrame(), expiry_close

    parts = []
    for dt in sorted(candidates["entry_date"].unique()):
        sub = candidates[candidates["entry_date"] == dt]
        ok = er.install_window(pool, pd.Timestamp(dt))
        if not ok:
            import historical_probs as hp
            hp._EMPIRICAL_TABLE = None
        parts.append(ground.score_candidates(sub))
    scored = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if scored.empty:
        return scored, expiry_close

    def gnd(row):
        g = row.get("G")
        dkl = row.get("DKL")
        if g is None or dkl is None or pd.isna(g) or pd.isna(dkl):
            return float("nan")
        return (math.exp(g) - 1.0) * math.exp(-ground.DKL_K * dkl)

    scored["GROUND"] = scored.apply(gnd, axis=1)
    return scored.dropna(subset=["GROUND"]), expiry_close


def realize(scored: pd.DataFrame, expiry_close: dict, threshold: float) -> pd.DataFrame:
    cols = ["entry_date", "realize_date", "pnl_per_contract", "max_loss_dollar"]
    if scored.empty or "entry_date" not in scored.columns:
        return pd.DataFrame(columns=cols)
    s = scored.copy()
    s["entry_dow"] = pd.to_datetime(s["entry_date"]).dt.dayofweek
    picked = []
    for dow in ACTIVE_DOWS:
        sub = s[s["entry_dow"] == dow]
        qual = sub[sub["GROUND"] >= threshold]
        top = (qual.sort_values(["entry_date", "GROUND"], ascending=[True, False])
                   .groupby("entry_date").head(5))
        picked.append(top)
    sel = pd.concat(picked, ignore_index=True) if picked else pd.DataFrame()
    if sel.empty:
        return pd.DataFrame(columns=cols)

    sel["expiry_close"] = sel.apply(
        lambda row: expiry_close.get((row["ticker"], row["expiry_date"])), axis=1)
    ok = sel.dropna(subset=["expiry_close"]).copy()
    ok["credit"] = ok["net_credit"] * 0.80
    ok["width"] = ok["net_credit"] + ok["max_loss"]
    ok["max_loss_adj"] = ok["width"] - ok["credit"]
    ok["pnl_per_contract"] = ok.apply(lambda row: spreads.calc_pnl(
        row["expiry_close"], row["short_strike"], row["long_strike"],
        row["credit"], row["max_loss_adj"], row["spread_type"]), axis=1) * 100
    ok["max_loss_dollar"] = ok["max_loss_adj"] * 100
    ok["realize_date"] = pd.to_datetime(ok["expiry_date"])
    ok["entry_date_dt"] = pd.to_datetime(ok["entry_date"])
    return ok


def _build_picks(year_files: dict[int, str], symbols: set[str], pool: pd.DataFrame,
                 iv_lookup: pd.DataFrame | None, rv_lookup: pd.DataFrame | None,
                 threshold: float) -> pd.DataFrame:
    frames = []
    for year, fp in year_files.items():
        print(f"-- {year} --", flush=True)
        scored, expiry_close = score_year(fp, symbols, pool, iv_lookup, rv_lookup)
        picks = realize(scored, expiry_close, threshold)
        print(f"  picks: {len(picks):,}", flush=True)
        if not picks.empty:
            frames.append(picks)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("entry_date_dt").reset_index(drop=True)


def _outcome_class(row) -> str:
    sp = row["expiry_close"]
    ss = row["short_strike"]
    ls = row["long_strike"]
    if row["spread_type"] == "bull_put":
        if sp > ss:
            return "WIN"
        if sp <= ls:
            return "LOSS"
        return "PARTIAL"
    if sp < ss:
        return "WIN"
    if sp >= ls:
        return "LOSS"
    return "PARTIAL"


def simulate_equity(picks: pd.DataFrame, sizing):
    bankroll = START_BANKROLL
    daily_pnl = {}
    for _, row in picks.iterrows():
        if isinstance(sizing, str) and sizing.startswith("kelly_"):
            frac = float(sizing.split("_")[1])
            ws = row.get("w_star")
            ml_dollar = row["max_loss_dollar"]
            if ws is None or pd.isna(ws) or ws <= 0 or ml_dollar <= 0:
                qty = 1
            else:
                qty = max(1, min(5, int(frac * float(ws) * START_BANKROLL / ml_dollar)))
        else:
            qty = int(sizing)
        pnl = qty * row["pnl_per_contract"]
        rd = row["realize_date"]
        daily_pnl[rd] = daily_pnl.get(rd, 0.0) + pnl
        bankroll += pnl
        if bankroll <= 0:
            bankroll = 1
    return pd.Series(daily_pnl).sort_index()


def _summaries(picks: pd.DataFrame, years: list[int]) -> tuple[dict, list[dict], list[dict]]:
    pnl_qty2 = simulate_equity(picks, 2)
    pnl_qty1 = simulate_equity(picks, 1)
    pnl_sixteenk = simulate_equity(picks, "kelly_0.0625")

    spy = load_spy_daily().sort_values("Date").reset_index(drop=True)
    start_date = picks["entry_date_dt"].min().normalize()
    end_date = pd.Timestamp(f"{max(years)}-12-31")
    spy = spy[(spy["Date"] >= start_date) & (spy["Date"] <= end_date)].reset_index(drop=True)
    trading_days = pd.DatetimeIndex(spy["Date"])
    if trading_days.empty:
        raise ValueError("No SPY trading days in selected backtest window")

    eq_qty2 = START_BANKROLL + pnl_qty2.reindex(trading_days, fill_value=0.0).cumsum()
    eq_qty1 = START_BANKROLL + pnl_qty1.reindex(trading_days, fill_value=0.0).cumsum()
    eq_sixteenk = START_BANKROLL + pnl_sixteenk.reindex(trading_days, fill_value=0.0).cumsum()
    spy_eq = START_BANKROLL * (spy["Close"].values / spy["Close"].iloc[0])

    def sharpe(ret):
        sd = ret.std(ddof=0)
        return ret.mean() * np.sqrt(252) / sd if sd > 0 else 0.0

    def max_dd(eq):
        peak = eq.cummax() if hasattr(eq, "cummax") else pd.Series(eq).cummax()
        return float(((eq - peak) / peak).min())

    n_years = max((trading_days[-1] - trading_days[0]).days / 365.25, 1 / 365.25)

    def cagr(final):
        return ((final / START_BANKROLL) ** (1 / n_years) - 1) if final > 0 else -1

    def summary_for(eq, name):
        if isinstance(eq, pd.Series) and isinstance(eq.index, pd.DatetimeIndex):
            eq_s = eq
        else:
            eq_s = pd.Series(eq if not isinstance(eq, pd.Series) else eq.values, index=trading_days)
        ret = eq_s.diff().fillna(0) / eq_s.shift(1).fillna(START_BANKROLL)
        weekly_eq = eq_s.resample("W-FRI").last().ffill()
        weekly_ret = weekly_eq.pct_change().dropna()
        weekly_sd = weekly_ret.std(ddof=0)
        weekly_sh = float(weekly_ret.mean() * np.sqrt(52) / weekly_sd) if weekly_sd > 0 else 0.0
        final_val = float(eq_s.iloc[-1])
        return {
            f"{name}_final": round(final_val, 2),
            f"{name}_total_return": round((final_val - START_BANKROLL) / START_BANKROLL * 100, 2),
            f"{name}_cagr": round(cagr(final_val) * 100, 2),
            f"{name}_sharpe": round(float(sharpe(ret)), 2),
            f"{name}_sharpe_weekly": round(weekly_sh, 2),
            f"{name}_max_dd": round(float(max_dd(eq_s)) * 100, 2),
        }

    summary = {
        "window_start": trading_days[0].strftime("%Y-%m-%d"),
        "window_end": trading_days[-1].strftime("%Y-%m-%d"),
        "years": round(n_years, 2),
        "trading_days": len(trading_days),
        "n_trades": int(len(picks)),
    }
    summary.update(summary_for(eq_qty2, "strategy"))
    summary.update(summary_for(eq_qty1, "qty1"))
    summary.update(summary_for(eq_sixteenk, "sixteenk"))
    summary.update(summary_for(pd.Series(spy_eq), "spy"))

    ml_dollar_col = picks["max_loss_dollar"]
    wagered_qty2 = float((ml_dollar_col * 2).sum())
    wagered_qty1 = float(ml_dollar_col.sum())

    def kelly_qty_row(row, frac=0.0625, cap=5):
        ws = row.get("w_star")
        ml_d = row["max_loss_dollar"]
        if ws is None or pd.isna(ws) or ws <= 0 or ml_d <= 0:
            return 1
        return max(1, min(cap, int(frac * float(ws) * START_BANKROLL / ml_d)))

    wagered_sixteenk = float((picks.apply(kelly_qty_row, axis=1) * ml_dollar_col).sum())
    summary["strategy_wagered"] = round(wagered_qty2, 2)
    summary["qty1_wagered"] = round(wagered_qty1, 2)
    summary["sixteenk_wagered"] = round(wagered_sixteenk, 2)
    summary["strategy_yield"] = round(100.0 * (summary["strategy_final"] - START_BANKROLL) / wagered_qty2, 2) if wagered_qty2 else 0
    summary["qty1_yield"] = round(100.0 * (summary["qty1_final"] - START_BANKROLL) / wagered_qty1, 2) if wagered_qty1 else 0
    summary["sixteenk_yield"] = round(100.0 * (summary["sixteenk_final"] - START_BANKROLL) / wagered_sixteenk, 2) if wagered_sixteenk else 0

    points = []
    for i, d in enumerate(trading_days):
        points.append({
            "date": d.strftime("%Y-%m-%d"),
            "strategy": round(float(eq_qty2.iloc[i]), 2),
            "qty1": round(float(eq_qty1.iloc[i]), 2),
            "sixteenk": round(float(eq_sixteenk.iloc[i]), 2),
            "spy": round(float(spy_eq[i]), 2),
        })

    sorted_picks = picks.sort_values(["entry_date_dt", "GROUND"], ascending=[True, False]).copy()
    sorted_picks["_q"] = 2
    sorted_picks["_pnl"] = sorted_picks["_q"] * sorted_picks["pnl_per_contract"]
    sorted_picks["_week"] = sorted_picks["entry_date_dt"].dt.to_period("W-FRI")
    running = START_BANKROLL
    sorted_picks["_pre_bank"] = 0.0
    sorted_picks["_post_bank"] = 0.0
    for idx in sorted_picks.index:
        sorted_picks.at[idx, "_pre_bank"] = running
        running += sorted_picks.at[idx, "_pnl"]
        sorted_picks.at[idx, "_post_bank"] = running

    def row_dict(row):
        return {
            "entry": pd.Timestamp(row["entry_date_dt"]).strftime("%Y-%m-%d"),
            "dow": DOW_LONG.get(pd.Timestamp(row["entry_date_dt"]).dayofweek, "?"),
            "ticker": row["ticker"],
            "type": row["spread_type"],
            "k_s": round(float(row["short_strike"]), 2),
            "k_l": round(float(row["long_strike"]), 2),
            "credit": round(float(row["credit"]), 4),
            "max_loss": round(float(row["max_loss_adj"]), 4),
            "spot": round(float(row["expiry_close"]), 2),
            "qty": int(row["_q"]),
            "pnl": round(float(row["_pnl"]), 2),
            "ground": round(float(row["GROUND"]), 6),
            "dkl": round(float(row["DKL"]), 4) if pd.notna(row.get("DKL")) else None,
            "kelly_ev": round((math.exp(float(row["G"])) - 1.0) * 100, 2) if pd.notna(row.get("G")) else None,
            "outcome": _outcome_class(row),
        }

    weeks = []
    for _, grp in sorted_picks.groupby("_week", sort=True):
        grp = grp.sort_values(["entry_date_dt", "GROUND"], ascending=[True, False])
        n_bp = int((grp["spread_type"] == "bull_put").sum())
        n_bc = int((grp["spread_type"] == "bear_call").sum())
        weeks.append({
            "label": f"Week of {grp['entry_date_dt'].min().strftime('%b %d, %Y')}",
            "start": grp["entry_date_dt"].min().strftime("%Y-%m-%d"),
            "n_trades": int(len(grp)),
            "n_bull_put": n_bp,
            "n_bear_call": n_bc,
            "direction": n_bp - n_bc,
            "pnl": round(float(grp["_pnl"].sum()), 2),
            "credit": round(float((grp["credit"] * grp["_q"]).sum() * 100), 2),
            "risk": round(float((grp["max_loss_adj"] * grp["_q"]).sum() * 100), 2),
            "pre_bank": round(float(grp["_pre_bank"].iloc[0]), 2),
            "post_bank": round(float(grp["_post_bank"].iloc[-1]), 2),
            "max_g": round(float(grp["GROUND"].max()), 6),
            "trades": [row_dict(row) for _, row in grp.iterrows()],
        })
    return summary, points, weeks


def main() -> int:
    args = parse_args()
    years_arg = _parse_years(args.years)
    symbols = _parse_symbols(args.symbols)
    year_files = _find_year_files(years_arg)
    years = list(year_files)

    symbol_tag = "-".join(sorted(symbols)).lower()
    year_tag = f"{years[0]}-{years[-1]}" if years else "none"
    cache_path = Path(args.cache_path) if args.cache_path else Path(
        f"output/euro_parquets/euro_picks_cache_{year_tag}_{symbol_tag}_k{ground.DKL_K:g}_thr{args.thr:g}{_suffix(args.cache_suffix)}.parquet"
    )

    global MIN_OI, MAX_ML, ACTIVE_DOWS, DTE_MIN, DTE_MAX
    MIN_OI = args.min_oi
    MAX_ML = args.max_max_loss
    ACTIVE_DOWS = [int(x) for x in args.entry_dows.split(",") if x.strip()!=""]
    DTE_MIN, DTE_MAX = args.dte_min, args.dte_max
    print(f"Two-leg OI floor: {MIN_OI}   max-loss cap: {MAX_ML}   "
          f"entry dows: {[DOW_LONG[d] for d in ACTIVE_DOWS]}   DTE {DTE_MIN}-{DTE_MAX}", flush=True)

    iv_lookup = _lookup_frame(args.iv_rank, ["Symbol", "DataDate", "iv_rank_bucket"])
    rv_lookup = _lookup_frame(args.rv_table, ["Symbol", "DataDate", "rv_30d"])
    if rv_lookup is not None:
        have_rv = set(rv_lookup.loc[rv_lookup["rv_30d"].notna(), "Symbol"].unique())
        missing_rv = sorted(symbols - have_rv)
        covered = sorted(symbols & have_rv)
        print(f"RV table {args.rv_table}: covers {', '.join(covered) or 'none'} "
              f"of the requested roots ({len(have_rv):,} symbols total)", flush=True)
        if missing_rv:
            print(f"WARNING: no RV for {', '.join(missing_rv)} — these roots will score nothing",
                  flush=True)
    pool = er.load_master_pool(args.pool)
    print(f"Loaded euro pool: {len(pool):,} rows from {args.pool}", flush=True)

    if cache_path.exists() and not args.no_cache:
        print(f"Loading cached euro picks from {cache_path}", flush=True)
        picks = pd.read_parquet(cache_path)
        picks["entry_date_dt"] = pd.to_datetime(picks["entry_date_dt"])
        if "realize_date" in picks.columns:
            picks["realize_date"] = pd.to_datetime(picks["realize_date"])
    else:
        if cache_path.exists():
            raise FileExistsError(f"{cache_path} already exists; refusing to overwrite")
        print(f"Building euro picks for {year_tag}, symbols={','.join(sorted(symbols))}", flush=True)
        picks = _build_picks(year_files, symbols, pool, iv_lookup, rv_lookup, args.thr)
        print(f"Writing euro picks cache: {cache_path}", flush=True)
        _safe_write_parquet(picks, cache_path)

    if picks.empty:
        raise ValueError("No picks selected; not writing web payload")

    picks["_outcome"] = picks.apply(_outcome_class, axis=1)
    pwin = (picks["_outcome"] == "PARTIAL") & (picks["pnl_per_contract"] > 0)
    print(f"Partial-WIN haircut: discounting {int(pwin.sum())} picks to 50% intrinsic", flush=True)
    picks.loc[pwin, "pnl_per_contract"] *= 0.5

    summary, points, weeks = _summaries(picks, years)
    trade_log_rows = [trade for week in weeks for trade in week["trades"]]
    payload = {
        "config": {
            "universe": ",".join(sorted(symbols)),
            "days": "Mon, Tue, Wed, Thu",
            "expiry": "any expiry weekday (DTE 1-4)",
            "selection": f"top-5 per day, k={ground.DKL_K:g}, GROUND threshold {args.thr:g}",
            "scoring": "European cash-settled index options; G_rv/DKL canon; no Friday-expiry filter",
            "fill_basis": "0.80 x clamped LAST; partial-WIN at 50% intrinsic",
            "regime": "OFF (both directions eligible)",
            "vol_gate": "OFF",
            "sizing": "qty=2 per pick",
            "starting_bankroll": START_BANKROLL,
        },
        "summary": summary,
        "points": points,
        "trades": trade_log_rows,
        "weeks": weeks,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {out_path}", flush=True)
    print(f"Final qty=2: ${summary['strategy_final']:,.0f} ({summary['strategy_total_return']:+.1f}%)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
