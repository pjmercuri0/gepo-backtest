"""Strike-local option-book direction search for 1-4 DTE credit spreads.

This is research-only. It tests option features tied to the candidate's exact
short/long strikes instead of chain-level averages:

* same-strike and risk-side put/call OI walls;
* strike-local gamma exposure on safe vs risk side;
* OI-weighted dealer delta pressure near the short strike;
* pin/max-pain slope around the current underlying.

Every directional feature is evaluated side-aligned:
    bull_put wants bullish / support signals high;
    bear_call wants bearish / resistance signals high.

The harness requires a rule to improve both 2020-25 and 2026 splits across
P&L, full-win, profitable rate, yield, weekly Sharpe, and Calmar.
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
from direction_signal_suite import outcome, pnl

HERE = Path(__file__).resolve().parent
FRAME = HERE / "stack_frame_gap_on.parquet"
FEATURES = HERE / "strike_local_oi_gex_features.parquet"
SEARCH = HERE / "strike_local_oi_gex_search.csv"
BEST_METRICS = HERE / "strike_local_oi_gex_best_metrics.csv"


def log(msg: str) -> None:
    print(msg, flush=True)


def add_pnl_cols(D: pd.DataFrame) -> pd.DataFrame:
    D = D.copy()
    D["credit"] = (D["model_credit"] * ec.FILL_MULT).round(4)
    D["max_loss_adj"] = (D["width"] - D["credit"]).round(4)
    D = D[D["max_loss_adj"] > 0].copy()
    D["_outcome"] = D.apply(outcome, axis=1)
    D["pnl"] = D.apply(pnl, axis=1)
    return D


def metrics(sel: pd.DataFrame) -> dict[str, float]:
    x = add_pnl_cols(sel)
    if x.empty:
        return {
            "n": 0.0,
            "pnl": 0.0,
            "fw": math.nan,
            "prof": math.nan,
            "loss": math.nan,
            "yld": math.nan,
            "sh": math.nan,
            "dd": math.nan,
            "calmar": math.nan,
            "cagr": math.nan,
        }
    start = 10_000.0
    daily = x.groupby("entry_date", sort=True)["pnl"].sum()
    equity = start + daily.cumsum()
    dd = (equity / equity.cummax() - 1.0).min() * 100.0
    total_ret = equity.iloc[-1] / start - 1.0
    days = max(float(x["entry_date"].nunique()), 1.0)
    cagr = ((1.0 + total_ret) ** (252.0 / days) - 1.0) * 100.0
    dr = daily / start
    sh = float(dr.mean() / dr.std(ddof=1) * math.sqrt(252.0)) if len(dr) > 2 and dr.std(ddof=1) > 0 else math.nan
    calmar = float(cagr / abs(dd)) if dd < 0 else math.nan
    wagered = float((x["max_loss_adj"] * 100.0).sum())
    return {
        "n": float(len(x)),
        "pnl": float(x["pnl"].sum()),
        "fw": float((x["_outcome"] == "WIN").mean()),
        "prof": float((x["pnl"] > 0).mean()),
        "loss": float((x["_outcome"] == "LOSS").mean()),
        "yld": float(100.0 * x["pnl"].sum() / wagered) if wagered > 0 else math.nan,
        "sh": sh,
        "dd": float(dd),
        "calmar": calmar,
        "cagr": float(cagr),
    }


def select_base(D: pd.DataFrame, period: str) -> pd.DataFrame:
    if period == "is":
        Z = D[D["year"].between(2020, 2025)]
    elif period == "oot":
        Z = D[D["year"].eq(2026)]
    else:
        Z = D
    return (
        Z[Z["GROUND"] >= ec.THR]
        .sort_values(["entry_date", "GROUND"], ascending=[True, False])
        .groupby("entry_date", sort=False)
        .head(ec.TOP_N)
    )


def select_rule(D: pd.DataFrame, score_col: str, rule: str, period: str) -> pd.DataFrame:
    if period == "is":
        Z = D[D["year"].between(2020, 2025)]
    elif period == "oot":
        Z = D[D["year"].eq(2026)]
    else:
        Z = D
    R = Z.dropna(subset=["GROUND"]).copy()
    R["pct"] = R.groupby("year", sort=False)[score_col].rank(pct=True).fillna(0.5)
    if rule.startswith("veto"):
        cut = float(rule.replace("veto", ""))
        pool = R[(R["GROUND"] >= ec.THR) & (R["pct"] >= cut)]
        key = "GROUND"
    else:
        mult = float(rule.replace("tilt", ""))
        R["score"] = R["GROUND"] * (1.0 + mult * (R["pct"] - 0.5))
        pool = R[R["GROUND"] >= ec.THR]
        key = "score"
    return pool.sort_values(["entry_date", key], ascending=[True, False]).groupby("entry_date", sort=False).head(ec.TOP_N)


def build_features(force: bool = False) -> pd.DataFrame:
    if FEATURES.exists() and not force:
        log(f"loading {FEATURES}")
        return pd.read_parquet(FEATURES)

    C = pd.read_parquet(FRAME)
    need = C[["ticker", "entry_date", "expiry_date", "entry_price", "short_strike", "long_strike", "spread_type", "DTE"]].copy()
    need["entry_date"] = pd.to_datetime(need["entry_date"]).dt.normalize()
    need["expiry_date"] = pd.to_datetime(need["expiry_date"]).dt.normalize()
    rows: list[dict] = []
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
        "Theta",
        "BidPrice",
        "AskPrice",
    ]
    for year in range(2020, 2027):
        cand = need[need["entry_date"].dt.year.eq(year)].copy()
        if cand.empty:
            continue
        path = ROOT / ec.vendor_year_parquet(year)
        log(f"reading {path.name}")
        ch = pd.read_parquet(path, columns=cols)
        ch["DataDate"] = pd.to_datetime(ch["DataDate"]).dt.normalize()
        ch["ExpirationDate"] = pd.to_datetime(ch["ExpirationDate"]).dt.normalize()
        ch = ch.rename(columns={"Symbol": "ticker", "DataDate": "entry_date", "ExpirationDate": "expiry_date"})
        ch["PutCall"] = ch["PutCall"].astype(str).str.lower().str.strip()
        for c in ["StrikePrice", "OpenInterest", "UnderlyingPrice", "ImpliedVolatility", "Delta", "Gamma", "Theta", "BidPrice", "AskPrice"]:
            ch[c] = pd.to_numeric(ch[c], errors="coerce")
        keys = cand[["ticker", "entry_date", "expiry_date"]].drop_duplicates()
        ch = ch.merge(keys, on=["ticker", "entry_date", "expiry_date"], how="inner")
        ch = ch[(ch["AskPrice"] > ch["BidPrice"]) & (ch["OpenInterest"].fillna(0) >= 0) & ch["UnderlyingPrice"].gt(0)].copy()
        ch["oi"] = ch["OpenInterest"].fillna(0.0)
        ch["gex"] = ch["Gamma"].fillna(0.0) * ch["oi"] * np.where(ch["PutCall"].eq("call"), 1.0, -1.0)
        ch["dex"] = ch["Delta"].fillna(0.0) * ch["oi"]
        ch["mid"] = (ch["BidPrice"] + ch["AskPrice"]) / 2.0
        by = {k: g.sort_values("StrikePrice") for k, g in ch.groupby(["ticker", "entry_date", "expiry_date"], sort=False)}
        for r in cand.itertuples(index=True):
            g = by.get((r.ticker, r.entry_date, r.expiry_date))
            if g is None or g.empty:
                continue
            S = float(r.entry_price)
            ss = float(r.short_strike)
            width = abs(float(r.short_strike) - float(r.long_strike))
            em = np.nanmedian(g["ImpliedVolatility"]) * math.sqrt(max(float(r.DTE), 1.0) / 365.0) * S
            band = max(width * 2.0, em if np.isfinite(em) and em > 0 else width * 2.0)
            near = g[(g["StrikePrice"] >= ss - band) & (g["StrikePrice"] <= ss + band)].copy()
            if near.empty:
                continue
            pc = near.pivot_table(index="StrikePrice", columns="PutCall", values="oi", aggfunc="sum").fillna(0.0)
            for col in ("call", "put"):
                if col not in pc:
                    pc[col] = 0.0
            same = pc.iloc[(pc.index.to_numpy(float) - ss).__abs__().argmin()]
            below = near[near["StrikePrice"] <= ss]
            above = near[near["StrikePrice"] >= ss]
            risk_put = below[below["PutCall"].eq("put")]
            risk_call = above[above["PutCall"].eq("call")]
            safe_call = below[below["PutCall"].eq("call")]
            safe_put = above[above["PutCall"].eq("put")]
            is_put_spread = r.spread_type == "bull_put"
            # bullish-positive raw signals:
            put_wall_below = float(np.log1p(risk_put["oi"].sum()))
            call_wall_above = float(np.log1p(risk_call["oi"].sum()))
            same_pc = float(np.log1p(same.get("put", 0.0)) - np.log1p(same.get("call", 0.0)))
            gex_near = float(near["gex"].sum())
            dex_near = float(near["dex"].sum())
            # Side-aligned variants: high means favorable for the selected credit spread.
            side = 1.0 if is_put_spread else -1.0
            rows.append(
                {
                    "idx": int(r.Index),
                    "oi_wall_support_resist": put_wall_below if is_put_spread else call_wall_above,
                    "oi_wall_bull_raw": put_wall_below - call_wall_above,
                    "same_strike_oi_pc_bull": same_pc,
                    "local_gex_bull": gex_near / max(S, 1.0),
                    "local_dex_bull": dex_near / max(S, 1.0),
                    "risk_side_oi": float(np.log1p((risk_put if is_put_spread else risk_call)["oi"].sum())),
                    "safe_side_oi": float(np.log1p((safe_call if is_put_spread else safe_put)["oi"].sum())),
                    "risk_safe_oi_gap": float(np.log1p((risk_put if is_put_spread else risk_call)["oi"].sum()) - np.log1p((safe_call if is_put_spread else safe_put)["oi"].sum())),
                    "side_oi_wall": side * (put_wall_below - call_wall_above),
                    "side_same_oi_pc": side * same_pc,
                    "side_local_gex": side * gex_near / max(S, 1.0),
                    "side_local_dex": side * dex_near / max(S, 1.0),
                    "side_risk_safe_oi_gap": -float(np.log1p((risk_put if is_put_spread else risk_call)["oi"].sum()) - np.log1p((safe_call if is_put_spread else safe_put)["oi"].sum())),
                }
            )
    F = pd.DataFrame(rows).drop_duplicates("idx").set_index("idx").sort_index()
    F.to_parquet(FEATURES)
    log(f"wrote {FEATURES} rows={len(F):,}")
    return F


def add_wf_signs(D: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    D = D.copy()
    for c in cols:
        D[f"{c}_wf"] = np.nan
    for y in sorted(D["year"].dropna().unique()):
        train = D[D["year"] < y]
        m = D["year"].eq(y)
        for c in cols:
            h = train[[c, "pnl"]].dropna()
            if len(h) < 500 or h[c].nunique() < 3:
                sgn = 0.0
            else:
                corr = h[c].rank().corr(h["pnl"].rank())
                sgn = 0.0 if (not np.isfinite(corr) or abs(corr) < 0.002) else (1.0 if corr > 0 else -1.0)
            D.loc[m, f"{c}_wf"] = sgn * D.loc[m, c].to_numpy(float)
    return D


def main() -> None:
    force = "--force" in sys.argv
    C = pd.read_parquet(FRAME)
    C["entry_date"] = pd.to_datetime(C["entry_date"]).dt.normalize()
    C["expiry_date"] = pd.to_datetime(C["expiry_date"]).dt.normalize()
    C["year"] = C["entry_date"].dt.year
    C = add_pnl_cols(C)
    F = build_features(force=force)
    D = C.join(F, how="left")

    raw_cols = [
        "oi_wall_support_resist",
        "oi_wall_bull_raw",
        "same_strike_oi_pc_bull",
        "local_gex_bull",
        "local_dex_bull",
        "risk_side_oi",
        "safe_side_oi",
        "risk_safe_oi_gap",
        "side_oi_wall",
        "side_same_oi_pc",
        "side_local_gex",
        "side_local_dex",
        "side_risk_safe_oi_gap",
    ]
    D = add_wf_signs(D, raw_cols)
    # Consensus combos from the strike-local features.
    wf_cols = [f"{c}_wf" for c in raw_cols]
    for a in wf_cols:
        D[f"rank_{a}"] = D.groupby("year", sort=False)[a].rank(pct=True)
    combo_defs = {
        "combo_side_wall_gex": ["rank_side_oi_wall_wf", "rank_side_local_gex_wf"],
        "combo_side_wall_dex": ["rank_side_oi_wall_wf", "rank_side_local_dex_wf"],
        "combo_wall_same_gex": ["rank_side_oi_wall_wf", "rank_side_same_oi_pc_wf", "rank_side_local_gex_wf"],
        "combo_risk_safe_gex": ["rank_side_risk_safe_oi_gap_wf", "rank_side_local_gex_wf"],
        "combo_all_local_avg": [f"rank_{c}" for c in wf_cols],
        "combo_all_local_min": [f"rank_{c}" for c in wf_cols],
    }
    for out, cols in combo_defs.items():
        X = D[cols]
        D[out] = X.min(axis=1) if out.endswith("_min") else X.mean(axis=1)

    score_cols = wf_cols + list(combo_defs)
    base_is = metrics(select_base(D, "is"))
    base_oot = metrics(select_base(D, "oot"))
    rows = []
    rules = [f"veto{x:.2f}" for x in [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.12, 0.15, 0.18, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50]]
    rules += [f"tilt{x:.2f}" for x in [0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00]]
    for col in score_cols:
        if D[col].notna().mean() < 0.10:
            continue
        for rule in rules:
            mi = metrics(select_rule(D, col, rule, "is"))
            mo = metrics(select_rule(D, col, rule, "oot"))
            row = {"col": col, "rule": rule}
            for k, v in mi.items():
                row[f"is_{k}"] = v
                row[f"d_is_{k}"] = v - base_is[k] if np.isfinite(v) and np.isfinite(base_is[k]) else math.nan
            for k, v in mo.items():
                row[f"oot_{k}"] = v
                row[f"d_oot_{k}"] = v - base_oot[k] if np.isfinite(v) and np.isfinite(base_oot[k]) else math.nan
            rows.append(row)
    R = pd.DataFrame(rows)
    metrics_cols = ["pnl", "fw", "prof", "yld", "sh", "calmar"]
    keep = R[[f"d_is_{c}" for c in metrics_cols] + [f"d_oot_{c}" for c in metrics_cols]].gt(0).all(axis=1)
    R["all_pos"] = keep
    R["score"] = (
        R["d_is_pnl"] / 1000.0
        + R["d_oot_pnl"] / 500.0
        + 100.0 * (R["d_is_fw"] + R["d_oot_fw"] + R["d_is_prof"] + R["d_oot_prof"])
        + R["d_is_sh"]
        + R["d_oot_sh"]
        + 0.1 * (R["d_is_calmar"] + R["d_oot_calmar"])
    )
    R = R.sort_values(["all_pos", "score"], ascending=[False, False])
    R.to_csv(SEARCH, index=False)
    log(f"base_is {base_is}")
    log(f"base_oot {base_oot}")
    log(f"all-positive survivors {int(R['all_pos'].sum())}")
    log(R.head(40).to_string(index=False, float_format=lambda x: f"{x:.6f}"))

    if not R.empty:
        best = R.iloc[0]
        out = []
        for period in ["is", "oot", "all"]:
            b = metrics(select_base(D, period))
            s = metrics(select_rule(D, str(best["col"]), str(best["rule"]), period))
            b.update({"book": "BASE", "period": period})
            s.update({"book": f"{best['col']} {best['rule']}", "period": period})
            out += [b, s]
        pd.DataFrame(out).to_csv(BEST_METRICS, index=False)
        log(f"wrote {SEARCH}")
        log(f"wrote {BEST_METRICS}")


if __name__ == "__main__":
    main()
