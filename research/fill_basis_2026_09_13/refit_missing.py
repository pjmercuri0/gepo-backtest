"""Refit smiles for old-canon trades whose (ticker, entry_date) chain is absent from is_synth.parquet."""
import json, sys, warnings, time
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd, pyarrow.parquet as pq, pyarrow.compute as pc, pyarrow as pa
sys.path.insert(0, '.')
import ent_canon as ec
import subprocess, os
HERE = os.path.dirname(os.path.abspath(__file__))
SCR = HERE + '/'
def _archived(name):
    # the pre-2026-09-11 (old canon) payloads, straight from git; nothing on disk is overwritten
    p = SCR + name
    if not os.path.exists(p):
        open(p, 'w').write(subprocess.check_output(['git', 'show', f"5da76fd~1:live/data/{name.replace('old_', '')}"], text=True))
    return p
T = pd.DataFrame(json.load(open(_archived('old_backtest_equity.json')))['trades'])
T['entry_date'] = pd.to_datetime(T.entry)
S = pd.read_parquet('research/dkl_2026_09_13/is_synth.parquet', columns=['ticker', 'entry_date'])
S['entry_date'] = pd.to_datetime(S.entry_date)
have = set(zip(S.ticker, S.entry_date))
miss = T[[ (t, d) not in have for t, d in zip(T.ticker, T.entry_date)]][['ticker', 'entry_date']].drop_duplicates()
print('missing keys', len(miss))
cols = ['Symbol', 'DataDate', 'ExpirationDate', 'DTE', 'PutCall', 'StrikePrice', 'BidPrice', 'AskPrice', 'ImpliedVolatility', 'UnderlyingPrice']
out = []
for yr, g in miss.groupby(miss.entry_date.dt.year):
    t0 = time.time()
    pf = pq.ParquetFile(f'output/{yr}_sp500_last.parquet')
    print(yr, 'schema DataDate type', pf.schema_arrow.field('DataDate').type, 'rows', pf.metadata.num_rows, flush=True)
    syms = sorted(g.ticker.unique())
    tbl = pq.read_table(f'output/{yr}_sp500_last.parquet', columns=cols, filters=[('Symbol', 'in', syms), ('DTE', '<=', 5)])
    d = tbl.to_pandas()
    d['DataDate'] = pd.to_datetime(d.DataDate).dt.normalize(); d['ExpirationDate'] = pd.to_datetime(d.ExpirationDate).dt.normalize()
    d = d.merge(g.rename(columns={'ticker': 'Symbol', 'entry_date': 'DataDate'}), on=['Symbol', 'DataDate'], how='inner')
    d = d[d.DTE.between(1, 4)]
    print(yr, 'chain rows', len(d), 'keys', d[['Symbol', 'DataDate']].drop_duplicates().shape[0], f'[{time.time()-t0:.0f}s]', flush=True)
    F = ec.fit_smiles(d)
    sp = d.groupby(['Symbol', 'DataDate', 'ExpirationDate']).agg(entry_price=('UnderlyingPrice', 'median'), DTE=('DTE', 'first')).reset_index()
    F = F.merge(sp, on=['Symbol', 'DataDate', 'ExpirationDate'])
    out.append(F)
F = pd.concat(out).rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date', 'ExpirationDate': 'expiry_date'})
print('refit chains', len(F), 'keys covered', F[['ticker', 'entry_date']].drop_duplicates().shape[0], 'of', len(miss))
F.to_parquet(SCR + 'refit_missing.parquet')
