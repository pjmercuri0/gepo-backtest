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


REGIME = "below_100"   # symmetric on the 100d SMA (user 2026-09-19)
PARITY = 0.25
PARITY_GATE = False    # 2026-09-29 (user): bear parity veto OFF (§0.67 bear ablation: no effect on either window)
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
        "bull puts above prior-session SPY 100d SMA; bear calls below it (symmetric on the 100d)"
        if ec.BULL_REGIME_GATE else
        "bull puts every day; bear calls only below the prior-session SPY 100d SMA (2026-09-29)"
    )
    c["parity"] = (("bull parity > canon threshold; " if ec.BULL_PARITY_GATE else "no bull parity veto; ")
                   + ("bear mirrored parity > 25th percentile" if PARITY_GATE else "no bear parity veto (both off 2026-09-29)"))
    c["bear_gates"] = (
        "bear calls: GROUND >= 0.001, max 5/day. Earnings and ex-dividend gates "
        "(ex-date through expiry+1) apply to BOTH sleeves as of 2026-09-19."
    )
    c["selection"] = (
        "research bear sleeve selected on IS 2020-2025; 2026 attached after ranking"
        + (" (OOT view)" if oot else "")
    )
    # report_mid_canon.build_payload() writes the OLD canon's captions (0.80xmid
    # fill, G_rv / rv_vs_iv scoring).  realize() actually books ec.FILL_MULT x
    # model_credit minus ec.COMMISSION on D_ent scoring, so restate them here or
    # the dashboard mislabels its own numbers (caught 2026-09-19).
    c["fill_basis"] = (
        f"{ec.FILL_MULT:.2f}\u00d7 smile-fit model credit; partial-WIN at 50% intrinsic"
        + ("; no commission" if ec.COMMISSION == 0 else f"; ${ec.COMMISSION:.2f} commission")
    )
    c["window"] = ec.CANON_LABELS["window"]
    c["selection"] = (f"bull puts: GROUND >= {ec.THR:g}, " + (f"parity > {ec.PARITY_MIN_PCT:.0%}, " if ec.BULL_PARITY_GATE else "no parity veto, ")
                      + f"top-{ec.TOP_N}/day; "
                      + c["selection"])
    c["scoring"] = ec.CANON_LABELS["scoring"]
    c["dkl"] = ec.CANON_LABELS["dkl"]
    c["gap"] = ec.CANON_LABELS["gap"]
    c["commission"] = "none (2026-09-17)" if ec.COMMISSION == 0 else f"${ec.COMMISSION:.2f}/spread"
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

    bull_mask = c.spread_type.eq("bull_put") & ~c.exdiv_hit & ~c.earnings_hit & (c.GROUND >= ec.THR)
    if ec.BULL_REGIME_GATE:      # OFF since 2026-09-29 (§0.66/§0.67): bull puts every day
        bull_mask &= prior_spy_bull(c.entry_date, spy, 100)
    if ec.BULL_PARITY_GATE:      # OFF since 2026-09-29
        bull_mask &= c.parity_pct > ec.PARITY_MIN_PCT
    bull_pool = c[bull_mask]
    bear_mask = c.spread_type.eq("bear_call") & ~c.exdiv_hit & ~c.earnings_hit & c[REGIME] & (c.GROUND >= GROUND)
    if PARITY_GATE:              # OFF since 2026-09-29
        bear_mask &= c.bear_parity_pct > PARITY
    bear_pool = c[bear_mask]
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
