"""Restate every model-booked History and Snap row at the 1.00x booking (user 2026-10-17). Rows with no
model credit (before 2026-09-14) and rows with his typed fill keep the P&L they settled at.

History (live/frozen/*.json): each settled pick is re-booked at live.credit_basis.entry_credit -- the
actual fill where he typed one, else ent_canon.FILL_MULT x model -- and re-settled with spreads.settle_pnl at its RECORDED expiry close. Day totals are
recomputed. Settled History days used to be booked at 0.80 x the quoted mid regardless of the canon.

Snap (live/intraday_picks/*.json): entry_credit = FILL_MULT x model; settled
picks re-settled at their recorded expiry_close; open picks' tracked entry credit and unrealized P&L
moved to the new entry.

Writes in place. Usage: restate_1x_booking_20261017.py [--dry]
"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
import ent_canon as ec, spreads
from live import credit_basis, live_config
from live import snapshot_picks as sp
DRY = "--dry" in sys.argv

def _pnl(close, p, credit, width):
    return round(float(spreads.settle_pnl(float(close), float(p["short_strike"]), float(p["long_strike"]),
                                          credit, width - credit, p["spread_type"])) * 100, 2)

# ── History ──
h_days = h_rows = 0; h_before = h_after = 0.0
for fp in sorted(Path(live_config.FROZEN_DIR).glob("*.json")):
    d = json.loads(fp.read_text())
    out = d.get("outcome") or {}
    res = out.get("results") or {}
    if d.get("mock") or not res:
        continue
    rbp = out.get("results_by_pick")
    total = total_act = 0.0; any_act = False
    for i, p in enumerate(d.get("top_picks") or []):
        r = rbp[i] if rbp and len(rbp) > i else res.get(p.get("ticker"))
        if not r or r.get("underlying_price") is None or r.get("pnl_per_contract") is None:
            continue
        # Only rows booked on the model move (user 2026-10-17, option 2): picks with no model credit and
        # rows carrying his typed fill keep the P&L they settled at.
        if not p.get("model_credit") or p.get("actual_credit") is not None:
            total += float(r["pnl_per_contract"])
            total_act += float(r["actual_pnl_per_contract"] if r.get("actual_pnl_per_contract") is not None else r["pnl_per_contract"])
            any_act = any_act or r.get("actual_pnl_per_contract") is not None
            continue
        width = credit_basis.spread_width(p)
        c = min(credit_basis.entry_credit(p), round(width, 4))
        new = _pnl(r["underlying_price"], p, c, width)
        h_before += float(r["pnl_per_contract"]); h_after += new; h_rows += 1
        r["pnl_per_contract"] = new; r["pnl_per_share"] = round(new / 100, 4)
        total += new
        if r.get("actual_pnl_per_contract") is not None:
            total_act += float(r["actual_pnl_per_contract"]); any_act = True
        else:
            total_act += new
    out["total_pnl_per_contract"] = round(total, 2)
    if any_act or "total_pnl_per_contract_actual" in out:
        out["total_pnl_per_contract_actual"] = round(total_act, 2)
    out["restated_1x_at"] = "2026-10-17"
    h_days += 1
    if not DRY:
        fp.write_text(json.dumps(d, indent=2))
print(f"History: {h_days} settled days, {h_rows} rows, P&L/ctr ${h_before:+,.2f} -> ${h_after:+,.2f}")

# ── Snap ──
s_rows = s_settled = 0; s_before = s_after = 0.0
for fp in sorted(sp.PICKS_DIR.glob("*.json")):
    d = json.loads(fp.read_text())
    for scan in d.get("scans") or []:
        for p in scan.get("picks") or []:
            mc = p.get("model_credit")
            if not mc:
                continue          # no model credit: keeps its booking (user 2026-10-17, option 2)
            ent, basis = round(float(mc) * ec.FILL_MULT, 4), f"{ec.FILL_MULT:g}xMODEL"
            width = float(p["net_credit"]) + float(p["max_loss"])
            ent = min(ent, round(width, 4))
            p["entry_credit"], p["entry_basis"] = ent, basis
            s_rows += 1
            if p.get("expiry_close") is not None and p.get("pnl") is not None:
                new = _pnl(p["expiry_close"], p, ent, width)
                s_before += float(p["pnl"]); s_after += new; s_settled += 1
                p["pnl"] = new
            lv = p.get("live")
            if isinstance(lv, dict) and lv.get("entry_credit") is not None:
                lv["entry_credit"] = ent; lv["max_loss"] = round(width - ent, 4)
                if lv.get("current_mark") is not None:
                    lv["unrealized_pnl_per_contract"] = round((ent - float(lv["current_mark"])) * 100, 2)
    if not DRY:
        fp.write_text(json.dumps(d, indent=1))
print(f"Snap: {s_rows} picks, {s_settled} settled, P&L/ctr ${s_before:+,.2f} -> ${s_after:+,.2f}")
