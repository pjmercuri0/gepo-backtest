"""Re-emit the site's Backtest/OOT payloads from the committed picks (no vendor frame needed).

Runs report_mid_canon.build_payload on picks_C_252_k24.parquet and keeps each existing payload's
config block (captions) as is. Use when build_payload gains fields (2026-10-01: per-trade qty_q4 /
qty_h2 for the sizing toggle). Asserts the rebuilt summary equals the shipped one before writing.
"""
import sys, io, json, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT))
import report_mid_canon as rmc
HERE = Path(__file__).resolve().parent
P = pd.read_parquet(HERE / "picks_C_252_k24.parquet")
for lab, m, yr, name in (("IS", P.entry_date.dt.year <= 2025, 2025, "backtest_equity.json"), ("OOT", P.entry_date.dt.year == 2026, 2026, "oot_equity.json")):
    path = ROOT / "live/data" / name; old = json.loads(path.read_text())
    with contextlib.redirect_stdout(io.StringIO()):
        new = rmc.build_payload(P[m], yr, lab)
    # Keep the shipped summary, points and config; take only the trade/week tables (which now carry
    # qty_q4 / qty_h2). The finals must agree -- the calendar tail may not (the mini's SPY file can
    # run a few sessions past the machine that built the book).
    for k in ("n_trades", "strategy_final", "qty1_final", "quarterk_final", "halfk_final"):
        assert abs(float(new["summary"][k]) - float(old["summary"][k])) < 0.01, (lab, k, old["summary"][k], new["summary"][k])
    assert len(new["trades"]) == len(old["trades"])
    old["trades"] = new["trades"]; old["weeks"] = new["weeks"]; new = old
    path.write_text(json.dumps(new, indent=2))
    t = new["trades"]; print(f"{lab}: {len(t)} trades, finals identical; qty_q4 mean {sum(x['qty_q4'] for x in t)/len(t):.2f}, qty_h2 mean {sum(x['qty_h2'] for x in t)/len(t):.2f}")
