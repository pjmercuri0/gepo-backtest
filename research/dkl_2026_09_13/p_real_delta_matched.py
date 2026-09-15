"""Delta-matched P_real (handoff §0.41) vs the canon exact-strike P_real, on the featATM6 frame.

Canon P_real counts, over the trailing WINDOW sessions, how often the name's d-day move crossed
TODAY's percentage distance to the exact strikes. Delta-matched: on each historical day t the
strikes are placed where the same-delta strikes would have been, i.e. today's distance scaled by
sigma_t / sigma_today, and crossings of THAT are counted. Two sigma sources:
  rv : trailing 20-session realized vol from daily closes (runnable live from IBKR closes)
  iv : fitted ATM IV (sig0) per ticker-day from the research fits, forward-filled

Selection, credit (1.08x model), D_ent, k, thr, top-5/day are all unchanged: only (p, q, ro) move.
Prints month-by-month progress and a to-date metrics table after every year.

    python3 research/dkl_2026_09_13/p_real_delta_matched.py [--win IS|OOT] [--fill 1.08]
"""
import sys, time, argparse, warnings
warnings.filterwarnings('ignore')
import numpy as np, pandas as pd
sys.path.insert(0, '.')
import ent_canon as ec
import report_ent_canon as rec
import report_mid_canon as rmc

HERE = 'research/dkl_2026_09_13/'
ap = argparse.ArgumentParser()
ap.add_argument('--win', default='IS'); ap.add_argument('--fill', type=float, default=ec.FILL_MULT)
ap.add_argument('--rv_days', type=int, default=20)
ap.add_argument('--closes', default='store', choices=['store', 'frame'], help='store = output/daily_closes on SPY sessions; frame = the research frame\'s own sparse series (candidate-day underlying + expiry close), min 120 obs as the frame used')
ap.add_argument('--min_obs', type=int, default=None)
a = ap.parse_args()
ec.FILL_MULT = a.fill
T0 = time.time()
def log(*s): print(f'[{time.time()-T0:6.1f}s]', *s, flush=True)

# ── data ────────────────────────────────────────────────────────────────────
C = pd.read_parquet(rec.FRAME)
C = C[C.win == a.win].dropna(subset=['EV']).copy()
C['entry_date'] = pd.to_datetime(C.entry_date).dt.normalize(); C['expiry_date'] = pd.to_datetime(C.expiry_date)
C = C.sort_values(['entry_date', 'ticker', 'short_strike']).reset_index(drop=True)
C['width'] = (C.short_strike - C.long_strike).abs()
log(f'{a.win} candidates {len(C):,}  {C.entry_date.min().date()} .. {C.entry_date.max().date()}')

if a.closes == 'store':
    closes = pd.read_parquet('output/daily_closes.parquet').dropna().drop_duplicates(['ticker', 'date']).sort_values(['ticker', 'date'])
    closes['date'] = pd.to_datetime(closes.date).dt.normalize()
    # daily_closes carries ~8-10 non-session dates per year (vendor holiday republishes); keep SPY sessions only
    _spy = set(pd.to_datetime(pd.read_csv('data/spy_us_d.csv', parse_dates=['Date']).Date).dt.normalize())
    n0 = len(closes); closes = closes[closes.date.isin(_spy)].copy()
    log(f'closes restricted to SPY sessions: dropped {n0-len(closes):,} non-session rows')
else:
    fr = []
    for f in ('is_synth.parquet', 'oot_synth.parquet'):
        s_ = pd.read_parquet(HERE + f, columns=['ticker', 'entry_date', 'entry_price', 'expiry_date', 'expiry_close'])
        fr.append(s_[['ticker', 'entry_date', 'entry_price']].rename(columns={'entry_date': 'date', 'entry_price': 'close'}))
        fr.append(s_[['ticker', 'expiry_date', 'expiry_close']].rename(columns={'expiry_date': 'date', 'expiry_close': 'close'}))
    closes = pd.concat(fr).dropna(); closes['date'] = pd.to_datetime(closes.date).dt.normalize()
    closes = closes.drop_duplicates(['ticker', 'date']).sort_values(['ticker', 'date'])
    if a.min_obs is None: a.min_obs = 120
    log(f'closes = frame series (band_sweep PX): {len(closes):,} rows')
if a.min_obs is not None: ec.MIN_OBS = a.min_obs
log(f'MIN_OBS {ec.MIN_OBS}')

# ATM IV per ticker-day from the research fits (candidate chains), forward-filled on the close calendar
fits = []
for f in ('is_synth.parquet', 'oot_synth.parquet'):
    s = pd.read_parquet(HERE + f, columns=['ticker', 'entry_date', 'sig0']); s['entry_date'] = pd.to_datetime(s.entry_date).dt.normalize()
    fits.append(s.groupby(['ticker', 'entry_date']).sig0.median().reset_index().rename(columns={'entry_date': 'date'}))
IVD = pd.concat(fits).drop_duplicates(['ticker', 'date'])
closes = closes.merge(IVD, on=['ticker', 'date'], how='left')
closes['sig_iv'] = closes.groupby('ticker').sig0.ffill()
lr = np.log(closes.close) - np.log(closes.groupby('ticker').close.shift(1))
closes['sig_rv'] = lr.groupby(closes.ticker).transform(lambda x: x.rolling(a.rv_days).std()) * np.sqrt(252)
log(f'closes {len(closes):,} rows, {closes.ticker.nunique()} tickers; ATM-IV days {closes.sig0.notna().sum():,}, ffilled {closes.sig_iv.notna().sum():,}')

BY = {tk: g.reset_index(drop=True) for tk, g in closes.groupby('ticker')}


def p_real_generic(cands, sig_col=None, window=ec.WINDOW):
    """sig_col None -> canon exact strikes. Else thresholds scale by sigma_t / sigma_today."""
    out = np.full((len(cands), 3), np.nan)
    Cc = cands.reset_index(drop=True)
    for tk, g in Cc.groupby('ticker'):
        s = BY.get(tk)
        if s is None or len(s) < 60:
            continue
        dates = s.date.values.astype('datetime64[ns]'); cl = s.close.values.astype(float)
        sig = s[sig_col].values.astype(float) if sig_col else None
        pos = np.clip(np.searchsorted(dates, g.entry_date.values.astype('datetime64[ns]')), 0, len(cl) - 1)
        for d in (1, 2, 3, 4):
            m = (g.DTE.clip(1, 4).astype(int) == d).values
            if not m.any():
                continue
            R = cl[d:] / cl[:-d] - 1.0
            p0 = pos[m]; sub = g[m]; bp = (sub.spread_type == 'bull_put').values
            ths = (sub.short_strike.values / sub.entry_price.values - 1)[:, None]
            thl = (sub.long_strike.values / sub.entry_price.values - 1)[:, None]
            idx = (p0 - d - 1)[:, None] - np.arange(window)[None, :]; ok = idx >= 0
            ci = np.clip(idx, 0, len(R) - 1)
            r = np.where(ok, R[ci], np.nan)
            if sig is not None:
                s_today = sig[p0][:, None]; s_t = sig[ci]
                scale = s_t / s_today
                ok = ok & np.isfinite(scale)
                ths = ths * scale; thl = thl * scale
            bs_ = np.where(bp[:, None], r <= ths, r >= ths) & ok
            bl = np.where(bp[:, None], r <= thl, r >= thl) & ok
            n = ok.sum(1); ns = bs_.sum(1); nl = bl.sum(1)
            t = np.column_stack([n - ns + ec.PRIOR, nl + ec.PRIOR, ns - nl + ec.PRIOR]).astype(float)
            t = t / t.sum(1, keepdims=True); t[n < ec.MIN_OBS] = np.nan
            out[sub.index.values] = t
    return out


def rescore(df, P):
    df = df.copy(); df['p'], df['q'], df['ro'] = P[:, 0], P[:, 1], P[:, 2]
    b = df.model_credit.values / (df.width.values - df.model_credit.values)
    w, ell = ec.kelly(np.nan_to_num(df.p.values), np.nan_to_num(df.q.values), np.nan_to_num(df.ro.values), b)
    ell[~np.isfinite(df.p.values)] = np.nan
    df['EV'] = np.exp(ell) - 1.0; df['GROUND'] = df.EV * np.exp(-ec.K * df.D_ent)
    return df


def select_book(df):
    sel = (df[df.GROUND >= ec.THR].sort_values(['entry_date', 'GROUND'], ascending=[True, False]).groupby('entry_date').head(ec.TOP_N)).copy()
    sel['credit'] = (sel.model_credit * ec.FILL_MULT).round(4); sel['max_loss_adj'] = (sel.width - sel.credit).round(4)
    sel = sel[sel.max_loss_adj > 0].copy()
    sel['_outcome'] = sel.apply(rec._outcome, axis=1); sel['pnl_per_contract'] = sel.apply(rec._pnl, axis=1)
    sel['max_loss_dollar'] = sel.max_loss_adj * 100; sel['realize_date'] = sel.expiry_date; sel['entry_date_dt'] = sel.entry_date
    sel['DKL'] = sel.D_ent; sel['G'] = np.log1p(sel.EV); sel['w_star'] = np.nan
    return sel.sort_values('entry_date_dt').reset_index(drop=True)


def metrics(sel, label, ref=None, end_year=None):
    s = rmc.build_payload(sel, end_year or sel.expiry_date.max().year, label)['summary']
    oc = sel._outcome.value_counts(normalize=True)
    row = dict(variant=label, n=len(sel), win=round(100 * oc.get('WIN', 0), 1), loss=round(100 * oc.get('LOSS', 0), 1),
               bull_put=round(100 * (sel.spread_type == 'bull_put').mean(), 1), pnl_trade=round(sel.pnl_per_contract.mean(), 2),
               qty1_pnl=round(s['qty1_final'] - 10000), sh_wk=s['qty1_sharpe_weekly'], dd=s['qty1_max_dd'], qty2_pnl=round(s['strategy_final'] - 10000))
    if ref is not None:
        k = ['ticker', 'entry_date', 'spread_type', 'short_strike', 'long_strike']
        row['overlap'] = round(100 * len(sel.merge(ref[k].drop_duplicates(), on=k)) / max(len(sel), 1), 1)
    return row


# ── sanity: frame's own (p,q,ro) -> EV reproduces the frame's EV; exact-strike recompute on daily_closes vs frame ──
chk = rescore(C, C[['p', 'q', 'ro']].values)
log(f'EV reproduction from frame p/q/ro: max |dEV| = {np.nanmax(np.abs(chk.EV - C.EV)):.2e}')

VARIANTS = [('canon (frame p/q/ro)', None), ('exact strikes, daily_closes', None), ('delta-matched, RV20', 'sig_rv'), ('delta-matched, ATM IV', 'sig_iv')]
P = {v[0]: np.full((len(C), 3), np.nan) for v in VARIANTS}
P['canon (frame p/q/ro)'] = C[['p', 'q', 'ro']].values.astype(float)
years = sorted(C.entry_date.dt.year.unique())
pd.set_option('display.width', 250)
for yr in years:
    ym = C[C.entry_date.dt.year == yr]
    months = sorted(ym.entry_date.dt.month.unique())
    for i, mo in enumerate(months, 1):
        idx = ym.index[ym.entry_date.dt.month == mo]
        sub = C.loc[idx]
        P['exact strikes, daily_closes'][idx] = p_real_generic(sub, None)
        P['delta-matched, RV20'][idx] = p_real_generic(sub, 'sig_rv')
        P['delta-matched, ATM IV'][idx] = p_real_generic(sub, 'sig_iv')
        log(f'{yr}  month {mo:2d}  {100*i/len(months):5.1f}%  ({len(idx):,} candidates)')
    done = C.entry_date.dt.year <= yr
    rows = []; ref = None
    for name, _ in VARIANTS:
        sel = select_book(rescore(C[done], P[name][done.values]))
        if ref is None: ref = sel
        rows.append(metrics(sel, name, ref, end_year=yr))
    print(f'\n=== {a.win} to date: {C.entry_date.min().date()} .. {yr}-12-31  (fill {ec.FILL_MULT}x model, comm ${ec.COMMISSION}) ===')
    print(pd.DataFrame(rows).to_string(index=False)); print(flush=True)

# exact vs canon agreement on (p,q,ro)
d = np.abs(P['exact strikes, daily_closes'] - P['canon (frame p/q/ro)'])
log(f'exact-strike recompute vs frame: median |dp| {np.nanmedian(d[:,0]):.4f}, 90th pct {np.nanpercentile(d[:,0], 90):.4f}, NaN rows {np.isnan(P["exact strikes, daily_closes"][:,0]).sum()}')
out = C[['ticker', 'entry_date', 'expiry_date', 'DTE', 'spread_type', 'short_strike', 'long_strike', 'model_credit', 'D_ent', 'expiry_close']].copy()
for name, _ in VARIANTS[1:]:
    tag = {'exact strikes, daily_closes': 'ex', 'delta-matched, RV20': 'rv', 'delta-matched, ATM IV': 'iv'}[name]
    out[f'p_{tag}'], out[f'q_{tag}'], out[f'ro_{tag}'] = P[name].T
out.to_parquet(HERE + f'p_real_delta_matched_{a.win}_{a.closes}.parquet')
log('done')
