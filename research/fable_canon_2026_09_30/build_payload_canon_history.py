"""Site payload for the PLOT tab: History as today's canon would have picked it (user 2026-10-17).

Every archived IBKR snapshot day is re-ranked with live.ranker.rank_snapshot as the code stands now
(LIVE_COMBO_ENABLED = False: quotes = the snapshot's leg mids, no IB calls), on History's schedule:
the 15:15 scan is the frozen day (15:01 on the half-hour days before 2026-09-02), topped up to 6 at
15:30 (15:31) and again at 15:45. Gates as live: model credit >= 0.45 x width, quote above the min,
GROUND >= config.FABLE_GROUND_MIN; ranked by GROUND. Booked at ent_canon.FILL_MULT x model (History's
1.04), settled at the expiry close with live.closes.load_closes().

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
live_config.LIVE_COMBO_ENABLED = False

if REBUILD or not FRAME.exists():
    closes = load_closes(); closes["date"] = pd.to_datetime(closes.date).dt.strftime("%Y-%m-%d")
    px = closes.set_index(["ticker", "date"]).close
    rows = []
    for dd in sorted(p for p in (ROOT / "live/snapshots").iterdir() if p.is_dir() and p.name >= SINCE):
        for slot, files in SLOTS:
            f = next((dd / f"{hm}.parquet" for hm in files if (dd / f"{hm}.parquet").exists()), None)
            if f is None:
                continue
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
            "net_credit", "model_credit", "model_cw", "IV", "p", "q", "ro", "G", "EV", "D_ent", "GROUND", "G24", "settle",
            "above_min", "qualified"]
    R[cols].to_csv(FRAME, index=False)
    print(f"{len(R)} rows, {R.day.nunique()} days, {R.settle.isna().sum()} unsettled -> {FRAME.name}")

R = pd.read_csv(FRAME, dtype={"hm": str})
R = R[(R.day >= SINCE) & R.settle.notna() & (R.model_credit > 0) & R.net_credit.notna() & R.p.notna()].copy()
E = R[(R.model_cw >= FLOOR) & R.above_min.astype(bool)].copy()
GMIN = getattr(cfg, "FABLE_GROUND_MIN", None)
if GMIN is not None:
    E = E[E.G24 >= float(GMIN)].copy()
E = E.sort_values(["day", "slot", "G24"], ascending=[True, True, False])
key = ["ticker", "short_strike", "long_strike", "exp"]; out = []
for day, g in E.groupby("day"):
    held, picks = set(), []
    for slot, _ in SLOTS:            # freeze, then each top-up fills the day back to TOP
        for r in g[g.slot == slot].itertuples(index=False):
            if len(picks) >= TOP:
                break
            k = tuple(getattr(r, c) for c in key)
            if k not in held:
                held.add(k); picks.append(r._asdict())
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
k["selection"] = (f"History's schedule under the current canon: 15:15 freeze, topped up to {TOP} at 15:30 and 15:45; "
                  f"model credit >= {FLOOR:.2f}x width, quoted mid >= model, top-{TOP} by GROUND at k={K:g}"
                  + (f" (GROUND >= {float(GMIN):g})" if GMIN is not None else ""))
k["fill_basis"] = f"{FILL:.2f}× smile-fit model credit; partial-WIN at 50% intrinsic; no commission"
k["days"] = "every scan day in the archive"
(ROOT / "live/data/ibkr_replay_equity.json").write_text(json.dumps(pay, indent=2))
s = pay["summary"]
print(f"{s['n_trades']} trades ({P.spread_type.value_counts().to_dict()}), qty2 ${s['strategy_final']:,.0f}, $-Sh {s['strategy_sharpe_dollar']}, "
      f"DD {s['strategy_max_dd']}%, qty1 ${s['qty1_final']:,.0f}, yield {s['strategy_yield']}%, window {s['window_start']}..{s['window_end']}, weeks {len(pay['weeks'])}")
