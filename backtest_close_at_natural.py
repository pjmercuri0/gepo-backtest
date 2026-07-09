"""Backtest variant: any non-winning pick gets closed at Friday-close NATURAL
debit (short_ask − long_bid). Winners (clean OTM) just expire — no close cost.

Models the realistic "I must close any pick where my short leg is ITM" policy
to eliminate assignment + weekend-gap risk.

P&L per pick (per contract):
  WIN     (spot OTM of short_strike): + entry_credit (let expire, no close cost)
  BETWEEN (short ITM, long OTM):       + entry_credit − natural_close_debit
  MAX_LOSS(both ITM):                  + entry_credit − natural_close_debit  (≈ −max_loss)

natural_close_debit = short_ask − long_bid  (Friday-close BBO of both legs)
                      capped at spread_width (can't pay more than max_loss + credit)
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config

SP100 = set(bt_config.SP100_TICKERS)
YEARS = [2022, 2023, 2024, 2025]
SPY_CSV = 'data/spy_us_d.csv'
START_BANKROLL = 10_000.0
KELLY_FRAC = 0.0625
KELLY_CAP = 5

picks = pd.read_parquet('output/picks_cache_k30.parquet')
print(f'{len(picks):,} picks loaded')

# Need Friday-close BBO + LAST per leg.
year_views = {}
for year in YEARS:
    df = pd.read_parquet(f'output/{year}_sp500_last.parquet')
    df = df[df['Symbol'].isin(SP100)]
    year_views[year] = df.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['BidPrice','AskPrice','LastPrice']]


def lookup_close(row):
    pc = 'put' if row['spread_type']=='bull_put' else 'call'
    year = pd.Timestamp(row['expiry_date']).year
    if year not in year_views: return None, None, None, None, None, None
    dv = year_views[year]
    try:
        sr = dv.loc[(row['expiry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
        lr = dv.loc[(row['expiry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
        if hasattr(sr,'iloc') and sr.ndim>1: sr=sr.iloc[0]
        if hasattr(lr,'iloc') and lr.ndim>1: lr=lr.iloc[0]
        return float(sr['BidPrice']), float(sr['AskPrice']), float(sr['LastPrice']), float(lr['BidPrice']), float(lr['AskPrice']), float(lr['LastPrice'])
    except KeyError:
        return None, None, None, None, None, None


print('Looking up Friday-close BBO + LAST...')
picks[['fri_sb','fri_sa','fri_sl','fri_lb','fri_la','fri_ll']] = picks.apply(lambda r: pd.Series(lookup_close(r)), axis=1)
n_missing = picks['fri_sa'].isna().sum()
print(f'  missing BBO: {n_missing} / {len(picks)} ({100*n_missing/len(picks):.1f}%)')
picks = picks.dropna(subset=['fri_sa','fri_lb']).reset_index(drop=True)
print(f'  after dropping missing: {len(picks):,} picks')


def classify(row):
    spot = row['expiry_close']; ss = row['short_strike']; ls = row['long_strike']
    if row['spread_type'] == 'bull_put':
        if spot >= ss: return 'WIN'
        if spot <= ls: return 'MAX_LOSS'
        return 'BETWEEN'
    else:
        if spot <= ss: return 'WIN'
        if spot >= ls: return 'MAX_LOSS'
        return 'BETWEEN'
picks['outcome'] = picks.apply(classify, axis=1)


def calc_qty(row):
    ws = row.get('w_star'); ml_d = row['max_loss_dollar']
    if ws is None or pd.isna(ws) or ws <= 0 or ml_d <= 0: return 1
    return max(1, min(KELLY_CAP, int(KELLY_FRAC * float(ws) * START_BANKROLL / ml_d)))
picks['qty'] = picks.apply(calc_qty, axis=1)


def pnl_natural(row):
    """Close non-WIN at Friday-close NATURAL debit (short_ask − long_bid)."""
    credit_per_share = row['credit']
    width = row['net_credit'] + row['max_loss']
    if row['outcome'] == 'WIN':
        return credit_per_share * 100
    nat_debit = max(row['fri_sa'] - row['fri_lb'], 0.0)
    nat_debit = min(nat_debit, width)
    return (credit_per_share - nat_debit) * 100


def pnl_115last(row):
    """Close non-WIN at 1.15 × LAST debit (Friday-close LAST clamped to [BID, ASK])."""
    credit_per_share = row['credit']
    width = row['net_credit'] + row['max_loss']
    if row['outcome'] == 'WIN':
        return credit_per_share * 100
    # Clamp LAST to [BID, ASK] per leg
    sl = max(row['fri_sb'], min(row['fri_sl'], row['fri_sa']))
    ll = max(row['fri_lb'], min(row['fri_ll'], row['fri_la']))
    last_debit = max(sl - ll, 0.0) * 1.15
    last_debit = min(last_debit, width)
    return (credit_per_share - last_debit) * 100


def pnl_adaptive(row, ba_threshold=0.50):
    """Close non-WIN at 1.15×LAST when liquidity is good, fall back to natural when wide.
    Liquidity test: (short_BA + long_BA) / spread_width <= ba_threshold."""
    credit_per_share = row['credit']
    width = row['net_credit'] + row['max_loss']
    if row['outcome'] == 'WIN':
        return credit_per_share * 100
    short_ba = row['fri_sa'] - row['fri_sb']
    long_ba  = row['fri_la'] - row['fri_lb']
    ba_ratio = (short_ba + long_ba) / width if width > 0 else 99
    if ba_ratio <= ba_threshold:
        # Good liquidity → 1.15×LAST close
        sl = max(row['fri_sb'], min(row['fri_sl'], row['fri_sa']))
        ll = max(row['fri_lb'], min(row['fri_ll'], row['fri_la']))
        debit = min(max(sl - ll, 0.0) * 1.15, width)
    else:
        # Wide spread → pay natural
        debit = min(max(row['fri_sa'] - row['fri_lb'], 0.0), width)
    return (credit_per_share - debit) * 100


picks['pnl_close_natural'] = picks['qty'] * picks.apply(pnl_natural, axis=1)
picks['pnl_close_115last'] = picks['qty'] * picks.apply(pnl_115last, axis=1)
picks['pnl_adaptive_50']   = picks['qty'] * picks.apply(lambda r: pnl_adaptive(r, 0.50), axis=1)
picks['pnl_adaptive_30']   = picks['qty'] * picks.apply(lambda r: pnl_adaptive(r, 0.30), axis=1)
picks['pnl_canonical']     = picks['qty'] * picks['pnl_per_contract']


print(f'\n══ Outcome distribution ══')
counts = picks['outcome'].value_counts()
for o in ['WIN','BETWEEN','MAX_LOSS']:
    n = int(counts.get(o, 0))
    print(f'  {o:<9} {n:>4} ({100*n/len(picks):>5.1f}%)')


def stats(pnl_series, dates):
    n = len(pnl_series); tot = pnl_series.sum(); mu = pnl_series.mean()
    win = 100*(pnl_series > 0).mean()
    df = pd.DataFrame({'d':pd.to_datetime(dates), 'p':pnl_series})
    daily = df.groupby('d')['p'].sum().sort_index()
    sg = daily.std(ddof=0); sh = (daily.mean()*np.sqrt(252)/sg) if sg>0 else 0
    return n, tot, mu, win, sh


print(f'\n══ Canonical (partials at intrinsic) vs Close-at-Natural ══')
print(f'{"variant":<22} {"trades":>5} {"profit":>10} {"$/tr":>7} {"win %":>6} {"Sharpe":>8}')
print('-'*60)
for name, col in [('Canonical', 'pnl_canonical'),
                   ('Close@1.15×LAST', 'pnl_close_115last'),
                   ('Adaptive (1.15LAST if BA<50%, else nat)', 'pnl_adaptive_50'),
                   ('Adaptive (BA<30% threshold)', 'pnl_adaptive_30'),
                   ('Close@Natural', 'pnl_close_natural')]:
    n, tot, mu, win, sh = stats(picks[col], picks['entry_date'])
    print(f'  {name:<22} {n:>5} ${tot:>+8,.0f} ${mu:>+5.2f} {win:>5.1f}% Sh{sh:>+5.2f}')


# Per-outcome comparison
print(f'\n══ P&L by outcome (close-at-natural policy) ══')
for o in ['WIN','BETWEEN','MAX_LOSS']:
    sub = picks[picks['outcome']==o]
    if sub.empty: continue
    canon_sum = sub['pnl_canonical'].sum()
    last_sum  = sub['pnl_close_115last'].sum()
    nat_sum   = sub['pnl_close_natural'].sum()
    print(f'  {o:<9} n={len(sub):>3}  canon ${canon_sum:>+8,.0f}  1.15×LAST ${last_sum:>+8,.0f}  natural ${nat_sum:>+8,.0f}')

# Equity curve
print(f'\n══ Equity curve (¹⁄₁₆K cap=5, $10k base) ══')
spy = pd.read_csv(SPY_CSV, parse_dates=['Date']).sort_values('Date').reset_index(drop=True)
spy = spy[(spy['Date']>=pd.Timestamp(f'{YEARS[0]}-01-01'))&(spy['Date']<=pd.Timestamp(f'{YEARS[-1]}-12-31'))].reset_index(drop=True)
trading_days = pd.DatetimeIndex(spy['Date'])
n_years = (trading_days[-1]-trading_days[0]).days/365.25

def cagr(final): return ((final/START_BANKROLL)**(1/n_years) - 1) if final > 0 else -1
def max_dd(eq):
    peak = eq.cummax() if hasattr(eq,'cummax') else pd.Series(eq).cummax()
    return float(((eq-peak)/peak).min())
def sharpe(r): return r.mean()*np.sqrt(252)/r.std(ddof=0) if r.std(ddof=0)>0 else 0

picks['realize'] = pd.to_datetime(picks['expiry_date'])
for label, col in [('Canonical', 'pnl_canonical'),
                    ('Close@1.15LAST', 'pnl_close_115last'),
                    ('Adaptive(50%)', 'pnl_adaptive_50'),
                    ('Adaptive(30%)', 'pnl_adaptive_30'),
                    ('Close@Natural', 'pnl_close_natural')]:
    daily = picks.groupby('realize')[col].sum().sort_index()
    eq = START_BANKROLL + daily.reindex(trading_days, fill_value=0.0).cumsum()
    r = eq.diff().fillna(0)/eq.shift(1).fillna(START_BANKROLL)
    print(f'  {label:<14}  ${eq.iloc[-1]:>9,.0f}  CAGR {cagr(eq.iloc[-1])*100:+5.1f}%  Sharpe {sharpe(r):+.2f}  MaxDD {max_dd(eq)*100:.1f}%')
