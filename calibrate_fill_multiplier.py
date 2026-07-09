"""Replay frozen 15:01 picks against later same-day snapshots to calibrate
the fill multiplier (achievable credit / freeze mid) on real recorded markets.

For each frozen pick and each post-freeze snapshot t:
  natural_t = short_bid_t - long_ask_t   (credit guaranteed to fill at t)
  mid_t     = short_mid_t - long_mid_t   (credit if filled at combo mid at t)
Floor estimate  = max_t natural_t / mid_freeze  (limit order crossed by book)
Mid estimate    = max_t mid_t     / mid_freeze  (fill-at-mid sometime later)
Real May-28 fills averaged 0.82 x mid_freeze; this multiplies the sample.
"""
import sys, json, glob, os
import pandas as pd
sys.path.insert(0, '.')

rows = []
for fz in sorted(glob.glob('live/frozen/*.json')):
    day = os.path.basename(fz).replace('.json', '')
    d = json.load(open(fz))
    picks = d.get('top_picks') or []
    if not picks:
        continue
    snap_dir = f'live/snapshots/{day}'
    if not os.path.isdir(snap_dir):
        continue
    freeze_hhmm = pd.Timestamp(d.get('frozen_at', d['snapshot_ts'])).strftime('%H%M')
    later = sorted(t for t in (os.path.basename(p).replace('.parquet','')
                               for p in glob.glob(f'{snap_dir}/*.parquet'))
                   if t > freeze_hhmm)
    if not later:
        continue
    snaps = {}
    for t in later:
        try:
            s = pd.read_parquet(f'{snap_dir}/{t}.parquet')
            s = s.set_index(['Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()
            snaps[t] = s
        except Exception as e:
            print(f'  WARN {day} {t}: {e}')
    for p in picks:
        sb, sa = p.get('short_bid'), p.get('short_ask')
        lb, la = p.get('long_bid'), p.get('long_ask')
        if None in (sb, sa, lb, la):
            continue
        mid_freeze = (sb+sa)/2 - (lb+la)/2
        if mid_freeze <= 0:
            continue
        pc = 'put' if p['spread_type'] == 'bull_put' else 'call'
        exp = p['expiry_date']
        best_nat, best_mid, n_obs = None, None, 0
        for t, s in snaps.items():
            try:
                sr = s.loc[(p['ticker'], exp, p['short_strike'], pc)]
                lr = s.loc[(p['ticker'], exp, p['long_strike'],  pc)]
                if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
                if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
                sb2, sa2 = float(sr['BidPrice']), float(sr['AskPrice'])
                lb2, la2 = float(lr['BidPrice']), float(lr['AskPrice'])
                if min(sb2, sa2, lb2, la2) < 0:
                    continue
                nat = sb2 - la2
                mid2 = (sb2+sa2)/2 - (lb2+la2)/2
                best_nat = nat if best_nat is None else max(best_nat, nat)
                best_mid = mid2 if best_mid is None else max(best_mid, mid2)
                n_obs += 1
            except KeyError:
                continue
        if n_obs == 0:
            continue
        rows.append(dict(day=day, ticker=p['ticker'], stype=p['spread_type'],
                         mid_freeze=round(mid_freeze,3),
                         best_natural=round(best_nat,3), best_mid=round(best_mid,3),
                         nat_ratio=round(best_nat/mid_freeze,3),
                         mid_ratio=round(best_mid/mid_freeze,3),
                         n_obs=n_obs,
                         actual=p.get('actual_credit')))

R = pd.DataFrame(rows)
if R.empty:
    print('No replayable picks found.'); sys.exit(0)
print(R.to_string(index=False))
print(f'\nn={len(R)} picks across {R["day"].nunique()} days')
print(f'natural/mid_freeze (guaranteed-fill floor): mean {R["nat_ratio"].mean():.3f}  '
      f'median {R["nat_ratio"].median():.3f}  p25 {R["nat_ratio"].quantile(.25):.3f}  p75 {R["nat_ratio"].quantile(.75):.3f}')
print(f'best_mid/mid_freeze (fill-at-mid later):    mean {R["mid_ratio"].mean():.3f}  '
      f'median {R["mid_ratio"].median():.3f}')
print(f'share of picks where a 0.80 x mid_freeze limit was GUARANTEED crossed: '
      f'{100*(R["nat_ratio"] >= 0.80).mean():.0f}%')
for f in [0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 1.0]:
    print(f'  limit {f:.2f} x mid: guaranteed-fill rate {100*(R["nat_ratio"] >= f).mean():.0f}%   '
          f'fill-at-mid rate {100*(R["mid_ratio"] >= f).mean():.0f}%')
