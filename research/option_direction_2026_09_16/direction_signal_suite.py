"""Bakeoff of short-horizon option-market direction signals.

This is a research harness only. It does not touch live ranking or production
payloads. The suite uses the current canon candidate universe, recomputes canon
P_real/GROUND, adds option-market signals, and evaluates them with walk-forward
sign fitting.

Historical limitations are explicit:
  * no historical option volume or quote size exists, so the two live-only ideas
    are represented as untested stubs in the final report;
  * the local earnings calendar only covers 2026, so the event signal is reported
    as unavailable for 2020-2025 validation.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import ent_canon as ec

HERE = Path(__file__).resolve().parent
FRAME = ROOT / "research/dkl_2026_09_13/featATM7.parquet"
GAP_SERIES = ROOT / "output/name_gaps_backtest.parquet"
CHAIN_CACHE = HERE / "chain_direction_features.parquet"
REPORT_TXT = HERE / "direction_signal_suite.txt"
SIGNAL_CSV = HERE / "direction_signal_summary.csv"
BOOK_CSV = HERE / "direction_signal_books.csv"


@dataclass(frozen=True)
class Signal:
    name: str
    column: str
    status: str = "backtested"
    note: str = ""


SIGNALS = [
    Signal("skew25_level", "sig_skew25_bull", note="-put-call 25d IV skew"),
    Signal("skew25_change", "sig_skew25_chg_bull", note="-daily change in 25d skew on same expiry"),
    Signal("same_strike_cp_iv", "sig_same_strike_cp_bull", note="-median same-strike put-call IV gap near ATM"),
    Signal("dealer_gex", "sig_gex_net", note="signed gamma*OI imbalance, sign fit walk-forward"),
    Signal("oi_pin", "sig_pin_bull", note="direction toward max-OI strike, normalized by ATM vol move"),
    Signal("oi_pressure_change", "sig_oi_pressure_chg", note="daily change in call OI minus put OI near 20-60 delta"),
    Signal("atm_term_inversion", "sig_atm_term", note="front ATM IV minus next-expiry ATM IV"),
    Signal("skew_term_structure", "sig_skew_term", note="front 25d skew minus next-expiry 25d skew"),
    Signal("smile_slope", "sig_smile_slope", note="quadratic smile c1 from canon fit"),
    Signal("iv_price_residual", "sig_iv_price_resid", note="ATM IV change residual after same-day stock return"),
    Signal("earnings_event_skew", "sig_earnings_event_skew", status="not_backtested", note="local earnings calendar lacks 2020-2025 coverage"),
    Signal("live_volume_flow", "sig_live_volume_flow", status="live_only", note="requires live option volume history, absent in backtest"),
    Signal("live_quote_size_imbalance", "sig_live_size_imbalance", status="live_only", note="requires live bid/ask sizes, absent in backtest"),
]


def log(msg: str) -> None:
    print(msg, flush=True)


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


def add_canon_scores(C: pd.DataFrame, use_gap: bool = True) -> pd.DataFrame:
    C = C.copy()
    C["entry_date"] = pd.to_datetime(C["entry_date"]).dt.normalize()
    C["expiry_date"] = pd.to_datetime(C["expiry_date"]).dt.normalize()
    closes = ec.backtest_closes()
    gaps = pd.read_parquet(GAP_SERIES)
    mu = ec.gap_drift(C, closes, gaps) if (use_gap and ec.GAP_GAMMA) else None
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


def _weighted_mean(x: pd.DataFrame, value: str, weight: str) -> float:
    w = pd.to_numeric(x[weight], errors="coerce").fillna(0).to_numpy(float)
    v = pd.to_numeric(x[value], errors="coerce").to_numpy(float)
    ok = np.isfinite(v) & np.isfinite(w) & (w > 0)
    if not ok.any():
        return float(np.nan)
    return float(np.average(v[ok], weights=w[ok]))


def _pin_row(x: pd.DataFrame) -> pd.Series:
    z = x.groupby("StrikePrice", sort=False)["OpenInterest"].sum()
    if z.empty or not np.isfinite(z.max()) or z.max() <= 0:
        return pd.Series({"pin_strike": np.nan, "pin_oi": np.nan})
    k = z.idxmax()
    return pd.Series({"pin_strike": float(k), "pin_oi": float(z.loc[k])})


def build_chain_cache(force: bool = False) -> pd.DataFrame:
    if CHAIN_CACHE.exists() and not force:
        log(f"loading {CHAIN_CACHE}")
        return pd.read_parquet(CHAIN_CACHE)

    keys = pd.read_parquet(FRAME, columns=["ticker", "entry_date"]).drop_duplicates()
    keys = keys.rename(columns={"ticker": "Symbol", "entry_date": "DataDate"})
    keys["DataDate"] = pd.to_datetime(keys["DataDate"]).dt.normalize()

    rows = []
    cols = [
        "Symbol",
        "DataDate",
        "ExpirationDate",
        "DTE",
        "PutCall",
        "StrikePrice",
        "OpenInterest",
        "UnderlyingPrice",
        "ImpliedVolatility",
        "Delta",
        "Gamma",
        "BidPrice",
        "AskPrice",
    ]
    for year in range(2020, 2027):
        path = ROOT / ec.vendor_year_parquet(year)
        kk = keys[keys["DataDate"].dt.year == year]
        if kk.empty:
            continue
        log(f"reading {path.name}")
        d = pd.read_parquet(path, columns=cols)
        d["DataDate"] = pd.to_datetime(d["DataDate"]).dt.normalize()
        d["ExpirationDate"] = pd.to_datetime(d["ExpirationDate"]).dt.normalize()
        d = d.merge(kk, on=["Symbol", "DataDate"], how="inner")
        d = d[pd.to_numeric(d["DTE"], errors="coerce").between(1, 45)]
        d["PutCall"] = d["PutCall"].astype(str).str.lower().str.strip()
        d["iv"] = pd.to_numeric(d["ImpliedVolatility"], errors="coerce")
        d["abs_delta"] = pd.to_numeric(d["Delta"], errors="coerce").abs()
        d["oi"] = pd.to_numeric(d["OpenInterest"], errors="coerce").fillna(0.0)
        d["gamma"] = pd.to_numeric(d["Gamma"], errors="coerce")
        d["S"] = pd.to_numeric(d["UnderlyingPrice"], errors="coerce")
        d = d[
            (d["iv"] > 0.03)
            & (d["iv"] < 3.0)
            & (d["AskPrice"] > d["BidPrice"])
            & (d["S"] > 0)
        ].copy()
        if d.empty:
            continue
        g = ["Symbol", "DataDate", "ExpirationDate"]

        base = d.groupby(g, sort=False).agg(
            DTE=("DTE", "first"),
            S=("S", "median"),
            atm_iv=("iv", lambda s: float(np.nanmedian(s))),
        ).reset_index()
        atm = d[d["abs_delta"].between(0.45, 0.55)].groupby(g, sort=False)["iv"].median().rename("atm50_iv")
        base = base.merge(atm.reset_index(), on=g, how="left")

        rr = d[d["abs_delta"].between(0.20, 0.30)].groupby(g + ["PutCall"], sort=False)["iv"].median().unstack()
        rr = rr.rename(columns={"put": "put25_iv", "call": "call25_iv"}).reset_index()
        base = base.merge(rr, on=g, how="left")
        base["skew25"] = base["put25_iv"] - base["call25_iv"]

        near = d[d["abs_delta"].between(0.35, 0.65)]
        piv = near.pivot_table(index=g + ["StrikePrice"], columns="PutCall", values="iv", aggfunc="median").reset_index()
        if "put" in piv and "call" in piv:
            piv["cp_iv_gap"] = piv["put"] - piv["call"]
            cp = piv.groupby(g, sort=False)["cp_iv_gap"].median().reset_index()
            base = base.merge(cp, on=g, how="left")
        else:
            base["cp_iv_gap"] = np.nan

        oi_band = d[d["abs_delta"].between(0.20, 0.60)]
        oi = oi_band.groupby(g + ["PutCall"], sort=False)["oi"].sum().unstack().rename(
            columns={"call": "call_oi_band", "put": "put_oi_band"}
        )
        base = base.merge(oi.reset_index(), on=g, how="left")

        d["gex_piece"] = d["gamma"] * d["oi"] * np.where(d["PutCall"].eq("call"), 1.0, -1.0)
        gex = d[d["abs_delta"].between(0.05, 0.95)].groupby(g, sort=False)["gex_piece"].sum().rename("gex_net")
        base = base.merge(gex.reset_index(), on=g, how="left")

        pins = d[d["oi"] > 0].groupby(g, sort=False).apply(_pin_row, include_groups=False).reset_index()
        base = base.merge(pins, on=g, how="left")
        rows.append(base)

    if not rows:
        raise RuntimeError("No chain feature rows built")
    F = pd.concat(rows, ignore_index=True)
    F = F.rename(columns={"Symbol": "ticker", "DataDate": "entry_date", "ExpirationDate": "expiry_date"})
    F["entry_date"] = pd.to_datetime(F["entry_date"]).dt.normalize()
    F["expiry_date"] = pd.to_datetime(F["expiry_date"]).dt.normalize()
    F = F.sort_values(["ticker", "expiry_date", "entry_date"]).reset_index(drop=True)
    for c in ("skew25", "call_oi_band", "put_oi_band", "gex_net", "atm50_iv"):
        F[f"{c}_chg"] = F.groupby(["ticker", "expiry_date"], sort=False)[c].diff()
    F["oi_pressure"] = F["call_oi_band"].fillna(0) - F["put_oi_band"].fillna(0)
    F["oi_pressure_chg"] = F.groupby(["ticker", "expiry_date"], sort=False)["oi_pressure"].diff()
    F.to_parquet(CHAIN_CACHE, index=False)
    log(f"wrote {CHAIN_CACHE} rows={len(F):,}")
    return F


def add_term_features(C: pd.DataFrame, F: pd.DataFrame) -> pd.DataFrame:
    C = C.merge(F, on=["ticker", "entry_date", "expiry_date"], how="left", suffixes=("", "_chain"))
    term_rows = []
    for _, g in F.sort_values(["ticker", "entry_date", "expiry_date"]).groupby(["ticker", "entry_date"], sort=False):
        exp = g["expiry_date"].to_numpy("datetime64[ns]")
        for r in g.itertuples(index=False):
            k = np.searchsorted(exp, np.datetime64(r.expiry_date), side="right")
            if k < len(g):
                nxt = g.iloc[k]
                term_rows.append(
                    {
                        "ticker": r.ticker,
                        "entry_date": r.entry_date,
                        "expiry_date": r.expiry_date,
                        "next_atm50_iv": nxt.atm50_iv,
                        "next_skew25": nxt.skew25,
                    }
                )
    if term_rows:
        T = pd.DataFrame(term_rows)
        C = C.merge(T, on=["ticker", "entry_date", "expiry_date"], how="left")
    else:
        C["next_atm50_iv"] = np.nan
        C["next_skew25"] = np.nan
    C["sig_atm_term"] = C["atm50_iv"] - C["next_atm50_iv"]
    C["sig_skew_term"] = C["skew25"] - C["next_skew25"]
    return C


def add_candidate_signals(C: pd.DataFrame) -> pd.DataFrame:
    C = C.copy()
    C["sig_skew25_bull"] = -C["skew25"]
    C["sig_skew25_chg_bull"] = -C["skew25_chg"]
    C["sig_same_strike_cp_bull"] = -C["cp_iv_gap"]
    C["sig_gex_net"] = C["gex_net"] / C["S"].replace(0, np.nan)
    emove = C["atm50_iv"] * np.sqrt(np.clip(C["DTE"], 1, None) / 365.0)
    C["sig_pin_bull"] = (C["pin_strike"] / C["entry_price"] - 1.0) / emove.replace(0, np.nan)
    C["sig_oi_pressure_chg"] = C["oi_pressure_chg"]
    C["sig_smile_slope"] = C["c1"]

    C = C.sort_values(["ticker", "entry_date", "expiry_date"]).reset_index(drop=True)
    C["sig0_chg"] = C.groupby(["ticker", "expiry_date"], sort=False)["sig0"].diff()
    C["stock_ret_1d"] = C.groupby("ticker", sort=False)["entry_price"].pct_change()
    tmp = C[["sig0_chg", "stock_ret_1d"]].dropna()
    if len(tmp) > 100:
        x = tmp["stock_ret_1d"].to_numpy(float)
        y = tmp["sig0_chg"].to_numpy(float)
        beta = float(np.dot(x - x.mean(), y - y.mean()) / np.dot(x - x.mean(), x - x.mean()))
    else:
        beta = 0.0
    C["sig_iv_price_resid"] = C["sig0_chg"] - beta * C["stock_ret_1d"]
    C["fut_ret"] = C["expiry_close"] / C["entry_price"] - 1.0
    C["year"] = C["entry_date"].dt.year
    return C


def spearman_arrays(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 10:
        return math.nan
    xr = pd.Series(x).rank(method="average").to_numpy(float)
    yr = pd.Series(y).rank(method="average").to_numpy(float)
    xr -= xr.mean()
    yr -= yr.mean()
    den = math.sqrt(float(np.dot(xr, xr) * np.dot(yr, yr)))
    return float(np.dot(xr, yr) / den) if den else math.nan


def daily_spearman(df: pd.DataFrame, sig: str) -> tuple[float, int]:
    vals = []
    for _, g in df.groupby("entry_date", sort=False):
        h = g[[sig, "fut_ret"]].dropna()
        if len(h) >= 10 and h[sig].nunique() > 1 and h["fut_ret"].nunique() > 1:
            vals.append(spearman_arrays(h[sig].to_numpy(float), h["fut_ret"].to_numpy(float)))
    return (float(np.nanmean(vals)), len(vals)) if vals else (math.nan, 0)


def choose_sign(train: pd.DataFrame, col: str) -> int:
    h = train[[col, "fut_ret"]].dropna()
    if len(h) < 250 or h[col].nunique() < 2:
        return 0
    c = spearman_arrays(h[col].to_numpy(float), h["fut_ret"].to_numpy(float))
    if not np.isfinite(c) or abs(c) < 0.002:
        return 0
    return 1 if c > 0 else -1


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


def evaluate_signal(C: pd.DataFrame, sig: Signal) -> tuple[list[dict], list[dict]]:
    rows = []
    books = []
    if sig.status != "backtested":
        rows.append({"signal": sig.name, "status": sig.status, "note": sig.note})
        return rows, books

    signed_col = f"{sig.column}_wf"
    need = [
        "entry_date",
        "year",
        "fut_ret",
        "GROUND",
        "model_credit",
        "width",
        "expiry_close",
        "short_strike",
        "long_strike",
        "spread_type",
        sig.column,
    ]
    D = C[need].copy()
    D[signed_col] = np.nan
    signs = {}
    for year in sorted(D["year"].dropna().unique()):
        sign = choose_sign(D[D["year"] < year], sig.column)
        signs[int(year)] = int(sign)
        m = D["year"].to_numpy() == year
        D.loc[m, signed_col] = sign * D.loc[m, sig.column].to_numpy(float)

    for year, g in D.groupby("year", sort=True):
        h = g[[sig.column, signed_col, "fut_ret"]].dropna(subset=[sig.column, "fut_ret"])
        raw = spearman_arrays(h[sig.column].to_numpy(float), h["fut_ret"].to_numpy(float)) if len(h) else math.nan
        hw = h.dropna(subset=[signed_col])
        wf = spearman_arrays(hw[signed_col].to_numpy(float), hw["fut_ret"].to_numpy(float)) if len(hw) else math.nan
        cov = float(g[sig.column].notna().mean())
        rows.append(
            {
                "signal": sig.name,
                "status": "backtested",
                "year": int(year),
                "coverage": cov,
                "raw_spearman": raw,
                "wf_spearman": wf,
                "wf_sign": signs.get(int(year), 0),
                "note": sig.note,
            }
        )

    R = D.dropna(subset=["GROUND"]).copy()
    pct = R.groupby("year", sort=False)[signed_col].rank(pct=True)
    R["sig_pct"] = pct.fillna(0.5)
    R["rank_plus_10"] = R["GROUND"] * (1.0 + 0.10 * (R["sig_pct"] - 0.5))
    base_pool = R[R["GROUND"] >= ec.THR]
    base_sel = base_pool.sort_values(["entry_date", "GROUND"], ascending=[True, False]).groupby("entry_date").head(ec.TOP_N)
    base_keys = set(base_sel.index)

    for label, pool, score in (
        ("tilt10", base_pool, "rank_plus_10"),
        ("veto_bottom20", R[(R["GROUND"] >= ec.THR) & (R["sig_pct"] >= 0.20)], "GROUND"),
    ):
        sel = pool.sort_values(["entry_date", score], ascending=[True, False]).groupby("entry_date").head(ec.TOP_N)
        b = selected_book(sel)
        b["signal"] = sig.name
        b["overlay"] = label
        b["changed_vs_base"] = float(1.0 - len(base_keys & set(sel.index)) / len(base_keys)) if base_keys else np.nan
        books.append(b)
    return rows, books


def main() -> None:
    force = "--force-chain" in sys.argv
    use_gap = "--no-gap" not in sys.argv
    suffix = "" if use_gap else "_nogap"
    report_txt = HERE / f"direction_signal_suite{suffix}.txt"
    signal_csv = HERE / f"direction_signal_summary{suffix}.csv"
    book_csv = HERE / f"direction_signal_books{suffix}.csv"
    F = build_chain_cache(force=force)
    C = pd.read_parquet(FRAME)
    C = add_canon_scores(C, use_gap=use_gap)
    C = add_term_features(C, F)
    C = add_candidate_signals(C)

    base_sel = (
        C[C["GROUND"] >= ec.THR]
        .sort_values(["entry_date", "GROUND"], ascending=[True, False])
        .groupby("entry_date")
        .head(ec.TOP_N)
    )
    base = selected_book(base_sel)
    base["signal"] = "BASE"
    base["overlay"] = "canon"
    base["changed_vs_base"] = 0.0

    summary_rows = []
    book_rows = [base]
    for sig in SIGNALS:
        r, b = evaluate_signal(C, sig)
        summary_rows.extend(r)
        book_rows.extend(b)

    S = pd.DataFrame(summary_rows)
    B = pd.DataFrame(book_rows)
    S.to_csv(signal_csv, index=False)
    B.to_csv(book_csv, index=False)

    ranked = (
        S[(S["status"] == "backtested") & (S["year"].between(2021, 2026))]
        .groupby("signal", sort=False)
        .agg(
            mean_wf_spearman=("wf_spearman", "mean"),
            min_coverage=("coverage", "min"),
            years=("year", "count"),
        )
        .reset_index()
        .sort_values("mean_wf_spearman", ascending=False)
    )
    books = B.sort_values("pnl", ascending=False)

    lines = []
    lines.append("direction signal suite")
    lines.append(f"own_gap_drift: {'on' if use_gap else 'off'}")
    lines.append(f"candidate rows: {len(C):,}")
    lines.append(f"chain feature rows: {len(F):,}")
    lines.append("")
    lines.append("Backtested signal rank, walk-forward sign, years 2021-2026")
    lines.append(ranked.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    lines.append("")
    lines.append("Selected-book overlays, sorted by total PnL")
    lines.append(books.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    lines.append("")
    lines.append("Year-level signal diagnostics")
    lines.append(S.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    report_txt.write_text("\n".join(lines) + "\n")
    log("\n".join(lines))
    log(f"wrote {report_txt}")


if __name__ == "__main__":
    main()
