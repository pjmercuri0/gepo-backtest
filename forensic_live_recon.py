"""Forensic reconciliation of settled live picks vs vendor EOD data.

For each settled pick in live/frozen/*.json:
  - frozen net_credit (15:45 live snapshot, clamped LAST)
  - vendor EOD clamped-LAST credit for the same spread (4pm data the backtest uses)
  - actual_credit (real broker fill) where recorded
  - PnL under each credit basis, holding the settlement outcome fixed

Read-only: never writes to any data file.
"""
import json, glob, os
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIRS = {'2026-05': 'data/DG_2026May', '2026-06': 'data/DG_2026June'}
USECOLS = ['Symbol', 'ExpirationDate', 'AskPrice', 'BidPrice', 'LastPrice',
           'PutCall', 'StrikePrice', 'DataDate']


def vendor_rows(date_str, symbols):
    ym = date_str[:7]
    d = os.path.join(ROOT, VENDOR_DIRS[ym])
    ymd = date_str.replace('-', '')
    frames = []
    for part in (1, 2):
        path = os.path.join(d, f'Greek_{ymd}_OData{part}.csv')
        if not os.path.exists(path):
            continue
        for chunk in pd.read_csv(path, usecols=USECOLS, chunksize=500_000,
                                 skipinitialspace=True):
            frames.append(chunk[chunk['Symbol'].isin(symbols)])
    if not frames:
        return pd.DataFrame(columns=USECOLS)
    return pd.concat(frames, ignore_index=True)


def clamped_last(row):
    return max(float(row['BidPrice']),
               min(float(row['LastPrice']), float(row['AskPrice'])))


def leg_quote(vdf, sym, expiry, strike):
    m = vdf[(vdf['Symbol'] == sym) & (vdf['PutCall'] == 'put')
            & (vdf['ExpirationDate'] == expiry)
            & (abs(vdf['StrikePrice'].astype(float) - strike) < 1e-6)]
    if m.empty:
        return None
    return m.iloc[0]


def pnl_per_share(credit, ss, ls, settle, width):
    intrinsic = min(max(ss - settle, 0.0), width)  # all picks are bull_put
    return credit - intrinsic


def outcome_class(ss, ls, settle):
    if settle > ss: return 'WIN'
    if settle <= ls: return 'LOSS'
    return 'PARTIAL'


def main():
    picks = []
    for f in sorted(glob.glob(os.path.join(ROOT, 'live/frozen/*.json'))):
        d = json.load(open(f))
        res = (d.get('outcome') or {}).get('results', {})
        if not res:
            continue
        for p in d.get('top_picks', []):
            r = res.get(p['ticker'])
            if not r:
                continue
            picks.append({
                'date': d['data_date'], 'ticker': p['ticker'],
                'ss': float(p['short_strike']), 'ls': float(p['long_strike']),
                'expiry': p['expiry_date'][:10],
                'width': float(p['spread_width']),
                'frozen_credit': float(p['net_credit']),
                'actual_credit': p.get('actual_credit'),
                'settle': float(r['underlying_price']),
                'live_pnl_share': float(r['pnl_per_share']),
                'result': r['result'],
            })
    df = pd.DataFrame(picks)
    print(f'{len(df)} settled picks')

    vend = []
    for date in sorted(df['date'].unique()):
        sub = df[df['date'] == date]
        vdf = vendor_rows(date, set(sub['ticker']))
        for _, p in sub.iterrows():
            s = leg_quote(vdf, p['ticker'], p['expiry'], p['ss'])
            l = leg_quote(vdf, p['ticker'], p['expiry'], p['ls'])
            if s is None or l is None:
                vend.append({'vendor_credit': None, 'vendor_mid': None})
                continue
            vend.append({
                'vendor_credit': max(clamped_last(s) - clamped_last(l), 0.0),
                'vendor_mid': max((float(s['BidPrice']) + float(s['AskPrice'])) / 2
                                  - (float(l['BidPrice']) + float(l['AskPrice'])) / 2, 0.0),
            })
        print(f'  {date}: vendor rows={len(vdf)}')
    df = pd.concat([df.reset_index(drop=True), pd.DataFrame(vend)], axis=1)

    # PnL under each basis, settlement held fixed
    def pnl_cols(r):
        out = {}
        for name, credit in [('live080frozen', 0.80 * r['frozen_credit']),
                             ('bt080vendor', 0.80 * r['vendor_credit'] if r['vendor_credit'] is not None else None),
                             ('actual', r['actual_credit'])]:
            if credit is None or (isinstance(credit, float) and pd.isna(credit)):
                out[f'pnl_{name}'] = None
                continue
            pnl = pnl_per_share(credit, r['ss'], r['ls'], r['settle'], r['width'])
            oc = outcome_class(r['ss'], r['ls'], r['settle'])
            if name == 'bt080vendor' and oc == 'PARTIAL' and pnl > 0:
                pnl *= 0.5  # canon partial-WIN haircut
            out[f'pnl_{name}'] = pnl * 100
        return pd.Series(out)
    df = pd.concat([df, df.apply(pnl_cols, axis=1)], axis=1)
    df['fill_ratio'] = df['actual_credit'] / df['frozen_credit']
    df['frozen_vs_vendor'] = df['frozen_credit'] / df['vendor_credit']

    pd.set_option('display.width', 250)
    print(df[['date', 'ticker', 'result', 'frozen_credit', 'vendor_credit', 'vendor_mid',
              'actual_credit', 'fill_ratio', 'frozen_vs_vendor',
              'live_pnl_share', 'pnl_live080frozen', 'pnl_bt080vendor', 'pnl_actual']]
          .to_string(index=False))

    print('\n=== AGGREGATES (per contract) ===')
    n = len(df)
    print(f"live recorded:                total ${df['live_pnl_share'].sum()*100:8.2f}  avg ${df['live_pnl_share'].mean()*100:7.2f}  (n={n})")
    print(f"0.80 x frozen LAST:           total ${df['pnl_live080frozen'].sum():8.2f}  avg ${df['pnl_live080frozen'].mean():7.2f}")
    v = df.dropna(subset=['pnl_bt080vendor'])
    print(f"0.80 x vendor EOD (backtest): total ${v['pnl_bt080vendor'].sum():8.2f}  avg ${v['pnl_bt080vendor'].mean():7.2f}  (n={len(v)})")
    a = df.dropna(subset=['pnl_actual'])
    print(f"actual fills:                 total ${a['pnl_actual'].sum():8.2f}  avg ${a['pnl_actual'].mean():7.2f}  (n={len(a)})")
    print(f"\nfrozen/vendor credit ratio:  mean {df['frozen_vs_vendor'].mean():.3f}  median {df['frozen_vs_vendor'].median():.3f}")
    print(f"actual/frozen fill ratio:    mean {df['fill_ratio'].mean():.3f}  median {df['fill_ratio'].median():.3f}  (n={df['fill_ratio'].notna().sum()})")
    print(f"WR: {(df['result']=='WIN').mean()*100:.1f}% W / {(df['result']=='LOSS').mean()*100:.1f}% L / {(df['result']=='PARTIAL').mean()*100:.1f}% P")
    df.to_csv('/tmp/forensic_live_recon.csv', index=False)
    print('\nwrote /tmp/forensic_live_recon.csv')


if __name__ == '__main__':
    main()
