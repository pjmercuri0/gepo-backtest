"""Build the Backtest and OOT dashboard payloads with the research bear sleeve."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ent_canon as ec
import report_mid_canon as rmc
from bear_regime_sweep import (
    add_earnings_gate,
    add_exdiv_gate,
    add_regimes,
    prepare,
    realize,
)
from sma_bull_regime_sweep import prior_spy_bull


REGIME = "below_100_and_below_20"
PARITY = 0.25
GROUND = 0.001
CAP = 5


def enrich(picks: pd.DataFrame) -> pd.DataFrame:
    """Add the fields consumed by report_mid_canon.build_payload."""
    out = picks.copy()
    b = out.model_credit.to_numpy(float) / (
        out.width.to_numpy(float) - out.model_credit.to_numpy(float)
    )
    w, g = ec.kelly(
        np.nan_to_num(out.p.to_numpy(float)),
        np.nan_to_num(out.q.to_numpy(float)),
        np.nan_to_num(out.ro.to_numpy(float)),
        b,
    )
    out["w_star"] = w
    out["G"] = g
    out["DKL"] = out.D_ent
    out["realize_date"] = out.expiry_date
    out["entry_date_dt"] = out.entry_date
    out["short_delta"] = np.where(
        out.spread_type.eq("bull_put"), -out.dfit_short, out.dfit_short
    )
    return out.sort_values("entry_date_dt").reset_index(drop=True)


def patch_config(payload: dict, oot: bool) -> dict:
    c = payload["config"]
    c["regime"] = (
        "bull puts above prior-session SPY 100d SMA; bear calls below prior-session "
        "SPY 100d and 20d SMA"
    )
    c["parity"] = "bull parity > canon threshold; bear mirrored parity > 25th percentile"
    c["bear_gates"] = (
        "bear calls: GROUND >= 0.001, max 5/day, earnings and ex-dividend gated "
        "(ex-date through expiry+1)"
    )
    c["selection"] = (
        "research bear sleeve selected on IS 2020-2025; 2026 attached after ranking"
        + (" (OOT view)" if oot else "")
    )
    return payload


def write_payload(picks: pd.DataFrame, end_year: int, label: str, path: Path, oot: bool) -> None:
    payload = patch_config(rmc.build_payload(picks, end_year, label), oot)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {path}: {len(picks):,} trades")


def main() -> None:
    c, spy = prepare()
    c = add_regimes(c, spy)
    c = add_exdiv_gate(c)
    c = add_earnings_gate(c)

    bull_pool = c[
        c.spread_type.eq("bull_put")
        & prior_spy_bull(c.entry_date, spy, 100)
        & (c.parity_pct > ec.PARITY_MIN_PCT)
        & (c.GROUND >= ec.THR)
    ]
    bear_pool = c[
        c.spread_type.eq("bear_call")
        & ~c.exdiv_hit
        & ~c.earnings_hit
        & c[REGIME]
        & (c.bear_parity_pct > PARITY)
        & (c.GROUND >= GROUND)
    ]
    all_picks = pd.concat(
        [realize(bull_pool, ec.TOP_N), realize(bear_pool, CAP)],
        ignore_index=True,
    )
    all_picks = enrich(all_picks)
    write_payload(
        all_picks[all_picks.entry_date.dt.year <= 2025],
        2025,
        "backtest 2020-25 (bull + bear regime research sleeve)",
        ROOT / "live/data/backtest_equity.json",
        False,
    )
    write_payload(
        all_picks[all_picks.entry_date.dt.year == 2026],
        2026,
        "OOT 2026 (bull + bear regime research sleeve)",
        ROOT / "live/data/oot_equity.json",
        True,
    )


if __name__ == "__main__":
    main()
