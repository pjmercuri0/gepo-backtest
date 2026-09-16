"""Option-implied forward pressure from same-strike call/put parity.

For each candidate chain, pair calls and puts at the same strike and compute:

    F_impl(K) = K + call_mid(K) - put_mid(K)     (r=q=0 over 1-4 DTE)

The level and shape of F_impl/S across near-ATM strikes is an options-market
direction signal that is different from skew/OI. Dividends/borrow/events can
pollute the level, so all signs are fit walk-forward from prior years only.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ent_canon as ec
from direction_signal_suite import (
    FRAME,
    HERE,
    add_canon_scores,
    outcome,
    pnl,
    selected_book,
    spearman_arrays,
)

CACHE = HERE / "parity_forward_features.parquet"
REPORT = HERE / "parity_forward_signal.txt"
SUMMARY = HERE / "parity_forward_summary.csv"
BOOKS = HERE / "parity_forward_books.csv"


def log(s: str) -> None:
    print(s, flush=True)


def build_cache(force: bool = False) -> pd.DataFrame:
    if CACHE.exists() and not force:
        log(f"loading {CACHE}")
        return pd.read_parquet(CACHE)
    keys = pd.read_parquet(FRAME, columns=["ticker", "entry_date", "expiry_date"]).drop_duplicates()
    keys = keys.rename(columns={"ticker": "Symbol", "entry_date": "DataDate", "expiry_date": "ExpirationDate"})
    keys["DataDate"] = pd.to_datetime(keys["DataDate"]).dt.normalize()
    keys["ExpirationDate"] = pd.to_datetime(keys["ExpirationDate"]).dt.normalize()
    cols = [
        "Symbol",
        "DataDate",
        "ExpirationDate",
        "DTE",
        "PutCall",
        "StrikePrice",
        "BidPrice",
        "AskPrice",
        "ImpliedVolatility",
        "Delta",
        "UnderlyingPrice",
        "OpenInterest",
    ]
    rows = []
    for year in range(2020, 2027):
        kk = keys[keys["DataDate"].dt.year == year]
        if kk.empty:
            continue
        path = ROOT / f"output/{year}_sp500_last.parquet"
        log(f"reading {path.name}")
        d = pd.read_parquet(path, columns=cols)
        d["DataDate"] = pd.to_datetime(d["DataDate"]).dt.normalize()
        d["ExpirationDate"] = pd.to_datetime(d["ExpirationDate"]).dt.normalize()
        d = d.merge(kk, on=["Symbol", "DataDate", "ExpirationDate"], how="inner")
        d["PutCall"] = d["PutCall"].astype(str).str.lower().str.strip()
        d["mid"] = (pd.to_numeric(d["BidPrice"], errors="coerce") + pd.to_numeric(d["AskPrice"], errors="coerce")) / 2
        d["iv"] = pd.to_numeric(d["ImpliedVolatility"], errors="coerce")
        d["ad"] = pd.to_numeric(d["Delta"], errors="coerce").abs()
        d["S"] = pd.to_numeric(d["UnderlyingPrice"], errors="coerce")
        d = d[(d["BidPrice"] > 0) & (d["AskPrice"] > d["BidPrice"]) & (d["mid"] > 0) & (d["iv"] > 0.03) & (d["iv"] < 3.0) & (d["S"] > 0)].copy()
        if d.empty:
            continue
        g = ["Symbol", "DataDate", "ExpirationDate"]
        atm = d[d["ad"].between(0.40, 0.60)].groupby(g, sort=False)["iv"].median().rename("atm_iv").reset_index()
        p = d.pivot_table(index=g + ["StrikePrice"], columns="PutCall", values=["mid", "iv", "OpenInterest", "S", "DTE"], aggfunc="median")
        p.columns = [f"{a}_{b}" for a, b in p.columns]
        p = p.reset_index()
        if "mid_call" not in p or "mid_put" not in p:
            continue
        p = p.merge(atm, on=g, how="left")
        p["S"] = p[["S_call", "S_put"]].median(axis=1)
        p["DTE"] = p[["DTE_call", "DTE_put"]].median(axis=1)
        p["atm_iv"] = p["atm_iv"].fillna(p[["iv_call", "iv_put"]].median(axis=1))
        T = np.clip(p["DTE"].astype(float), 1, None) / 365.0
        em = p["atm_iv"].clip(0.03, 3.0) * np.sqrt(T)
        p["mny_z"] = np.log(p["StrikePrice"] / p["S"]) / em.replace(0, np.nan)
        p["fwd_ret"] = (p["StrikePrice"] + p["mid_call"] - p["mid_put"]) / p["S"] - 1.0
        p["fwd_z"] = p["fwd_ret"] / em.replace(0, np.nan)
        p["cp_premium_balance"] = (p["mid_call"] - p["mid_put"]) / (p["mid_call"] + p["mid_put"])
        p["oi_balance"] = (p["OpenInterest_call"].fillna(0) - p["OpenInterest_put"].fillna(0)) / (
            p["OpenInterest_call"].fillna(0) + p["OpenInterest_put"].fillna(0) + 1.0
        )
        near = p[p["mny_z"].abs() <= 1.25].copy()
        if near.empty:
            continue
        out = []
        for k, x in near.groupby(g, sort=False):
            if len(x) < 2:
                continue
            z = x["mny_z"].to_numpy(float)
            f = x["fwd_z"].to_numpy(float)
            ok = np.isfinite(z) & np.isfinite(f)
            if ok.sum() < 2:
                continue
            slope = float(np.polyfit(z[ok], f[ok], 1)[0])
            out.append(
                {
                    "ticker": k[0],
                    "entry_date": k[1],
                    "expiry_date": k[2],
                    "parity_fwd_z": float(np.nanmedian(f)),
                    "parity_fwd_slope": slope,
                    "parity_fwd_iqr": float(np.nanpercentile(f, 75) - np.nanpercentile(f, 25)),
                    "parity_balance": float(np.nanmedian(x["cp_premium_balance"])),
                    "parity_oi_balance": float(np.nanmedian(x["oi_balance"])),
                    "parity_pairs": int(len(x)),
                }
            )
        if out:
            rows.append(pd.DataFrame(out))
    if not rows:
        raise RuntimeError("No parity rows built")
    F = pd.concat(rows, ignore_index=True).sort_values(["ticker", "expiry_date", "entry_date"])
    F["entry_date"] = pd.to_datetime(F["entry_date"]).dt.normalize()
    F["expiry_date"] = pd.to_datetime(F["expiry_date"]).dt.normalize()
    for c in ["parity_fwd_z", "parity_fwd_slope", "parity_balance", "parity_oi_balance"]:
        F[f"{c}_chg"] = F.groupby(["ticker", "expiry_date"], sort=False)[c].diff()
    F.to_parquet(CACHE, index=False)
    log(f"wrote {CACHE} rows={len(F):,}")
    return F


def choose_sign(train: pd.DataFrame, col: str) -> float:
    h = train[[col, "fut_ret"]].dropna()
    if len(h) < 500 or h[col].nunique() < 2:
        return 0.0
    c = spearman_arrays(h[col].to_numpy(float), h["fut_ret"].to_numpy(float))
    return 0.0 if not np.isfinite(c) or abs(c) < 0.002 else (1.0 if c > 0 else -1.0)


def add_wf(D: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    D = D.copy()
    for c in cols:
        D[f"{c}_wf"] = np.nan
    for y in sorted(D["year"].dropna().unique()):
        train = D[D["year"] < y]
        m = D["year"].to_numpy() == y
        for c in cols:
            s = choose_sign(train, c)
            D.loc[m, f"{c}_wf"] = s * D.loc[m, c].to_numpy(float)
    return D


def book(sel: pd.DataFrame) -> dict[str, float]:
    return selected_book(sel)


def run() -> None:
    F = build_cache(force="--force" in sys.argv)
    C = pd.read_parquet(FRAME)
    C = add_canon_scores(C, use_gap=True)
    C["entry_date"] = pd.to_datetime(C["entry_date"]).dt.normalize()
    C["expiry_date"] = pd.to_datetime(C["expiry_date"]).dt.normalize()
    C = C.merge(F, on=["ticker", "entry_date", "expiry_date"], how="left")
    C["fut_ret"] = C["expiry_close"] / C["entry_price"] - 1.0
    C["year"] = C["entry_date"].dt.year
    cols = [
        "parity_fwd_z",
        "parity_fwd_slope",
        "parity_fwd_iqr",
        "parity_balance",
        "parity_oi_balance",
        "parity_fwd_z_chg",
        "parity_fwd_slope_chg",
        "parity_balance_chg",
    ]
    C = add_wf(C, cols)
    rows = []
    books = []
    base = C[C["GROUND"] >= ec.THR].sort_values(["entry_date", "GROUND"], ascending=[True, False]).groupby("entry_date").head(ec.TOP_N)
    b = book(base)
    b.update({"signal": "BASE", "rule": "canon"})
    books.append(b)
    for c in cols:
        wc = f"{c}_wf"
        for y, g in C.groupby("year", sort=True):
            h = g[[wc, "fut_ret"]].dropna()
            rows.append({"signal": c, "year": int(y), "coverage": float(g[c].notna().mean()), "spearman": spearman_arrays(h[wc].to_numpy(float), h["fut_ret"].to_numpy(float)) if len(h) else np.nan})
        R = C.dropna(subset=["GROUND"]).copy()
        R["pct"] = R.groupby("year", sort=False)[wc].rank(pct=True).fillna(0.5)
        for mult in [0.05, 0.10, 0.20, 0.50]:
            R["score"] = R["GROUND"] * (1.0 + mult * (R["pct"] - 0.5))
            sel = R[R["GROUND"] >= ec.THR].sort_values(["entry_date", "score"], ascending=[True, False]).groupby("entry_date").head(ec.TOP_N)
            bb = book(sel)
            bb.update({"signal": c, "rule": f"tilt{mult}"})
            books.append(bb)
        for cut in [0.05, 0.10, 0.15, 0.20, 0.30]:
            sel = R[(R["GROUND"] >= ec.THR) & (R["pct"] >= cut)].sort_values(["entry_date", "GROUND"], ascending=[True, False]).groupby("entry_date").head(ec.TOP_N)
            bb = book(sel)
            bb.update({"signal": c, "rule": f"veto{cut}"})
            books.append(bb)
    S = pd.DataFrame(rows)
    B = pd.DataFrame(books).sort_values("pnl", ascending=False)
    S.to_csv(SUMMARY, index=False)
    B.to_csv(BOOKS, index=False)
    lines = [
        "parity forward signal",
        f"feature rows: {len(F):,}",
        "",
        "Top books",
        B.head(30).to_string(index=False, float_format=lambda x: f"{x:.4f}"),
        "",
        "Year diagnostics",
        S.to_string(index=False, float_format=lambda x: f"{x:.4f}"),
    ]
    REPORT.write_text("\n".join(lines) + "\n")
    log("\n".join(lines))


if __name__ == "__main__":
    run()
