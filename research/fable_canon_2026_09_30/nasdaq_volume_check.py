"""Real exchange volume for a scan's candidates (mini, 2026-10-01).

The Volume column in live/snapshots/*.parquet is NOT reliable: on 2026-10-01 it showed 0 for legs the
user had traded that morning and fell from 2 to 0 between the 10:30 and 10:45 scans (ADP 265 put).
This pulls the end-of-day put chain from Nasdaq's public option-chain endpoint for every candidate in
a ranked scan and buckets each spread by the volume on its THINNER leg.
Usage: nasdaq_volume_check.py live/ranked/2026-10-01_1530.json   -> nasdaq_volume_<scan>.csv
Nasdaq serves the current chain only, so this must be run the same evening as the scan.
"""
import sys, json, time, urllib.request
from pathlib import Path
import pandas as pd
HERE = Path(__file__).resolve().parent
H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
     "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}

def chain(t, exp, cp):
    ac = "etf" if t in ("SPY", "QQQ", "IWM") else "stocks"
    u = (f"https://api.nasdaq.com/api/quote/{t}/option-chain?assetclass={ac}&limit=400&fromdate={exp}&todate={exp}"
         f"&excode=oprac&callput={cp}&money=all&type=all")
    with urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=25) as r:
        rows = (json.load(r).get("data") or {}).get("table", {}).get("rows") or []
    p = "p_" if cp == "put" else "c_"; out = {}
    for x in rows:
        try: k = float(x["strike"])
        except (TypeError, ValueError, KeyError): continue
        n = lambda v: 0 if v in (None, "", "--") else int(str(v).replace(",", ""))
        out[k] = (n(x.get(p + "Volume")), n(x.get(p + "Openinterest")))
    return out

scan = Path(sys.argv[1]); d = json.load(open(scan)); rows = []
for r in d["ticker"]:
    t, ks, kl = r["ticker"], float(r["short_strike"]), float(r["long_strike"])
    cp = "put" if r["spread_type"] == "bull_put" else "call"
    try: c = chain(t, str(r["expiry_date"])[:10], cp)
    except Exception as e: c = {}
    time.sleep(0.4)
    s, l = c.get(ks), c.get(kl)
    rows.append(dict(ticker=t, spread_type=r["spread_type"], short_strike=ks, long_strike=kl,
                     short_vol=s[0] if s else None, long_vol=l[0] if l else None,
                     short_oi=s[1] if s else None, long_oi=l[1] if l else None, snapshot_pick=bool(r.get("qualified"))))
T = pd.DataFrame(rows); T["thin_leg_vol"] = T[["short_vol", "long_vol"]].min(axis=1, skipna=False)
T["bucket"] = pd.cut(T.thin_leg_vol, [-1, 19, 199, 10**9], labels=["quiet (<20)", "middle (20-199)", "busy (200+)"])
T = T.sort_values("thin_leg_vol", ascending=False)
out = HERE / f"nasdaq_volume_{scan.stem}.csv"; T.to_csv(out, index=False)
print(T.to_string(index=False)); print(T.bucket.value_counts(dropna=False).to_dict()); print("wrote", out)
