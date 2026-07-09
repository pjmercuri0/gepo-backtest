"""Year-by-year and combined results at canonical config:
- k=30, top-5
- Per-day thresholds: Mon=0.003, Tue=0.005, Wed=0.010, Thu=0.010
- Realization: 0.85 × clamped LAST
- Sizing: qty=2 (2 contracts per pick)
- SP100, Mon-Thu, Friday expiry, DTE 1-4

Also writes equity curve to live/data/backtest_equity.json so the webapp
backtest tab reflects this config.
"""
import sys, math, json, os
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
K_VAL = 30
QTY = 2
START_BANKROLL = 10_000.0
THRESH_BY_DOW = {0: 0.003, 1: 0.005, 3: 0.010}  # Wed (DOW=2) dropped
DOW_NAMES = {0:'Mon', 1:'Tue', 3:'Thu'}
ACTIVE_DOWS = [0, 1, 3]
OUT_PATH = 'live/data/backtest_equity.json'


def score_year(year):
    df_full = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df_full = df_full[df_full['Symbol'].isin(SP100)]
    expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                    .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                    .first().to_dict())
    df_idx_view = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice']]
    df = df_full.copy()
    df['dow']     = df['DataDate'].dt.dayofweek
    df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
    df = df[df['dow'].isin(ACTIVE_DOWS)]
    df = df[df['exp_dow']==4]
    df = df[(df['DTE']>=1)&(df['DTE']<=4)]
    df = df[df['LastPrice'].astype(float) > 0]
    df = df.copy()
    df['AbsDelta'] = df['Delta'].abs()
    df['MidPrice'] = (df['BidPrice'] + df['AskPrice']) / 2.0

    spreads.REGIME_LOOKUP = spreads.build_regime_lookup(SPY_CSV, sma_window=100)
    spreads.REGIME_FILTER = True
    spreads.REGIME_PER_TICKER = False
    spreads.GAP_FILTER = False
    spreads.LOW_VIX_BULLPUT_FILTER = False
    spreads.SLIPPAGE_CENTS = 0.0
    bt_config.MIN_OPEN_INTEREST = 100

    candidates = spreads.build_candidates(df)
    scored = ground.score_candidates(candidates)
    def gnd(r):
        G, DKL = r.get('G'), r.get('DKL')
        if G is None or DKL is None or pd.isna(G) or pd.isna(DKL): return float('nan')
        return (math.exp(G)-1.0) * math.exp(-K_VAL * DKL)
    scored['GROUND'] = scored.apply(gnd, axis=1)
    return scored.dropna(subset=['GROUND']), df_idx_view, expiry_close


def realize_with_perday_thresh(scored, df_idx_view, expiry_close, thresh_map):
    s = scored.copy()
    s['entry_dow'] = pd.to_datetime(s['entry_date']).dt.dayofweek
    parts = []
    for dow in ACTIVE_DOWS:
        thr = thresh_map.get(dow, 0.001)
        sub = s[s['entry_dow'] == dow]
        qual = sub[sub['GROUND'] >= thr]
        top = qual.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
        parts.append(top)
    sel = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if sel.empty:
        return pd.DataFrame(columns=['entry_date','realize_date','pnl','dow'])
    def lookup(row):
        pc = 'put' if row['spread_type']=='bull_put' else 'call'
        try:
            sr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
            lr = df_idx_view.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
            if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
            if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
            sl = max(float(sr['BidPrice']), min(float(sr['LastPrice']), float(sr['AskPrice'])))
            ll = max(float(lr['BidPrice']), min(float(lr['LastPrice']), float(lr['AskPrice'])))
            return max(sl - ll, 0), expiry_close.get((row['ticker'], row['expiry_date']))
        except KeyError:
            return None, None
    sel['raw_last'], sel['expiry_close'] = zip(*sel.apply(lookup, axis=1))
    ok = sel.dropna(subset=['raw_last','expiry_close']).copy()
    ok['credit'] = ok['raw_last'] * 0.85
    ok['width']  = ok['net_credit'] + ok['max_loss']
    ok['max_loss_adj'] = ok['width'] - ok['credit']
    ok['pnl_per_contract'] = ok.apply(lambda r: spreads.calc_pnl(
        r['expiry_close'], r['short_strike'], r['long_strike'],
        r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100
    ok['pnl'] = ok['pnl_per_contract'] * QTY
    ok['realize_date'] = pd.to_datetime(ok['expiry_date'])
    ok['dow'] = pd.to_datetime(ok['entry_date']).dt.dayofweek
    return ok


def stats(ok):
    if ok.empty: return 0, 0.0, 0.0, 0.0, 0.0
    n = len(ok); tot = ok['pnl'].sum(); mu = ok['pnl'].mean()
    win = 100*(ok['pnl']>0).mean()
    daily = ok.groupby('entry_date')['pnl'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg > 0 else 0
    return n, tot, mu, win, sh


print(f'CONFIG: k={K_VAL}, top-5, per-day thresholds, qty={QTY}, 0.85×clamped LAST\n')

year_pnl = {}
for year in YEARS:
    print(f'── scoring {year} ──', flush=True)
    scored, df_idx_view, expiry_close = score_year(year)
    ok = realize_with_perday_thresh(scored, df_idx_view, expiry_close, THRESH_BY_DOW)
    year_pnl[year] = ok

print(f'\n══ Year × day breakdown (qty={QTY}) ══')
print(f'{"year":<6} {"day":<5} {"thr":<7} {"tr":>4} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
print('-'*60)
for year in YEARS:
    ok = year_pnl[year]
    for d in ACTIVE_DOWS:
        sub = ok[ok['dow']==d]
        n,tot,mu,win,sh = stats(sub)
        thr = THRESH_BY_DOW[d]
        print(f'{year:<6} {DOW_NAMES[d]:<5} {thr:<7.4f} {n:>4} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
    n,tot,mu,win,sh = stats(ok)
    print(f'{year:<6} {"ALL":<5} {"-":<7} {n:>4} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
    print('-'*60)

# Combined per-day
all_ok = pd.concat(year_pnl.values(), ignore_index=True)
print(f'\n══ 2022-2025 combined per-day (qty={QTY}) ══')
print(f'{"day":<5} {"thr":<7} {"tr":>4} {"profit":>9} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
print('-'*54)
for d in ACTIVE_DOWS:
    sub = all_ok[all_ok['dow']==d]
    n,tot,mu,win,sh = stats(sub)
    thr = THRESH_BY_DOW[d]
    print(f'{DOW_NAMES[d]:<5} {thr:<7.4f} {n:>4} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')
n,tot,mu,win,sh = stats(all_ok)
print(f'{"ALL":<5} {"-":<7} {n:>4} ${tot:>+7,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')

# Equity curve
spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
spy = spy[(spy['Date']>=pd.Timestamp(f'{YEARS[0]}-01-01'))&(spy['Date']<=pd.Timestamp(f'{YEARS[-1]}-12-31'))].reset_index(drop=True)
trading_days = pd.DatetimeIndex(spy['Date'])

daily = all_ok.groupby('realize_date')['pnl'].sum().sort_index()
strategy_equity = START_BANKROLL + daily.reindex(trading_days, fill_value=0.0).cumsum()
spy_normalized = spy['Close'].values / spy['Close'].iloc[0]
spy_equity = START_BANKROLL * spy_normalized

def sharpe(ret): return ret.mean()*np.sqrt(252)/ret.std(ddof=0) if ret.std(ddof=0) > 0 else 0.0
def max_dd(eq):
    peak = eq.cummax() if hasattr(eq,'cummax') else pd.Series(eq).cummax()
    return float(((eq-peak)/peak).min())
def cagr(final, n):
    return ((final/START_BANKROLL)**(1/n) - 1) if final > 0 else -1

strategy_returns = strategy_equity.diff().fillna(0) / strategy_equity.shift(1).fillna(START_BANKROLL)
spy_returns      = pd.Series(spy_equity).diff().fillna(0) / pd.Series(spy_equity).shift(1).fillna(START_BANKROLL)
n_years = (trading_days[-1] - trading_days[0]).days / 365.25

points = []
for i, d in enumerate(trading_days):
    points.append({
        'date':     d.strftime('%Y-%m-%d'),
        'strategy': round(float(strategy_equity.iloc[i]), 2),
        'spy':      round(float(spy_equity[i]), 2),
    })

summary = {
    'window_start':    trading_days[0].strftime('%Y-%m-%d'),
    'window_end':      trading_days[-1].strftime('%Y-%m-%d'),
    'years':           round(n_years, 2),
    'trading_days':    len(trading_days),
    'n_trades':        int(len(all_ok)),
    'qty':             QTY,
    'strategy_final':  round(float(strategy_equity.iloc[-1]), 2),
    'spy_final':       round(float(spy_equity[-1]), 2),
    'strategy_total_return': round(float((strategy_equity.iloc[-1] - START_BANKROLL) / START_BANKROLL * 100), 2),
    'spy_total_return':      round(float((spy_equity[-1] - START_BANKROLL) / START_BANKROLL * 100), 2),
    'strategy_cagr':   round(float(cagr(strategy_equity.iloc[-1], n_years) * 100), 2),
    'spy_cagr':        round(float(cagr(spy_equity[-1], n_years) * 100), 2),
    'strategy_sharpe': round(float(sharpe(strategy_returns)), 2),
    'spy_sharpe':      round(float(sharpe(spy_returns)), 2),
    'strategy_max_dd': round(float(max_dd(strategy_equity)) * 100, 2),
    'spy_max_dd':      round(float(max_dd(spy_equity)) * 100, 2),
}

payload = {
    'config': {
        'universe':   'SP100',
        'days':       'Mon, Tue, Thu (Wed dropped)',
        'expiry':     'Friday (DTE 1, 3, 4)',
        'selection':  f'top-5 per day, k={K_VAL}, per-day GROUND thresholds (Mon 0.003 / Tue 0.005 / Thu 0.010)',
        'scoring':    'LAST credit (clamped to BBO)',
        'fill_basis': '0.85×clamped LAST (15% haircut)',
        'regime':     'SPY 100d SMA',
        'vol_gate':   'OFF',
        'qty':        QTY,
        'starting_bankroll': START_BANKROLL,
    },
    'summary': summary,
    'points':  points,
}

os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
with open(OUT_PATH, 'w') as f:
    json.dump(payload, f, indent=2)
print(f'\n══ Equity curve (qty={QTY}) ══')
print(f'  Strategy ${summary["strategy_final"]:,.0f}  ({summary["strategy_total_return"]:+.1f}%)  CAGR {summary["strategy_cagr"]:+.1f}%  Sharpe {summary["strategy_sharpe"]:+.2f}  MaxDD {summary["strategy_max_dd"]:.1f}%')
print(f'  SPY      ${summary["spy_final"]:,.0f}  ({summary["spy_total_return"]:+.1f}%)  CAGR {summary["spy_cagr"]:+.1f}%  Sharpe {summary["spy_sharpe"]:+.2f}  MaxDD {summary["spy_max_dd"]:.1f}%')
print(f'\nWrote {OUT_PATH}')
