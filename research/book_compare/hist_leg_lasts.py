"""Historical two-book comparison off the scan snapshots (user 2026-11-02: 'we don't have any
historical lasts in our data?'). Every 15-minute snapshot (live/snapshots/DATE/HHMM.parquet) holds
each option's LastPrice and Volume for both rights, so for every ranked candidate at every scan:
  credit book : short leg last - long leg last, on the canon right            (credit)
  debit book  : width - (long leg last - short leg last), on the parity right (credit equivalent)
  volume      : the two legs' session volume on each right (where the flow is)
Leg lasts are not simultaneous prints, so the spread-from-lasts is noisy; volume is the firmer read.
Usage: hist_leg_lasts.py [--since YYYY-MM-DD]
"""
import json, sys, glob, statistics as st, collections
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[2]
since = sys.argv[sys.argv.index("--since") + 1] if "--since" in sys.argv else "2026-10-01"
rows = []
days = collections.Counter()
for rf in sorted(glob.glob(str(ROOT / "live" / "ranked" / "20??-??-??_????.json"))):
    stem = Path(rf).stem; day, hhmm = stem.split("_")
    if day < since: continue
    pq = ROOT / "live" / "snapshots" / day / f"{hhmm}.parquet"
    if not pq.exists(): continue
    try:
        d = json.load(open(rf)); snap = pd.read_parquet(pq, columns=["Symbol", "ExpirationDate", "StrikePrice", "PutCall", "LastPrice", "Volume", "BidPrice", "AskPrice"])
    except Exception: continue
    snap["exp"] = pd.to_datetime(snap["ExpirationDate"]).dt.strftime("%Y-%m-%d")
    snap["pc"] = snap["PutCall"].astype(str).str.lower().str[0]
    idx = {(r.Symbol, r.exp, float(r.StrikePrice), r.pc): r for r in snap.itertuples()}
    for r in d.get("ticker") or []:
        try:
            w = abs(float(r["short_strike"]) - float(r["long_strike"])); exp = str(r["expiry_date"])[:10]
            cr = "p" if r["spread_type"] == "bull_put" else "c"; dr = "c" if cr == "p" else "p"
            cs, cl = idx.get((r["ticker"], exp, float(r["short_strike"]), cr)), idx.get((r["ticker"], exp, float(r["long_strike"]), cr))
            ds, dl = idx.get((r["ticker"], exp, float(r["short_strike"]), dr)), idx.get((r["ticker"], exp, float(r["long_strike"]), dr))
            if not all((cs, cl, ds, dl)): continue
            c_last = d_last = None
            if cs.LastPrice > 0 and cl.LastPrice > 0:
                v = float(cs.LastPrice) - float(cl.LastPrice)
                if 0 < v <= w: c_last = v
            if ds.LastPrice > 0 and dl.LastPrice > 0:
                v = w - (float(dl.LastPrice) - float(ds.LastPrice))
                if 0 < v <= w: d_last = v
            rows.append({"day": day, "hhmm": hhmm, "ticker": r["ticker"], "side": r["spread_type"], "w": w,
                         "qualified": bool(r.get("qualified")), "c_last": c_last, "d_last": d_last,
                         "c_vol": float(cs.Volume or 0) + float(cl.Volume or 0), "d_vol": float(ds.Volume or 0) + float(dl.Volume or 0)})
            days[day] += 1
        except Exception:
            continue
df = pd.DataFrame(rows)
print(f"since {since}: {len(df)} candidate-scans over {len(days)} days")
both = df.dropna(subset=["c_last", "d_last"])
print(f"\nSPREAD FROM LEG LASTS, credit scale (both books printed on both legs): {len(both)} rows")
print(f"  mean credit-book last {both.c_last.mean():.3f}   mean debit-book last {both.d_last.mean():.3f}   debit minus credit {(both.d_last-both.c_last).mean():+.3f}/sh = {100*((both.d_last-both.c_last)/both.w).mean():+.1f}% of width")
print(f"  debit book higher on {(both.d_last>both.c_last).mean()*100:.0f}% of rows; median diff {(both.d_last-both.c_last).median():+.3f}")
q = both[both.qualified]
if len(q): print(f"  qualified only ({len(q)}): debit minus credit {(q.d_last-q.c_last).mean():+.3f}/sh, debit higher on {(q.d_last>q.c_last).mean()*100:.0f}%")
for side, g in both.groupby("side"):
    print(f"  {side:9} ({len(g)}): credit-right last {g.c_last.mean():.3f} vs twin-right {g.d_last.mean():.3f}, diff {(g.d_last-g.c_last).mean():+.3f}")
# Volume: medians, and rows where either leg reports a junk value (the snapshot sometimes carries
# tens of millions on one leg) are dropped from the volume read.
vdf = df[(df.c_vol < 1e5) & (df.d_vol < 1e5)]
print(f"\nLEG VOLUME (session, both legs; medians, {len(vdf)} clean rows): credit right {vdf.c_vol.median():.0f} vs debit right {vdf.d_vol.median():.0f} per scan;"
      f" credit right busier on {(vdf.c_vol>vdf.d_vol).mean()*100:.0f}% of rows, tie {(vdf.c_vol==vdf.d_vol).mean()*100:.0f}%")
for side, g in vdf.groupby("side"):
    print(f"  {side:9}: credit-right vol {g.c_vol.median():.0f} vs twin-right {g.d_vol.median():.0f}; credit busier {(g.c_vol>g.d_vol).mean()*100:.0f}%")
qv = vdf[vdf.qualified]
if len(qv): print(f"  qualified only ({len(qv)}): credit-right {qv.c_vol.median():.0f} vs twin-right {qv.d_vol.median():.0f}; credit busier {(qv.c_vol>qv.d_vol).mean()*100:.0f}%")
print(f"\nby day (debit minus credit on leg lasts, credit scale):")
for day, g in both.groupby("day"):
      _v = vdf[vdf.day == day]
    print(f"  {day}: n {len(g):4d}  diff {(g.d_last-g.c_last).mean():+.3f}  debit higher {(g.d_last>g.c_last).mean()*100:3.0f}%  median vol credit/debit {_v.c_vol.median():.0f}/{_v.d_vol.median():.0f}")
