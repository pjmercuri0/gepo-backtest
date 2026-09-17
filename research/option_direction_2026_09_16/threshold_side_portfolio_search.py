"""Threshold portfolio search: up to five bull puts and five bear calls per day.

Unlike the production-style top-five selector, each side is filtered and capped
independently. Option scores are ranked cross-sectionally on the entry date, so
the percentile uses only information available that day.
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
OUT = HERE / "threshold_side_portfolio_search.csv"


def metrics(x: pd.DataFrame) -> dict[str, float]:
    daily = x.groupby("entry_date")["pnl"].sum()
    eq = 10_000.0 + daily.cumsum()
    dd = float((eq / eq.cummax() - 1.0).min() * 100.0)
    days = max(int(x["entry_date"].nunique()), 1)
    cagr = float(((eq.iloc[-1] / 10_000.0) ** (252.0 / days) - 1.0) * 100.0)
    sh = float(daily.mean() / daily.std(ddof=1) * math.sqrt(252.0))
    return {
        "n": float(len(x)),
        "pnl": float(x["pnl"].sum()),
        "full_win": float((x["_outcome"] == "WIN").mean() * 100.0),
        "profitable": float((x["pnl"] > 0).mean() * 100.0),
        "yield_pct": float(100.0 * x["pnl"].sum() / (x["max_loss_adj"] * 100.0).sum()),
        "weekly_sharpe": sh,
        "max_dd": dd,
        "calmar": cagr / abs(dd),
    }


def select(d: pd.DataFrame, ground: float, bull_cut: float, bear_cut: float) -> pd.DataFrame:
    z = d[d["GROUND"] >= ground]
    bp = z[(z["spread_type"] == "bull_put") & (z["bull_score"] >= bull_cut)]
    bc = z[(z["spread_type"] == "bear_call") & (z["bear_score"] >= bear_cut)]
    parts = []
    for x in (bp, bc):
        parts.append(
            x.sort_values(["entry_date", "GROUND"], ascending=[True, False])
            .groupby("entry_date", sort=False).head(5)
        )
    return pd.concat(parts, ignore_index=False)


def main() -> None:
    d = pd.read_parquet(FRAME)
    d["entry_date"] = pd.to_datetime(d["entry_date"]).dt.normalize()
    d["year"] = d["entry_date"].dt.year
    d["credit"] = (d["model_credit"] * ec.FILL_MULT).round(4)
    d["max_loss_adj"] = (d["width"] - d["credit"]).round(4)
    d = d[d["max_loss_adj"] > 0].copy()
    d["_outcome"] = d.apply(outcome, axis=1)
    d["pnl"] = d.apply(pnl, axis=1)
    for c in ("sig_gex_net_signed", "sig_same_strike_cp_bull_signed"):
        d[c + "_r"] = d.groupby("entry_date")[c].rank(pct=True)
    d["bull_score"] = d[["sig_gex_net_signed_r", "sig_same_strike_cp_bull_signed_r"]].mean(axis=1)
    d["bear_score"] = 1.0 - d["bull_score"]

    rows = []
    for ground in (0.005, 0.0075, 0.01, 0.0125, 0.015, 0.02, 0.03):
        for bull_cut in np.arange(0.0, 0.51, 0.05):
            for bear_cut in np.arange(0.0, 0.51, 0.05):
                x = select(d, ground, float(bull_cut), float(bear_cut))
                i = metrics(x[x["year"] <= 2025])
                o = metrics(x[x["year"] == 2026])
                row = {"ground": ground, "bull_cut": bull_cut, "bear_cut": bear_cut}
                row.update({"is_" + k: v for k, v in i.items()})
                row.update({"oot_" + k: v for k, v in o.items()})
                rows.append(row)
    pd.DataFrame(rows).to_csv(OUT, index=False)
    print(f"wrote {OUT} rows={len(rows):,}")


if __name__ == "__main__":
    main()
