"""Walk-forward stacks of option-direction signals.

This sits on top of direction_signal_suite.py and keeps the experiment separate
from the single-signal bakeoff. It tests simple ensembles:
  * equal rank-average of prior-year positive signals;
  * correlation-weighted rank blend;
  * veto stacks using combinations of weak-but-different signals.

No live/production ranking code is touched.
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
    HERE,
    FRAME,
    SIGNALS,
    add_candidate_signals,
    add_canon_scores,
    add_term_features,
    build_chain_cache,
    selected_book,
    spearman_arrays,
)


REPORT_TXT = HERE / "direction_signal_stack.txt"
BOOK_CSV = HERE / "direction_signal_stack_books.csv"
YEAR_CSV = HERE / "direction_signal_stack_years.csv"
REPORT_TXT_NOGAP = HERE / "direction_signal_stack_nogap.txt"
BOOK_CSV_NOGAP = HERE / "direction_signal_stack_books_nogap.csv"
YEAR_CSV_NOGAP = HERE / "direction_signal_stack_years_nogap.csv"

BACKTEST_COLS = [s.column for s in SIGNALS if s.status == "backtested"]
CORE_COLS = [
    "sig_gex_net",
    "sig_same_strike_cp_bull",
    "sig_pin_bull",
    "sig_iv_price_resid",
    "sig_smile_slope",
    "sig_oi_pressure_chg",
    "sig_skew_term",
]


def log(msg: str) -> None:
    print(msg, flush=True)


def prepare(use_gap: bool) -> pd.DataFrame:
    F = build_chain_cache(force=False)
    C = pd.read_parquet(FRAME)
    C = add_canon_scores(C, use_gap=use_gap)
    C = add_term_features(C, F)
    C = add_candidate_signals(C)
    return C


def choose_weight(train: pd.DataFrame, col: str) -> float:
    h = train[[col, "fut_ret"]].dropna()
    if len(h) < 500 or h[col].nunique() < 2:
        return 0.0
    c = spearman_arrays(h[col].to_numpy(float), h["fut_ret"].to_numpy(float))
    if not np.isfinite(c) or abs(c) < 0.002:
        return 0.0
    return float(c)


def pct_rank_by_year(D: pd.DataFrame, col: str) -> pd.Series:
    return D.groupby("year", sort=False)[col].rank(pct=True)


def add_walk_forward_stacks(C: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    D = C.copy()
    rows = []
    for col in BACKTEST_COLS:
        D[f"{col}_signed"] = np.nan
    D["stack_equal_all"] = np.nan
    D["stack_weighted_all"] = np.nan
    D["stack_equal_core"] = np.nan
    D["stack_weighted_core"] = np.nan
    D["veto_gex_cp_iv"] = np.nan
    D["veto_gex_ivresid"] = np.nan
    D["veto_gex_skewterm"] = np.nan
    D["veto_top4_bad"] = np.nan

    for year in sorted(D["year"].dropna().unique()):
        train = D[D["year"] < year]
        m = D["year"].to_numpy() == year
        weights = {col: choose_weight(train, col) for col in BACKTEST_COLS}
        for col, w in weights.items():
            sign = 0.0 if w == 0 else (1.0 if w > 0 else -1.0)
            D.loc[m, f"{col}_signed"] = sign * D.loc[m, col].to_numpy(float)
            rows.append({"year": int(year), "signal": col, "weight": w, "sign": sign})

        idx = D.index[m]
        all_signed = [f"{c}_signed" for c in BACKTEST_COLS]
        core_signed = [f"{c}_signed" for c in CORE_COLS]
        for cols, out, weighted in (
            (all_signed, "stack_equal_all", False),
            (all_signed, "stack_weighted_all", True),
            (core_signed, "stack_equal_core", False),
            (core_signed, "stack_weighted_core", True),
        ):
            parts = []
            ws = []
            for signed in cols:
                raw = signed.removesuffix("_signed")
                s = D.loc[idx, signed]
                if s.notna().mean() < 0.05:
                    continue
                r = s.rank(pct=True)
                w = abs(weights.get(raw, 0.0)) if weighted else 1.0
                if weighted and w == 0:
                    continue
                parts.append(r)
                ws.append(w)
            if parts:
                X = pd.concat(parts, axis=1)
                W = np.asarray(ws, dtype=float)
                if W.sum() <= 0:
                    D.loc[idx, out] = X.mean(axis=1)
                else:
                    D.loc[idx, out] = (X.fillna(0.5).to_numpy(float) @ W) / W.sum()

        # Veto scores are "badness" ranks: low score means at least one component is bottom-ranked.
        combos = {
            "veto_gex_cp_iv": ["sig_gex_net_signed", "sig_same_strike_cp_bull_signed"],
            "veto_gex_ivresid": ["sig_gex_net_signed", "sig_iv_price_resid_signed"],
            "veto_gex_skewterm": ["sig_gex_net_signed", "sig_skew_term_signed"],
            "veto_top4_bad": [
                "sig_gex_net_signed",
                "sig_same_strike_cp_bull_signed",
                "sig_pin_bull_signed",
                "sig_iv_price_resid_signed",
            ],
        }
        for out, cols in combos.items():
            ranks = []
            for col in cols:
                if col in D:
                    ranks.append(D.loc[idx, col].rank(pct=True))
            if ranks:
                D.loc[idx, out] = pd.concat(ranks, axis=1).min(axis=1)
    return D, pd.DataFrame(rows)


def changed_vs_base(base_idx: set, sel_idx: set) -> float:
    return float(1.0 - len(base_idx & sel_idx) / len(base_idx)) if base_idx else math.nan


def eval_books(D: pd.DataFrame) -> pd.DataFrame:
    rows = []
    base_pool = D[D["GROUND"] >= ec.THR]
    base_sel = base_pool.sort_values(["entry_date", "GROUND"], ascending=[True, False]).groupby("entry_date").head(ec.TOP_N)
    base_idx = set(base_sel.index)
    b = selected_book(base_sel)
    b.update({"stack": "BASE", "overlay": "canon", "changed_vs_base": 0.0})
    rows.append(b)

    stack_cols = [
        "stack_equal_all",
        "stack_weighted_all",
        "stack_equal_core",
        "stack_weighted_core",
        "veto_gex_cp_iv",
        "veto_gex_ivresid",
        "veto_gex_skewterm",
        "veto_top4_bad",
    ]
    for col in stack_cols:
        R = D.dropna(subset=["GROUND"]).copy()
        R["stack_pct"] = pct_rank_by_year(R, col).fillna(0.5)
        for mult in (0.05, 0.10, 0.20):
            R["score"] = R["GROUND"] * (1.0 + mult * (R["stack_pct"] - 0.5))
            sel = (
                R[R["GROUND"] >= ec.THR]
                .sort_values(["entry_date", "score"], ascending=[True, False])
                .groupby("entry_date")
                .head(ec.TOP_N)
            )
            rb = selected_book(sel)
            rb.update({"stack": col, "overlay": f"tilt{int(mult*100)}", "changed_vs_base": changed_vs_base(base_idx, set(sel.index))})
            rows.append(rb)
        for cut in (0.10, 0.20, 0.30, 0.40):
            sel = (
                R[(R["GROUND"] >= ec.THR) & (R["stack_pct"] >= cut)]
                .sort_values(["entry_date", "GROUND"], ascending=[True, False])
                .groupby("entry_date")
                .head(ec.TOP_N)
            )
            rb = selected_book(sel)
            rb.update({"stack": col, "overlay": f"veto_bottom{int(cut*100)}", "changed_vs_base": changed_vs_base(base_idx, set(sel.index))})
            rows.append(rb)
    return pd.DataFrame(rows)


def per_year_books(D: pd.DataFrame, best: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in best.itertuples(index=False):
        if r.stack == "BASE":
            continue
        R = D.dropna(subset=["GROUND"]).copy()
        R["stack_pct"] = pct_rank_by_year(R, r.stack).fillna(0.5)
        if str(r.overlay).startswith("tilt"):
            mult = float(str(r.overlay).replace("tilt", "")) / 100.0
            R["score"] = R["GROUND"] * (1.0 + mult * (R["stack_pct"] - 0.5))
            pool = R[R["GROUND"] >= ec.THR]
            score = "score"
        else:
            cut = float(str(r.overlay).replace("veto_bottom", "")) / 100.0
            pool = R[(R["GROUND"] >= ec.THR) & (R["stack_pct"] >= cut)]
            score = "GROUND"
        sel = pool.sort_values(["entry_date", score], ascending=[True, False]).groupby("entry_date").head(ec.TOP_N)
        for year, g in sel.groupby(sel["entry_date"].dt.year):
            b = selected_book(g)
            b.update({"stack": r.stack, "overlay": r.overlay, "year": int(year)})
            rows.append(b)
    return pd.DataFrame(rows)


def run(use_gap: bool) -> None:
    suffix = "" if use_gap else "_nogap"
    report = REPORT_TXT if use_gap else REPORT_TXT_NOGAP
    book_csv = BOOK_CSV if use_gap else BOOK_CSV_NOGAP
    year_csv = YEAR_CSV if use_gap else YEAR_CSV_NOGAP

    C = prepare(use_gap=use_gap)
    D, weights = add_walk_forward_stacks(C)
    B = eval_books(D).sort_values("pnl", ascending=False)
    Y = per_year_books(D, B.head(8))
    B.to_csv(book_csv, index=False)
    Y.to_csv(year_csv, index=False)

    lines = []
    lines.append("direction signal stack")
    lines.append(f"own_gap_drift: {'on' if use_gap else 'off'}")
    lines.append(f"candidate rows: {len(D):,}")
    lines.append("")
    lines.append("Top stack overlays")
    lines.append(B.head(25).to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    lines.append("")
    lines.append("Per-year books for top overlays")
    lines.append(Y.to_string(index=False, float_format=lambda x: f"{x:.4f}"))
    lines.append("")
    lines.append("Walk-forward weights")
    lines.append(weights.to_string(index=False, float_format=lambda x: f"{x:.5f}"))
    report.write_text("\n".join(lines) + "\n")
    log("\n".join(lines))
    log(f"wrote {report}")


def main() -> None:
    if "--both" in sys.argv:
        run(use_gap=True)
        run(use_gap=False)
    else:
        run(use_gap="--no-gap" not in sys.argv)


if __name__ == "__main__":
    main()
