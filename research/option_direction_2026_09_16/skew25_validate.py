"""Validate same-expiry 25-delta put-call skew as a short-horizon direction signal.

Signal:
    skew25 = median IV(puts, abs(delta) in [.20,.30])
             - median IV(calls, abs(delta) in [.20,.30])
    bullish_skew25 = -skew25

The tests are deliberately causal at the daily-EOD level used by the repository's
backtests. They do not claim that an EOD-only historical snapshot is the exact 3pm
live signal; it is the closest replay available in this dataset.
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ent_canon as ec

HERE = Path(__file__).resolve().parent
FRAME = ROOT / "research/dkl_2026_09_13/featATM6.parquet"
GAP_SERIES = ROOT / "output/name_gaps_backtest.parquet"
SKEW_CACHE = HERE / "skew25_by_chain.parquet"
REPORT_TXT = HERE / "skew25_validate.txt"
REPORT_CSV = HERE / "skew25_years.csv"
BUCKET_CSV = HERE / "skew25_buckets.csv"


def log(s: str) -> None:
    print(s, flush=True)


def outcome(r: pd.Series) -> str:
    sp, ss, ls = r["expiry_close"], r["short_strike"], r["long_strike"]
    if r["spread_type"] == "bull_put":
        return "WIN" if sp > ss else ("LOSS" if sp <= ls else "PARTIAL")
    return "WIN" if sp < ss else ("LOSS" if sp >= ls else "PARTIAL")


def pnl(r: pd.Series) -> float:
    c = r["credit"]
    ml = r["max_loss_adj"]
    sp, ss, ls = r["expiry_close"], r["short_strike"], r["long_strike"]
    if r["spread_type"] == "bull_put":
        val = c if sp >= ss else (-ml if sp <= ls else c - (ss - sp))
    else:
        val = c if sp <= ss else (-ml if sp >= ls else c - (sp - ss))
    val *= 100.0
    if r["_outcome"] == "PARTIAL" and val > 0:
        val *= 0.5
    return val - ec.COMMISSION


def add_canon_scores(C: pd.DataFrame) -> pd.DataFrame:
    C = C.copy()
    C["entry_date"] = pd.to_datetime(C["entry_date"]).dt.normalize()
    C["expiry_date"] = pd.to_datetime(C["expiry_date"]).dt.normalize()
    closes = ec.backtest_closes()
    gaps = pd.read_parquet(GAP_SERIES)
    mu = ec.gap_drift(C, closes, gaps) if ec.GAP_GAMMA else None
    P = ec.p_real(C, closes, mu=mu)
    C["p"], C["q"], C["ro"] = P[:, 0], P[:, 1], P[:, 2]
    b = C["model_credit"].to_numpy(float) / (C["width"].to_numpy(float) - C["model_credit"].to_numpy(float))
    _, ell = ec.kelly(
        np.nan_to_num(C["p"].to_numpy(float)),
        np.nan_to_num(C["q"].to_numpy(float)),
        np.nan_to_num(C["ro"].to_numpy(float)),
        b,
    )
    ell[~np.isfinite(C["p"].to_numpy(float))] = np.nan
    C["EV"] = np.exp(ell) - 1.0
    C["GROUND"] = C["EV"] * np.exp(-ec.K * C["D_ent"])
    return C


def build_skew_cache(force: bool = False) -> pd.DataFrame:
    if SKEW_CACHE.exists() and not force:
        log(f"loading {SKEW_CACHE}")
        return pd.read_parquet(SKEW_CACHE)

    cands = pd.read_parquet(FRAME, columns=["ticker", "entry_date", "expiry_date"])
    keys = cands.drop_duplicates().rename(
        columns={"ticker": "Symbol", "entry_date": "DataDate", "expiry_date": "ExpirationDate"}
    )
    keys["DataDate"] = pd.to_datetime(keys["DataDate"]).dt.normalize()
    keys["ExpirationDate"] = pd.to_datetime(keys["ExpirationDate"]).dt.normalize()

    rows = []
    cols = [
        "Symbol",
        "DataDate",
        "ExpirationDate",
        "PutCall",
        "ImpliedVolatility",
        "Delta",
        "BidPrice",
        "AskPrice",
        "OpenInterest",
    ]
    for year in range(2020, 2027):
        path = ROOT / f"output/{year}_sp500_last.parquet"
        kk = keys[keys["DataDate"].dt.year == year]
        if kk.empty:
            continue
        log(f"reading {path.name}")
        d = pd.read_parquet(path, columns=cols)
        d["DataDate"] = pd.to_datetime(d["DataDate"]).dt.normalize()
        d["ExpirationDate"] = pd.to_datetime(d["ExpirationDate"]).dt.normalize()
        d = d.merge(kk, on=["Symbol", "DataDate", "ExpirationDate"], how="inner")
        d["PutCall"] = d["PutCall"].astype(str).str.lower().str.strip()
        d["abs_delta"] = pd.to_numeric(d["Delta"], errors="coerce").abs()
        d["iv"] = pd.to_numeric(d["ImpliedVolatility"], errors="coerce")
        d = d[
            d["abs_delta"].between(0.20, 0.30)
            & (d["iv"] > 0.03)
            & (d["iv"] < 3.0)
            & (d["BidPrice"] > 0)
            & (d["AskPrice"] > d["BidPrice"])
        ].copy()
        if d.empty:
            continue
        g = ["Symbol", "DataDate", "ExpirationDate"]
        med = d.groupby(g + ["PutCall"], sort=False)["iv"].median().unstack()
        cnt = d.groupby(g + ["PutCall"], sort=False)["iv"].size().unstack()
        oi = d.groupby(g + ["PutCall"], sort=False)["OpenInterest"].sum().unstack()
        out = med.rename(columns={"put": "put25_iv", "call": "call25_iv"}).reset_index()
        out = out.merge(cnt.add_prefix("n_").reset_index(), on=g, how="left")
        out = out.merge(oi.add_prefix("oi_").reset_index(), on=g, how="left")
        rows.append(out)

    if not rows:
        raise RuntimeError("No skew rows built")
    S = pd.concat(rows, ignore_index=True)
    S["skew25"] = S["put25_iv"] - S["call25_iv"]
    S = S.rename(columns={"Symbol": "ticker", "DataDate": "entry_date", "ExpirationDate": "expiry_date"})
    S.to_parquet(SKEW_CACHE, index=False)
    log(f"wrote {SKEW_CACHE} rows={len(S):,}")
    return S


def spearman(a: pd.Series, b: pd.Series) -> float:
    x = pd.Series(a).rank()
    y = pd.Series(b).rank()
    return float(x.corr(y))


def daily_spearman(df: pd.DataFrame, sig: str, target: str) -> tuple[float, int]:
    vals = []
    for _, g in df.groupby("entry_date", sort=False):
        h = g[[sig, target]].dropna()
        if len(h) >= 10 and h[sig].nunique() > 1 and h[target].nunique() > 1:
            vals.append(spearman(h[sig], h[target]))
    if not vals:
        return math.nan, 0
    return float(np.nanmean(vals)), len(vals)


def _rank_corr_arrays(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 10:
        return math.nan
    xr = pd.Series(x).rank(method="average").to_numpy(float)
    yr = pd.Series(y).rank(method="average").to_numpy(float)
    xr -= xr.mean()
    yr -= yr.mean()
    den = math.sqrt(float(np.dot(xr, xr) * np.dot(yr, yr)))
    return float(np.dot(xr, yr) / den) if den else math.nan


def date_shuffle_null(df: pd.DataFrame, n: int = 200, seed: int = 20260916) -> pd.DataFrame:
    """Shuffle the signal across names within each entry_date, preserving date regimes."""
    rng = np.random.default_rng(seed)
    base = df[["entry_date", "bullish_skew25", "fut_ret", "year"]].dropna().copy()
    out = []
    for year, gy in base.groupby("year", sort=True):
        groups = []
        obs_vals = []
        for _, gd in gy.groupby("entry_date", sort=False):
            if len(gd) < 10 or gd["bullish_skew25"].nunique() <= 1 or gd["fut_ret"].nunique() <= 1:
                continue
            xr = pd.Series(gd["bullish_skew25"].to_numpy(float)).rank(method="average").to_numpy(float)
            yr = pd.Series(gd["fut_ret"].to_numpy(float)).rank(method="average").to_numpy(float)
            xr -= xr.mean()
            yr -= yr.mean()
            xden = math.sqrt(float(np.dot(xr, xr)))
            yden = math.sqrt(float(np.dot(yr, yr)))
            if xden and yden:
                groups.append((xr, yr, xden, yden))
                obs_vals.append(float(np.dot(xr, yr) / (xden * yden)))
        observed = float(np.nanmean(obs_vals)) if obs_vals else math.nan
        nd = len(groups)
        vals = []
        for _ in range(n):
            daily = []
            for xr, yr, xden, yden in groups:
                xs = xr.copy()
                rng.shuffle(xs)
                daily.append(float(np.dot(xs, yr) / (xden * yden)))
            vals.append(float(np.nanmean(daily)) if daily else math.nan)
        vals = np.asarray(vals, dtype=float)
        out.append(
            {
                "year": int(year),
                "observed_daily_spearman": observed,
                "null_p05": float(np.nanpercentile(vals, 5)),
                "null_p50": float(np.nanpercentile(vals, 50)),
                "null_p95": float(np.nanpercentile(vals, 95)),
                "beat_null_95": bool(observed > np.nanpercentile(vals, 95)),
                "days": nd,
            }
        )
    return pd.DataFrame(out)


def bucket_table(C: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for year, gy in C.dropna(subset=["bullish_skew25", "fut_ret"]).groupby("year", sort=True):
        yy = gy.copy()
        yy["bucket"] = yy.groupby("entry_date")["bullish_skew25"].transform(
            lambda s: pd.qcut(s.rank(method="first"), 5, labels=False, duplicates="drop") if len(s) >= 5 else np.nan
        )
        for (bucket, spread_type), g in yy.groupby(["bucket", "spread_type"], sort=True):
            if pd.isna(bucket):
                continue
            want_up = g["spread_type"].eq("bull_put")
            win = np.where(want_up, g["expiry_close"] > g["short_strike"], g["expiry_close"] < g["short_strike"])
            rows.append(
                {
                    "year": int(year),
                    "bucket": int(bucket) + 1,
                    "spread_type": spread_type,
                    "n": len(g),
                    "avg_fut_ret": float(g["fut_ret"].mean()),
                    "full_win": float(np.mean(win)),
                }
            )
    return pd.DataFrame(rows)


def selected_book(sel: pd.DataFrame) -> dict[str, float]:
    x = sel.copy()
    x["credit"] = (x["model_credit"] * ec.FILL_MULT).round(4)
    x["max_loss_adj"] = (x["width"] - x["credit"]).round(4)
    x = x[x["max_loss_adj"] > 0].copy()
    x["_outcome"] = x.apply(outcome, axis=1)
    x["pnl"] = x.apply(pnl, axis=1)
    return {
        "n": float(len(x)),
        "pnl": float(x["pnl"].sum()),
        "full_win": float((x["_outcome"] == "WIN").mean()),
        "profitable": float((x["pnl"] > 0).mean()),
        "days": float(x["entry_date"].nunique()),
    }


def choose_direction(train: pd.DataFrame) -> int:
    h = train[["bullish_skew25", "fut_ret"]].dropna()
    if len(h) < 100 or h["bullish_skew25"].nunique() < 2:
        return 0
    c = spearman(h["bullish_skew25"], h["fut_ret"])
    return 1 if c >= 0 else -1


def rank_books(C: pd.DataFrame) -> pd.DataFrame:
    rows = []
    C = C.dropna(subset=["GROUND"]).copy()
    C["base_rank"] = C["GROUND"]
    C["skew_signed"] = np.nan
    C["skew_year_sign"] = 0
    for year in sorted(C["entry_date"].dt.year.unique()):
        train = C[C["entry_date"].dt.year < year]
        sign = choose_direction(train)
        m = C["entry_date"].dt.year == year
        C.loc[m, "skew_year_sign"] = sign
        C.loc[m, "skew_signed"] = sign * C.loc[m, "bullish_skew25"]

    for year, g in C.groupby(C["entry_date"].dt.year):
        q = g["skew_signed"].rank(pct=True)
        C.loc[g.index, "skew_pct"] = q.fillna(0.5)

    for label, score in (
        ("base", "base_rank"),
        ("base_plus_5pct_skew", "rank_plus_5"),
        ("base_plus_10pct_skew", "rank_plus_10"),
        ("skew_veto_bottom_20pct", "veto20_rank"),
    ):
        if score == "rank_plus_5":
            C[score] = C["GROUND"] * (1.0 + 0.05 * (C["skew_pct"] - 0.5))
            pool = C[C["GROUND"] >= ec.THR]
        elif score == "rank_plus_10":
            C[score] = C["GROUND"] * (1.0 + 0.10 * (C["skew_pct"] - 0.5))
            pool = C[C["GROUND"] >= ec.THR]
        elif score == "veto20_rank":
            C[score] = C["GROUND"]
            pool = C[(C["GROUND"] >= ec.THR) & (C["skew_pct"] >= 0.20)]
        else:
            pool = C[C["GROUND"] >= ec.THR]
        sel = pool.sort_values(["entry_date", score], ascending=[True, False]).groupby("entry_date").head(ec.TOP_N)
        b = selected_book(sel)
        b["book"] = label
        base_keys = set(
            C[C["GROUND"] >= ec.THR]
            .sort_values(["entry_date", "GROUND"], ascending=[True, False])
            .groupby("entry_date")
            .head(ec.TOP_N)
            .index
        )
        this_keys = set(sel.index)
        b["changed_vs_base"] = float(1.0 - len(base_keys & this_keys) / len(base_keys)) if base_keys else np.nan
        rows.append(b)
        for y, yy in sel.groupby(sel["entry_date"].dt.year):
            by = selected_book(yy)
            by["book"] = label
            by["year"] = int(y)
            rows.append(by)
    return pd.DataFrame(rows)


def main() -> None:
    force = "--force-skew" in sys.argv
    S = build_skew_cache(force=force)
    C = pd.read_parquet(FRAME)
    C = add_canon_scores(C)
    C["entry_date"] = pd.to_datetime(C["entry_date"]).dt.normalize()
    C["expiry_date"] = pd.to_datetime(C["expiry_date"]).dt.normalize()
    S["entry_date"] = pd.to_datetime(S["entry_date"]).dt.normalize()
    S["expiry_date"] = pd.to_datetime(S["expiry_date"]).dt.normalize()
    C = C.merge(S, on=["ticker", "entry_date", "expiry_date"], how="left")
    C["bullish_skew25"] = -C["skew25"]
    C["fut_ret"] = C["expiry_close"] / C["entry_price"] - 1.0
    C["year"] = C["entry_date"].dt.year

    rows = []
    for year, g in C.groupby("year", sort=True):
        v = g.dropna(subset=["bullish_skew25", "fut_ret"])
        dcor, ndays = daily_spearman(v, "bullish_skew25", "fut_ret")
        rows.append(
            {
                "year": int(year),
                "candidates": len(g),
                "with_skew": len(v),
                "coverage": len(v) / len(g) if len(g) else np.nan,
                "pooled_spearman": spearman(v["bullish_skew25"], v["fut_ret"]) if len(v) else np.nan,
                "daily_spearman_mean": dcor,
                "daily_spearman_days": ndays,
            }
        )
    Y = pd.DataFrame(rows)
    B = rank_books(C)
    N = date_shuffle_null(C)
    BK = bucket_table(C)
    Y.to_csv(REPORT_CSV, index=False)
    BK.to_csv(BUCKET_CSV, index=False)

    lines = []
    lines.append("skew25 validation")
    lines.append(f"candidate rows: {len(C):,}")
    lines.append(f"skew cache rows: {len(S):,}")
    lines.append("")
    lines.append("Directional rank test: bullish_skew25 = -skew25, target = expiry_close / entry_price - 1")
    lines.append(Y.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    lines.append("")
    lines.append("Within-date shuffle null for daily Spearman, signal shuffled across names on each date")
    lines.append(N.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    lines.append("")
    lines.append("Directional quintiles by date, Q1 least bullish skew, Q5 most bullish skew")
    piv = BK.pivot_table(index=["year", "spread_type"], columns="bucket", values="full_win")
    lines.append(piv.to_string(float_format=lambda x: f"{x:.4f}"))
    lines.append("")
    lines.append("Selection book tests, canon GROUND threshold/top-5, skew sign fitted only on prior years")
    lines.append(B[B["year"].isna()].drop(columns=["year"], errors="ignore").to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    lines.append("")
    lines.append("Per-year selected book tests")
    lines.append(B[B["year"].notna()].to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    REPORT_TXT.write_text("\n".join(lines) + "\n")
    log("\n".join(lines))
    log(f"wrote {REPORT_TXT}")


if __name__ == "__main__":
    main()
