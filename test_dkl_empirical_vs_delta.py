"""Test DKL(P_emp || Q_delta) — empirical-vs-delta reference.
LOYO: for each test year, build empirical table from OTHER 3 years.
Compare to canonical uniform DKL.
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground
import historical_probs as hp

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
ACTIVE_DOWS = [0, 1, 3]
THRESH = {0: 0.003, 1: 0.005, 3: 0.010}
KELLY_FRAC = 0.0625
KELLY_CAP = 5
START_BANKROLL = 10_000.0


def build_table_for(train_years):
    frames = []
    for y in train_years:
        df = pd.read_parquet(f'output/{y}_sp500_last.parquet')
        df = df[df['Symbol'].isin(SP100)]
        ec = (df[df['DataDate']==df['ExpirationDate']]
              .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice'].first())
        df = df[(df['DTE']>=1)&(df['DTE']<=4)].copy()
        df = df[df['DataDate'].dt.dayofweek.isin([0,1,2,3])]
        df = df[df['ExpirationDate'].dt.dayofweek==4]
        df['expiry_close'] = df.set_index(['Symbol','ExpirationDate']).index.map(ec.get)
        df = df.dropna(subset=['expiry_close','Delta','ImpliedVolatility'])
        df['abs_delta'] = df['Delta'].abs()
        df['itm'] = np.where(
            df['PutCall'].str.lower()=='put',
            df['expiry_close'] < df['StrikePrice'],
            df['expiry_close'] > df['StrikePrice'],
        ).astype(int)
        frames.append(df[['DTE','abs_delta','ImpliedVolatility','itm']])
    train = pd.concat(frames, ignore_index=True)
    train['delta_bucket'] = (train['abs_delta']*10).astype(int).clip(0,9)
    train['iv_capped'] = train['ImpliedVolatility'].clip(upper=3.0)
    iv_bins = train['iv_capped'].quantile([0,0.2,0.4,0.6,0.8,1.0]).values
    iv_bins[-1] += 0.001
    train['iv_bucket'] = pd.cut(train['iv_capped'], bins=iv_bins, labels=False, include_lowest=True)
    agg = train.groupby(['DTE','delta_bucket','iv_bucket']).agg(
        n=('itm','size'), p_itm=('itm','mean')
    ).reset_index()
    agg['p_itm_reliable'] = np.where(agg['n']>=30, agg['p_itm'], np.nan)
    return agg, iv_bins


def score_year(year):
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
    spreads.REGIME_FILTER=True; spreads.REGIME_PER_TICKER=False
    spreads.GAP_FILTER=False; spreads.LOW_VIX_BULLPUT_FILTER=False; spreads.SLIPPAGE_CENTS=0.0
    bt_config.MIN_OPEN_INTEREST=100
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


print(f'\n══ DKL reference: uniform vs empirical_vs_delta (LOYO) ══')
print(f'{"year":<5} {"ref":<22} {"trades":>5} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>7}')
print('-'*68)

uni, evd = [], []
for test_year in YEARS:
    train_years = [y for y in YEARS if y != test_year]
    agg, bins = build_table_for(train_years)
    hp._EMPIRICAL_TABLE = agg
    hp._EMPIRICAL_IV_BINS = bins

    ground.DKL_REFERENCE = "uniform"
    sc, dv, ec = score_year(test_year)
    ok_u = select_realize(sc, dv, ec)
    n,tot,mu,win,sh = stats(ok_u)
    print(f'{test_year:<5} {"uniform":<22} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
    uni.append(ok_u)

    ground.DKL_REFERENCE = "empirical_vs_delta"
    sc, dv, ec = score_year(test_year)
    ok_e = select_realize(sc, dv, ec)
    n,tot,mu,win,sh = stats(ok_e)
    print(f'{test_year:<5} {"empirical_vs_delta":<22} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
    evd.append(ok_e)


print(f'\n══ Combined 2022-2025 ══')
all_u = pd.concat(uni, ignore_index=True)
all_e = pd.concat(evd, ignore_index=True)
n,tot,mu,win,sh = stats(all_u)
print(f'{"uniform":<22} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
n,tot,mu,win,sh = stats(all_e)
print(f'{"empirical_vs_delta":<22} {n:>5} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
