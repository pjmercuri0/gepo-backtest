"""Site payload for the PLOT tab: History as today's canon would have picked it (user 2026-10-17).

Every archived IBKR snapshot day is re-ranked with live.ranker.rank_snapshot as the code stands now, on
History's schedule and with History's logic (user 2026-10-17: "then the plot should use that too"):
  quotes   combo first, as live: the combo quote that scan recorded (live/ranked/combo/<day>_<hhmm>.csv,
           every candidate, from 2026-10-17; before that only the ranked payload's rows), else leg mids
  schedule the 15:15 scan is the frozen day (15:01 on the half-hour days before 2026-09-02), topped up
           at 15:30 (15:31) and 15:45
  top-ups  fill the day to 6 by its own picks, one per ticker (freeze_snapshot._dedupe_key); with
           config.FABLE_TOPUP_BY_ACTUALS on, 6 minus the trades he held in Actuals by then
Gates as live: model credit >= 0.45 x width, quote above the min, GROUND >= config.FABLE_GROUND_MIN;
ranked by the margin of victory GROUND_M = M x exp(-k D_ent) (config.FABLE_RANK_KEY). Booked at ent_canon.FILL_MULT x model, settled at the expiry close.

The candidate frame is cached in canon_history_candidates.csv; pass --rebuild to re-rank the snapshots.
Writes live/data/ibkr_replay_equity.json.  Usage: build_payload_canon_history.py [--rebuild] [since=2026-08-20]
"""
import sys, io, json, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import os; os.chdir(ROOT)
import ent_canon as ec, report_mid_canon as rmc, report_bear_regime as rbr
import config as cfg
from spreads import settle_pnl
from live import live_config, ranker
from live.closes import load_closes
HERE = Path(__file__).resolve().parent
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
SINCE = ARGS[0] if ARGS else "2026-08-20"
REBUILD = "--rebuild" in sys.argv
FRAME = HERE / "canon_history_candidates.csv"
FILL = float(ec.FILL_MULT); K = 24.0; FLOOR = 0.45; TOP = 6
# History's schedule: (slot, snapshot files in preference order).
SLOTS = [("freeze", ("1515", "1501")), ("1530", ("1530", "1531")), ("1545", ("1545",))]
live_config.LIVE_COMBO_ENABLED = True
from live import combo_quotes
_CQ_COLS = ["combo_bid", "combo_ask", "combo_last", "combo_credit_mid", "combo_credit_touch", "combo_credit_last"]

def _recorded_quotes(day, hm):
    """{(ticker, short, long, expiry): row} of the combo quotes the live scan recorded."""
    rec = {}
    def walk(o):
        if isinstance(o, dict):
            if "ticker" in o and "short_strike" in o and "combo_bid" in o: yield o
            for v in o.values(): yield from walk(v)
        elif isinstance(o, list):
            for v in o: yield from walk(v)
    rf = ROOT / "live/ranked" / f"{day}_{hm}.json"
    if rf.exists():
        for r in walk(json.loads(rf.read_text())):
            rec[(r["ticker"], float(r["short_strike"]), float(r["long_strike"]), str(r["expiry_date"])[:10])] = {
                "combo_bid": r.get("combo_bid"), "combo_ask": r.get("combo_ask"), "combo_last": r.get("combo_last"),
                "combo_credit_mid": r.get("combo_credit_mid"), "combo_credit_touch": r.get("combo_credit_touch"),
                "combo_credit_last": r.get("combo_last")}
    cf = ROOT / "live/ranked/combo" / f"{day}_{hm}.csv"
    if cf.exists():
        for r in pd.read_csv(cf).to_dict("records"):
            rec[(r["ticker"], float(r["short_strike"]), float(r["long_strike"]), str(r["expiry_date"])[:10])] = r
    return rec

if REBUILD or not FRAME.exists():
    closes = load_closes(); closes["date"] = pd.to_datetime(closes.date).dt.strftime("%Y-%m-%d")
    px = closes.set_index(["ticker", "date"]).close
    rows = []
    for dd in sorted(p for p in (ROOT / "live/snapshots").iterdir() if p.is_dir() and p.name >= SINCE):
        for slot, files in SLOTS:
            f = next((dd / f"{hm}.parquet" for hm in files if (dd / f"{hm}.parquet").exists()), None)
            if f is None:
                continue
            rec = _recorded_quotes(dd.name, f.stem)
            def _stub(c, rec=rec):
                c = c.copy()
                for col in _CQ_COLS: c[col] = np.nan
                for i, r in c.iterrows():
                    x = rec.get((r.ticker, float(r.short_strike), float(r.long_strike), pd.Timestamp(r.expiry_date).strftime("%Y-%m-%d")))
                    if x:
                        for col in _CQ_COLS:
                            v = x.get(col)
                            c.at[i, col] = np.nan if v is None else v
                return c
            combo_quotes.attach_combo_quotes = _stub
            with contextlib.redirect_stdout(io.StringIO()):
                r = ranker.rank_snapshot(pd.read_parquet(f))
            if r is None or r.empty:
                print(dd.name, f.stem, "no candidates"); continue
            r = r.copy(); r["day"] = dd.name; r["slot"] = slot; r["hm"] = f.stem; rows.append(r)
            print(dd.name, f.stem, len(r), flush=True)
    R = pd.concat(rows, ignore_index=True)
    R["exp"] = pd.to_datetime(R.expiry_date).dt.strftime("%Y-%m-%d")
    R["width"] = (R.short_strike.astype(float) - R.long_strike.astype(float)).abs()
    R["model_cw"] = R.model_credit / R.width
    R["G24"] = R.EV * np.exp(-K * R.D_ent)
    R["settle"] = [px.get((t, e), np.nan) for t, e in zip(R.ticker, R.exp)]
    cols = ["day", "slot", "hm", "ticker", "spread_type", "short_strike", "long_strike", "width", "exp", "DTE", "entry_price",
            "net_credit", "model_credit", "model_cw", "IV", "p", "q", "ro", "G", "EV", "D_ent", "GROUND", "G24", "M", "GROUND_M", "settle",
            "above_min", "qualified", "credit_source"]
    R[cols].to_csv(FRAME, index=False)
    print(f"{len(R)} rows, {R.day.nunique()} days, {R.settle.isna().sum()} unsettled -> {FRAME.name}")

R = pd.read_csv(FRAME, dtype={"hm": str})
R = R[(R.day >= SINCE) & R.settle.notna() & (R.model_credit > 0) & R.net_credit.notna() & R.p.notna()].copy()
E = R[(R.model_cw >= FLOOR) & R.above_min.astype(bool)].copy()
GMIN = getattr(cfg, "FABLE_GROUND_MIN", None)
if GMIN is not None:
    E = E[E.G24 >= float(GMIN)].copy()
# 2026-10-21 (user, new canon): rank on the margin of victory GROUND_M, as live; GROUND (G24) stays the gate.
E = E.sort_values(["day", "slot", "GROUND_M"], ascending=[True, True, False], na_position="last")
# Top-ups fill to TOP by what he HELD in Actuals before that scan, one per ticker (freeze_snapshot).
_A = json.loads((ROOT / "live/actuals.json").read_text()).get("trades") or []
def _held(day, before):
    out = set()
    for t in _A:
        s = t.get("source") or {}
        if s.get("date") == day and not (s.get("hhmm") and s["hhmm"] >= before):
            out.add((t.get("pick") or {}).get("ticker"))
    return out
out = []
for day, g in E.groupby("day"):
    picks = [r._asdict() for r in g[g.slot == "freeze"].head(TOP).itertuples(index=False)]
    for slot in ("1530", "1545"):
        if not picks:
            # freeze_snapshot: a day with no picks yet is written whole from this scan -- no Actuals cap.
            picks = [r._asdict() for r in g[g.slot == slot].head(TOP).itertuples(index=False)]
            continue
        # config.FABLE_TOPUP_BY_ACTUALS: count what he held in Actuals by then, else the day's own picks.
        by_actuals = bool(getattr(cfg, "FABLE_TOPUP_BY_ACTUALS", False))
        h = _held(day, slot) if by_actuals else set()
        seen = {p["ticker"] for p in picks} | h; n = len(h) if by_actuals else len(picks)
        for r in g[g.slot == slot].itertuples(index=False):
            if n >= TOP:
                break
            if r.ticker not in seen:
                picks.append(r._asdict()); seen.add(r.ticker); n += 1
    out.extend(picks)
P = pd.DataFrame(out)
P["entry_date"] = pd.to_datetime(P.day); P["entry_date_dt"] = P.entry_date; P["expiry_date"] = pd.to_datetime(P.exp); P["realize_date"] = P.expiry_date
P["credit"] = (FILL * P.model_credit).clip(upper=P.width - 0.01).round(4)
P["max_loss_adj"] = (P.width - P.credit).round(4); P["max_loss_dollar"] = P.max_loss_adj * 100
P["pnl_per_contract"] = [settle_pnl(s, ks, kl, c, w - c, t) * 100 for s, ks, kl, c, w, t in
                         zip(P.settle, P.short_strike, P.long_strike, P.credit, P.width, P.spread_type)]
bp = P.spread_type.eq("bull_put")
win = np.where(bp, P.settle >= P.short_strike, P.settle <= P.short_strike); loss = np.where(bp, P.settle <= P.long_strike, P.settle >= P.long_strike)
P["_outcome"] = np.where(win, "WIN", np.where(loss, "LOSS", "PARTIAL")); P["expiry_close"] = P.settle
b = (P.model_credit / (P.width - P.model_credit)).values
P["w_star"], P["G"] = ec.kelly(P.p.values, P.q.values, P.ro.values, b)
P["w_star_carry"] = ec.kelly_carry(P.p.values, P.q.values, P.ro.values, b, (cfg.CARRY_RATE * np.clip(P.DTE.astype(float), 1, None) / 365.0).values)
P["DKL"] = P.D_ent; P["GROUND"] = P.G24

with contextlib.redirect_stdout(io.StringIO()):
    pay = rbr.patch_config(rmc.build_payload(P, 2026, "History under the current canon"), True)
k = pay["config"]
k["universe"] = "SP100 + SPY/QQQ/IWM, IBKR live snapshots"
k["selection"] = (f"History's schedule and logic under the current canon: 15:15 freeze, topped up at 15:30 and 15:45 to {TOP} "
                  f"{'less the trades held in Actuals' if getattr(cfg, 'FABLE_TOPUP_BY_ACTUALS', False) else 'by its own picks'}; model credit >= {FLOOR:.2f}x width, combo quote (else leg mids) >= model, "
                  f"top-{TOP} by margin of victory M·e^(−k·D_ent) at k={K:g}"
                  + (f" (GROUND >= {float(GMIN):g})" if GMIN is not None else ""))
k["fill_basis"] = f"{FILL:.2f}× smile-fit model credit; partial-WIN at 50% intrinsic; no commission"
k["days"] = "every scan day in the archive"
(ROOT / "live/data/ibkr_replay_equity.json").write_text(json.dumps(pay, indent=2))
s = pay["summary"]
print(f"{s['n_trades']} trades ({P.spread_type.value_counts().to_dict()}), qty2 ${s['strategy_final']:,.0f}, $-Sh {s['strategy_sharpe_dollar']}, "
      f"DD {s['strategy_max_dd']}%, qty1 ${s['qty1_final']:,.0f}, yield {s['strategy_yield']}%, window {s['window_start']}..{s['window_end']}, weeks {len(pay['weeks'])}")
