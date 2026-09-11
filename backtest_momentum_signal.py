"""Gate 1 for a directional MOMENTUM overlay (2020-2025).

Mirrors report_oot_2026.py candidate construction + realization EXACTLY
(0.80x clamped-LAST fill, partial-WIN 50% haircut, MIN_OI=100, all filters
OFF), but DELIBERATELY UNCONDITIONED: no GROUND scoring, no top-5 selection.
The sample is the full candidate universe the scorer would see, so it is not
already filtered by the signal momentum is meant to add to.

Feature: trailing N-day return of the underlying per (Symbol, entry_date),
    mom_N = UnderlyingPrice[t] / UnderlyingPrice[t - N trading days] - 1
Also a per-ticker RANK form (mom ranked against that ticker's own trailing
history), matching how iv_rank_bucket / a future skew_rank_bucket would wire in.

Trend-continuation hypothesis: high momentum => recent up-trend => short puts
(bull_put) safer (lower breach, higher P&L) and short calls (bear_call) more
exposed (higher breach, lower P&L). The two directions must point OPPOSITE ways
if momentum carries real directional information.

Writes nothing to production. Caches the realized+joined table to
output/momentum_gate_candidates.parquet for fast re-runs.
"""
import sys, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads

YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
YEAR_PARQUET = 'output/{}_sp500_last.parquet'
CACHE = 'output/momentum_gate_candidates.parquet'
SP100 = set(bt_config.SP100_TICKERS)
ACTIVE_DOWS = [0, 1, 2, 3]
MOM_LOOKBACKS = [5, 10, 20, 60]   # trading days
PRIMARY = 20


def build_price_series(years):
    """Per-(Symbol, DataDate) underlying price across ALL years, one row/day."""
    frames = []
    for y in years:
        p = YEAR_PARQUET.format(y)
        if not os.path.exists(p):
            print(f'  MISSING {p} — skipping'); continue
        df = pd.read_parquet(p, columns=['Symbol', 'DataDate', 'UnderlyingPrice'])
        df = df[df['Symbol'].isin(SP100)]
        frames.append(df)
    px = pd.concat(frames, ignore_index=True)
    px = (px.dropna(subset=['UnderlyingPrice'])
            .groupby(['Symbol', 'DataDate'], as_index=False)['UnderlyingPrice'].first()
            .sort_values(['Symbol', 'DataDate'])
            .reset_index(drop=True))
    # trailing N-day return per symbol (shift on the daily series)
    for n in MOM_LOOKBACKS:
        px[f'mom_{n}'] = px.groupby('Symbol')['UnderlyingPrice'].transform(
            lambda s: s / s.shift(n) - 1.0)
    return px


def build_candidates_year(year):
    p = YEAR_PARQUET.format(year)
    if not os.path.exists(p):
        return pd.DataFrame(), {}
    df = pd.read_parquet(p)
    df = df[df['Symbol'].isin(SP100)]
    df['PutCall'] = df['PutCall'].str.lower().str.strip()
    # expiry settlement price: UnderlyingPrice on the row where DataDate==Expiry
    expiry_close = (df[df['DataDate'] == df['ExpirationDate']]
                    .groupby(['Symbol', 'ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df['dow'] = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow'] == 4]                       # Friday expiry
    df = df[(df['DTE'] >= 1) & (df['DTE'] <= 4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0

    # same knobs report_oot_2026.py sets before build_candidates
    spreads.REGIME_FILTER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.EARNINGS_FILTER = False
    spreads.HOLIDAY_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100
    bt_config.CREDIT_BASIS = "last_clamped"
    bt_config.CREDIT_SCALE = 1.0

    cand = spreads.build_candidates(df)
    return cand, expiry_close


def realize(cand, expiry_close):
    """Same realization as report_oot_2026.realize(): 0.80x fill, true payoff."""
    if cand.empty:
        return pd.DataFrame()
    c = cand.copy()
    c['expiry_close'] = c.apply(
        lambda r: expiry_close.get((r['ticker'], r['expiry_date'])), axis=1)
    c = c.dropna(subset=['expiry_close']).copy()
    if c.empty:
        return pd.DataFrame()
    c['credit'] = c['net_credit'] * 0.80
    c['width'] = c['net_credit'] + c['max_loss']
    c['max_loss_adj'] = c['width'] - c['credit']
    c['pnl_per_contract'] = c.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100

    # outcome class + partial-WIN 50% haircut (report_oot canon)
    def oc(r):
        sp, ss, ls = r['expiry_close'], r['short_strike'], r['long_strike']
        if r['spread_type'] == 'bull_put':
            return 'WIN' if sp > ss else ('LOSS' if sp <= ls else 'PARTIAL')
        return 'WIN' if sp < ss else ('LOSS' if sp >= ls else 'PARTIAL')
    c['outcome'] = c.apply(oc, axis=1)
    hc = (c['outcome'] == 'PARTIAL') & (c['pnl_per_contract'] > 0)
    c.loc[hc, 'pnl_per_contract'] *= 0.5

    # breach = short leg finished ITM (not a clean WIN)
    c['breach'] = (c['outcome'] != 'WIN')
    c['entry_date'] = pd.to_datetime(c['entry_date'])
    return c


def load_or_build():
    if os.path.exists(CACHE):
        print(f'Loading cached realized candidates from {CACHE}')
        return pd.read_parquet(CACHE)
    print('Building price series across all years...')
    px = build_price_series(YEARS)
    print(f'  price rows: {len(px):,}')
    parts = []
    for y in YEARS:
        print(f'── {y} ──', flush=True)
        cand, exp = build_candidates_year(y)
        r = realize(cand, exp)
        print(f'   candidates={len(cand):,}  realized={len(r):,}')
        parts.append(r)
    allc = pd.concat([p for p in parts if not p.empty], ignore_index=True)
    # join momentum on (Symbol, DataDate) == (ticker, entry_date)
    momcols = ['Symbol', 'DataDate'] + [f'mom_{n}' for n in MOM_LOOKBACKS]
    allc = allc.merge(px[momcols], left_on=['ticker', 'entry_date'],
                      right_on=['Symbol', 'DataDate'], how='left')
    allc.to_parquet(CACHE)
    print(f'Wrote {CACHE}: {len(allc):,} rows')
    return allc


def quintile_report(df, momcol, label, rank_per_ticker=False):
    d = df.dropna(subset=[momcol]).copy()
    if rank_per_ticker:
        d['_signal'] = d.groupby('ticker')[momcol].rank(pct=True)
    else:
        d['_signal'] = d[momcol]
    print(f'\n{"="*72}\n{label}   (n={len(d):,}, rank_per_ticker={rank_per_ticker})\n{"="*72}')
    for st in ['bull_put', 'bear_call']:
        sub = d[d['spread_type'] == st].copy()
        if len(sub) < 100:
            print(f'  {st}: n={len(sub)} too small'); continue
        try:
            sub['q'] = pd.qcut(sub['_signal'], 5, labels=[1, 2, 3, 4, 5], duplicates='drop')
        except ValueError:
            print(f'  {st}: cannot quintile'); continue
        g = sub.groupby('q', observed=True)
        pnl = g['pnl_per_contract'].mean()
        br = g['breach'].mean() * 100
        n = g.size()
        print(f'\n  {st}  (n={len(sub):,})')
        print(f'    {"Q":>2} {"n":>6} {"P&L/ctr":>9} {"breach%":>8}')
        for q in sorted(sub['q'].dropna().unique()):
            print(f'    {int(q):>2} {int(n[q]):>6} {pnl[q]:>9.2f} {br[q]:>8.1f}')
        q5, q1 = 5, 1
        if q5 in pnl.index and q1 in pnl.index:
            # Welch t on Q5-Q1 P&L
            a = sub[sub['q'] == q5]['pnl_per_contract']
            b = sub[sub['q'] == q1]['pnl_per_contract']
            dm = a.mean() - b.mean()
            se = np.sqrt(a.var(ddof=1)/len(a) + b.var(ddof=1)/len(b))
            t = dm/se if se > 0 else float('nan')
            # breach diff + z (two-proportion)
            pa, pb = a.index.size, b.index.size  # placeholder
            ba = sub[sub['q'] == q5]['breach']; bb = sub[sub['q'] == q1]['breach']
            dbr = (ba.mean() - bb.mean()) * 100
            pooled = pd.concat([ba, bb]).mean()
            sez = np.sqrt(pooled*(1-pooled)*(1/len(ba)+1/len(bb)))
            z = (ba.mean()-bb.mean())/sez if sez > 0 else float('nan')
            print(f'    Q5-Q1 P&L: {dm:+.2f}  t={t:+.2f}   |   breach: {dbr:+.2f}pp  z={z:+.2f}')


if __name__ == '__main__':
    df = load_or_build()
    print(f'\nTotal realized candidates: {len(df):,}')
    print(f'Date range: {df["entry_date"].min().date()} -> {df["entry_date"].max().date()}')
    print(f'bull_put={int((df["spread_type"]=="bull_put").sum()):,}  '
          f'bear_call={int((df["spread_type"]=="bear_call").sum()):,}')
    for n in MOM_LOOKBACKS:
        quintile_report(df, f'mom_{n}', f'RAW momentum, {n}-day trailing return', rank_per_ticker=False)
    # per-ticker rank form for the primary lookback (how it'd actually wire in)
    quintile_report(df, f'mom_{PRIMARY}', f'PER-TICKER-RANK momentum, {PRIMARY}-day', rank_per_ticker=True)
