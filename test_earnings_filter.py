"""Test earnings filter: reject picks where earnings falls in (entry, expiry].
Compare to canonical (no earnings filter) under our standard config.
"""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
EARNINGS_CSV = 'data/earnings_calendar.csv'
ACTIVE_DOWS = [0, 1, 3]
THRESH = {0: 0.003, 1: 0.005, 3: 0.010}
KELLY_FRAC = 0.0625
KELLY_CAP = 5
START_BANKROLL = 10_000.0


def build_earnings_lookup():
    df = pd.read_csv(EARNINGS_CSV, parse_dates=['EarningsDate'])
    out = {}
    for sym, grp in df.groupby('Symbol'):
        out[sym] = pd.DatetimeIndex(sorted(grp['EarningsDate'].unique()))
    return out


def score_year(year, use_earnings_filter):
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    ec = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
          .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice'].first().to_dict())
    dv = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]
    df = df_full.copy()
    df['dow']=df['DataDate'].dt.dayofweek; df['exp_dow']=df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float)>0]
    df = df.copy()
    df['AbsDelta']=df['Delta'].abs(); df['MidPrice']=(df['BidPrice']+df['AskPrice'])/2

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    spreads.EARNINGS_FILTER = use_earnings_filter
    spreads.EARNINGS_LOOKUP = build_earnings_lookup() if use_earnings_filter else {}
    bt_config.MIN_OPEN_INTEREST = 100

    cand = spreads.build_candidates(df)
    scored = ground.score_candidates(cand)
    def gnd(r):
        G,DKL=r.get('G'),r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0)*math.exp(-ground.DKL_K*DKL)
    scored['GROUND']=scored.apply(gnd, axis=1)
    return scored.dropna(subset=['GROUND']), dv, ec


def select_realize(scored, dv, ec):
    s=scored.copy(); s['entry_dow']=pd.to_datetime(s['entry_date']).dt.dayofweek
    parts=[]
    for dow in ACTIVE_DOWS:
        sub=s[(s['entry_dow']==dow)&(s['GROUND']>=THRESH[dow])]
        top=sub.sort_values(['entry_date','GROUND'],ascending=[True,False]).groupby('entry_date').head(5)
        parts.append(top)
    sel=pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if sel.empty: return pd.DataFrame()
    def lookup(r):
        pc='put' if r['spread_type']=='bull_put' else 'call'
        try:
            sr=dv.loc[(r['entry_date'],r['ticker'],r['expiry_date'],r['short_strike'],pc)]
            lr=dv.loc[(r['entry_date'],r['ticker'],r['expiry_date'],r['long_strike'],pc)]
            if hasattr(sr,'iloc') and sr.ndim>1: sr=sr.iloc[0]
            if hasattr(lr,'iloc') and lr.ndim>1: lr=lr.iloc[0]
            sl=max(float(sr['BidPrice']),min(float(sr['LastPrice']),float(sr['AskPrice'])))
            ll=max(float(lr['BidPrice']),min(float(lr['LastPrice']),float(lr['AskPrice'])))
            return max(sl-ll,0), ec.get((r['ticker'],r['expiry_date']))
        except KeyError: return None,None
    sel['raw_last'],sel['expiry_close']=zip(*sel.apply(lookup,axis=1))
    ok=sel.dropna(subset=['raw_last','expiry_close']).copy()
    ok['credit']=ok['raw_last']*0.85
    ok['width']=ok['net_credit']+ok['max_loss']
    ok['ml_adj']=ok['width']-ok['credit']; ok['ml_dollar']=ok['ml_adj']*100
    ok['pnl_per_ctr']=ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'],r['short_strike'],r['long_strike'],r['credit'],r['ml_adj'],r['spread_type']),axis=1)*100
    def qty(r):
        ws=r.get('w_star')
        if ws is None or pd.isna(ws) or ws<=0 or r['ml_dollar']<=0: return 1
        return max(1,min(KELLY_CAP,int(KELLY_FRAC*float(ws)*START_BANKROLL/r['ml_dollar'])))
    ok['qty']=ok.apply(qty,axis=1); ok['pnl']=ok['qty']*ok['pnl_per_ctr']
    return ok


def stats(ok):
    if ok.empty: return 0,0,0,0,0
    n=len(ok); tot=ok['pnl'].sum(); mu=ok['pnl'].mean()
    win=100*(ok['pnl']>0).mean()
    daily=ok.groupby('entry_date')['pnl'].sum().sort_index()
    sg=daily.std(ddof=0); sh=(daily.mean()*np.sqrt(252)/sg) if sg>0 else 0
    return n,tot,mu,win,sh


print(f'\n══ Earnings filter test (Mon/Tue/Thu, per-day thresh, ¹⁄₁₆K cap=5) ══')
print(f'{"year":<5} {"variant":<14} {"trades":>5} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>7}')
print('-'*64)

base, with_earnings = [], []
for yr in YEARS:
    # baseline (no earnings filter)
    sc, dv, ec = score_year(yr, use_earnings_filter=False)
    ok_base = select_realize(sc, dv, ec)
    n,tot,mu,win,sh = stats(ok_base)
    print(f'{yr:<5} {"No filter":<14} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
    base.append(ok_base)

    # with earnings filter
    sc, dv, ec = score_year(yr, use_earnings_filter=True)
    ok_ef = select_realize(sc, dv, ec)
    n,tot,mu,win,sh = stats(ok_ef)
    print(f'{yr:<5} {"+ Earnings":<14} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
    with_earnings.append(ok_ef)


print(f'\n══ Combined 2022-2025 ══')
all_b = pd.concat(base, ignore_index=True)
all_e = pd.concat(with_earnings, ignore_index=True)
n,tot,mu,win,sh = stats(all_b)
print(f'{"No filter":<14} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
n,tot,mu,win,sh = stats(all_e)
print(f'{"+ Earnings":<14} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')


# Drop analysis
def _key(r): return (r['entry_date'], r['ticker'], r['short_strike'], r['spread_type'])
base_keys = set(all_b.apply(_key, axis=1)) if not all_b.empty else set()
ef_keys   = set(all_e.apply(_key,   axis=1)) if not all_e.empty   else set()
dropped_keys = base_keys - ef_keys
dropped = all_b[all_b.apply(_key, axis=1).isin(dropped_keys)]
print(f'\n══ Picks DROPPED by earnings filter ══')
print(f'  {len(dropped)} picks')
print(f'  Their combined P&L: ${dropped["pnl"].sum():+,.0f}')
print(f'  Win rate: {100*(dropped["pnl"]>0).mean():.1f}%')
print(f'  Mean: ${dropped["pnl"].mean():+.2f}')
