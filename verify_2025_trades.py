"""Spot-check select 2025 trades from the canonical backtest.
Picks: top-3 winners, top-3 losers, a random middle, a max-loss case.
For each, show entry data + Friday close + computed vs expected P&L.
"""
import sys, math
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import config as bt_config
import spreads, ground

SP100 = set(bt_config.SP100_TICKERS)
SPY_CSV = 'data/spy_us_d.csv'
MULT = 0.85
YEAR = 2025

print(f'Loading {YEAR} parquet...')
df_full = pd.read_parquet(f'output/{YEAR}_sp500_last.parquet')
df_full = df_full[df_full['Symbol'].isin(SP100)]
expiry_close = (df_full[df_full['DataDate']==df_full['ExpirationDate']]
                .groupby(['Symbol','ExpirationDate'])['UnderlyingPrice']
                .first().to_dict())
df_idx = df_full.set_index(['DataDate','Symbol','ExpirationDate','StrikePrice','PutCall']).sort_index()[['LastPrice','BidPrice','AskPrice','UnderlyingPrice']]

df = df_full.copy()
df['dow']     = df['DataDate'].dt.dayofweek
df['exp_dow'] = df['ExpirationDate'].dt.dayofweek
df = df[df['dow'].isin([0,1,2,3])]
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
    return (math.exp(G)-1.0) * math.exp(-ground.DKL_K * DKL)
scored['GROUND'] = scored.apply(gnd, axis=1)
scored = scored.dropna(subset=['GROUND'])

sel = scored[scored['GROUND'] >= 0.001]
sel = sel.sort_values(['entry_date','GROUND'], ascending=[True,False]).groupby('entry_date').head(5)
sel = sel.copy()

def lookup(row):
    pc = 'put' if row['spread_type']=='bull_put' else 'call'
    try:
        sr = df_idx.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['short_strike'], pc)]
        lr = df_idx.loc[(row['entry_date'], row['ticker'], row['expiry_date'], row['long_strike'],  pc)]
        if isinstance(sr, pd.DataFrame): sr = sr.iloc[0]
        if isinstance(lr, pd.DataFrame): lr = lr.iloc[0]
        last_cr = max(float(sr['LastPrice']) - float(lr['LastPrice']), 0)
        return last_cr, expiry_close.get((row['ticker'], row['expiry_date'])), float(sr['UnderlyingPrice']), float(sr['LastPrice']), float(lr['LastPrice']), float(sr['BidPrice']), float(sr['AskPrice']), float(lr['BidPrice']), float(lr['AskPrice'])
    except KeyError:
        return None,None,None,None,None,None,None,None,None

print('Looking up entry/expiry data for each pick...')
cols = ['raw_last','expiry_close','spot_at_entry','short_last','long_last','short_bid','short_ask','long_bid','long_ask']
sel[cols] = sel.apply(lambda r: pd.Series(lookup(r)), axis=1)
ok = sel.dropna(subset=['raw_last','expiry_close']).copy()
ok['credit'] = ok['raw_last'] * MULT
ok['width']  = ok['net_credit'] + ok['max_loss']
ok['max_loss_adj'] = ok['width'] - ok['credit']
ok['pnl'] = ok.apply(lambda r: spreads.calc_pnl(
    r['expiry_close'], r['short_strike'], r['long_strike'],
    r['credit'], r['max_loss_adj'], r['spread_type']), axis=1) * 100

# Select diverse picks
def spot_check(row, label):
    print(f'\n── {label} ──')
    print(f'  Entry: {row["entry_date"].date()} {row["ticker"]} {row["spread_type"]} {row["short_strike"]:.2f}/{row["long_strike"]:.2f}')
    print(f'  Expiry: {row["expiry_date"].date() if hasattr(row["expiry_date"],"date") else row["expiry_date"]}  DTE={int(row["DTE"])}')
    print(f'  Underlying at entry: ${row["spot_at_entry"]:.2f}  at expiry: ${row["expiry_close"]:.2f}  move: {(row["expiry_close"]-row["spot_at_entry"])/row["spot_at_entry"]*100:+.2f}%')
    print(f'  Short leg: bid=${row["short_bid"]:.3f} ask=${row["short_ask"]:.3f} mid=${(row["short_bid"]+row["short_ask"])/2:.3f} LAST=${row["short_last"]:.3f}')
    print(f'  Long  leg: bid=${row["long_bid"]:.3f} ask=${row["long_ask"]:.3f} mid=${(row["long_bid"]+row["long_ask"])/2:.3f} LAST=${row["long_last"]:.3f}')
    print(f'  Net credit (LAST): ${row["raw_last"]:.4f}  ×0.85 = ${row["credit"]:.4f}')
    print(f'  Spread width: ${row["width"]:.2f}  Max loss: ${row["max_loss_adj"]:.4f}')
    print(f'  GROUND: {row["GROUND"]:.4f}')
    # Manual P&L check
    pc = row["spread_type"]
    sp = row["short_strike"]; lp = row["long_strike"]; spot = row["expiry_close"]
    cr = row["credit"]; ml = row["max_loss_adj"]
    if pc == "bull_put":
        if spot >= sp:    label2, manual_pps = "WIN  (OTM)", cr
        elif spot <= lp:  label2, manual_pps = "MAX LOSS (deep ITM)", -ml
        else:             label2, manual_pps = "PARTIAL (in spread)", cr - (sp - spot)
    else:  # bear_call
        if spot <= sp:    label2, manual_pps = "WIN  (OTM)", cr
        elif spot >= lp:  label2, manual_pps = "MAX LOSS (deep ITM)", -ml
        else:             label2, manual_pps = "PARTIAL (in spread)", cr - (spot - sp)
    manual_pnl = manual_pps * 100
    print(f'  Outcome: {label2}')
    print(f'  Manual P&L: ${manual_pnl:+.2f}  Backtest P&L: ${row["pnl"]:+.2f}  match={abs(manual_pnl-row["pnl"])<0.01}')


winners = ok.sort_values('pnl', ascending=False).head(3)
losers  = ok.sort_values('pnl', ascending=True).head(3)
maxloss = ok[ok['pnl'] == ok['pnl'].min()].head(1)
middle  = ok.iloc[len(ok)//2:len(ok)//2+1]
random  = ok.sample(2, random_state=42)

for i, (_, r) in enumerate(winners.iterrows()):
    spot_check(r, f'TOP WINNER #{i+1}')
for i, (_, r) in enumerate(losers.iterrows()):
    spot_check(r, f'TOP LOSER #{i+1}')
spot_check(middle.iloc[0], 'MIDDLE PICK')
for i, (_, r) in enumerate(random.iterrows()):
    spot_check(r, f'RANDOM SAMPLE #{i+1}')

print(f'\n══ Summary ══')
print(f'  {len(ok)} picks total, ${ok["pnl"].sum():+,.0f} total P&L, {100*(ok["pnl"]>0).mean():.1f}% win rate')
print(f'  Best win: ${ok["pnl"].max():+.2f}  Worst loss: ${ok["pnl"].min():+.2f}')
