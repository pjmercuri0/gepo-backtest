# Smile-fit model credit on the IBKR intraday chain of each live snapshot vs the IBKR quoted mid of the pick.
import sys, os, json, glob, numpy as np, pandas as pd
sys.path.insert(0, 'research/dkl_2026_09_13'); import synth_credit as sc
rows = []
for f in sorted(glob.glob('live/ranked/2026-*.json')):
    d = json.load(open(f)); sf = d.get('snapshot_file')
    if not sf or not os.path.exists(sf) or not d.get('top_picks'): continue
    P = pd.DataFrame(d['top_picks'])
    if not {'short_bid','short_ask','long_bid','long_ask','IV','DTE'}.issubset(P.columns): continue
    P['entry_date'] = pd.to_datetime(d['data_date']); P['expiry_date'] = pd.to_datetime(P.expiry_date)
    P['live_mid'] = (P.short_bid + P.short_ask) / 2 - (P.long_bid + P.long_ask) / 2
    P['net_credit'] = P.live_mid; P['file'] = f.split('/')[-1]
    try:
        R = sc.synth(P[['ticker','entry_date','expiry_date','DTE','spread_type','entry_price','short_strike','long_strike','net_credit','live_mid','short_bid','short_ask','long_bid','long_ask','IV','file']], [sf])
    except Exception as e:
        print('skip', f, e); continue
    rows.append(R)
L = pd.concat(rows, ignore_index=True); L.to_parquet('research/dkl_2026_09_13/live_synth.parquet')
m = L.dropna(subset=['model_credit'])
m = m[m.live_mid > 0]
r = m.model_credit / m.live_mid
print(f'\nlive picks {len(L)}, with model credit {len(m)}')
print('model / IBKR-quoted-mid:', r.describe(percentiles=[.1,.25,.5,.75,.9]).round(3).to_dict())
print('short-leg fitted IV / IBKR IV med', (m.iv_fit_short / m.IV).median().round(3))
late = m[m.file.str.contains('_15')]; print('late-day only n', len(late), 'med ratio', (late.model_credit/late.live_mid).median().round(3))
