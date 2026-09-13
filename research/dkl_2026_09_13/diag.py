"""What predicts LOSS beyond P_emp, in the real-credit world? Market-implied triples,
market-vs-history divergences (both directions, signed/unsigned), entropy references,
plus a numpy logistic regression on the valid candidate set and on the top-20/day margin."""
import sys, math, time
import numpy as np, pandas as pd
sys.path.insert(0, '/private/tmp/claude-501/-Users-mercurio-Downloads-gepo-backtest/74cb077b-eb69-4075-bfe1-766ea8fc2c15/scratchpad')
from dkl_eval import *   # noqa
from math import erf
T0 = time.time()
pd.set_option('display.width', 230)
fmt = lambda x: f'{x:.2f}'


def ncdf(x):
    return 0.5 * (1 + np.vectorize(erf)(x / math.sqrt(2)))


def iv_triple(C, ivs, ivl):
    T = np.clip(C.DTE.values, 1, None) / 365.0; sT = np.sqrt(T)
    S, Ks, Kl = C.entry_price.values, C.short_strike.values, C.long_strike.values
    ok = (ivs > 0) & (ivl > 0)
    ivs_ = np.where(ok, ivs, 0.3); ivl_ = np.where(ok, ivl, 0.3)
    d2s = (np.log(S / Ks) - 0.5 * ivs_ ** 2 * T) / (ivs_ * sT)
    d2l = (np.log(S / Kl) - 0.5 * ivl_ ** 2 * T) / (ivl_ * sT)
    bp = (C.spread_type == 'bull_put').values
    ps = np.where(bp, ncdf(-d2s), ncdf(d2s)); pl = np.where(bp, ncdf(-d2l), ncdf(d2l))
    pl = np.minimum(pl, ps)
    t = np.stack([1 - ps, pl, ps - pl], 1); t = t / t.sum(1, keepdims=True)
    t[~ok] = np.nan
    return t


def add_market(C):
    C = C.copy()
    Qiv = iv_triple(C, C.IV.values, C.long_IV.fillna(C.IV).values)
    Qd = np.stack([1 - C.d_sh.values, C.d_lg.values, np.clip(C.d_sh.values - C.d_lg.values, 1e-9, None)], 1)
    Qd = Qd / Qd.sum(1, keepdims=True)
    Pe = C[['p', 'q', 'ro']].values
    U = np.full_like(Pe, 1 / 3)
    C['ls_iv'] = Qiv[:, 1] + 0.5 * Qiv[:, 2]; C['ls_d'] = Qd[:, 1] + 0.5 * Qd[:, 2]
    C['ls_emp'] = Pe[:, 1] + 0.5 * Pe[:, 2]
    C['D_emp_iv'] = kl(Pe, Qiv); C['D_iv_emp'] = kl(Qiv, Pe)
    C['D_emp_d'] = kl(Pe, Qd); C['D_d_emp'] = kl(Qd, Pe)
    mk_riskier_iv = C.ls_iv > C.ls_emp; mk_riskier_d = C.ls_d > C.ls_emp
    C['D_emp_iv_S'] = np.where(mk_riskier_iv, C.D_emp_iv, 0.0); C['D_iv_emp_S'] = np.where(mk_riskier_iv, C.D_iv_emp, 0.0)
    C['D_emp_d_S'] = np.where(mk_riskier_d, C.D_emp_d, 0.0); C['D_d_emp_S'] = np.where(mk_riskier_d, C.D_d_emp, 0.0)
    # history riskier than market (the other sign)
    C['D_emp_iv_H'] = np.where(~mk_riskier_iv, C.D_emp_iv, 0.0); C['D_emp_d_H'] = np.where(~mk_riskier_d, C.D_emp_d, 0.0)
    C['D_iv_unif'] = kl(Qiv, U); C['D_d_unif'] = kl(Qd, U); C['D_emp_unif'] = kl(Pe, U)
    C['b'] = C.net_credit / C.max_loss; C['logb'] = np.log(C.b)
    C['otm'] = np.where(C.spread_type == 'bull_put', 100 * (C.entry_price - C.short_strike) / C.entry_price,
                        100 * (C.short_strike - C.entry_price) / C.entry_price)
    C['bull'] = (C.spread_type == 'bull_put').astype(float)
    C['frac_iv'] = np.where(mk_riskier_iv, 1.0, 0.0)
    ivr = pd.read_parquet(f'{ROOT}/output/iv_rank.parquet'); ivr['DataDate'] = pd.to_datetime(ivr.DataDate)
    ivr = ivr.rename(columns={'Symbol': 'ticker', 'DataDate': 'entry_date'})
    C = C.merge(ivr[['ticker', 'entry_date', 'atm_iv', 'iv_rank']], on=['ticker', 'entry_date'], how='left')
    spy = pd.read_csv(f'{ROOT}/data/spy_us_d.csv', parse_dates=['Date']).sort_values('Date').set_index('Date')['Close']
    r = np.log(spy).diff()
    feat = pd.DataFrame({'spy_r5': r.rolling(5).sum(), 'spy_rv20': r.rolling(20).std() * np.sqrt(252)}).shift(1)
    feat.index.name = 'entry_date'
    C = C.merge(feat, left_on='entry_date', right_index=True, how='left')
    return C


def logit_fit(X, y, names, ridge=1e-3, it=50):
    X = np.column_stack([np.ones(len(X)), X]); w = np.zeros(X.shape[1])
    for _ in range(it):
        p = 1 / (1 + np.exp(-X @ w)); W = p * (1 - p)
        H = X.T @ (X * W[:, None]) + ridge * np.eye(X.shape[1]); g = X.T @ (y - p) - ridge * w
        w = w + np.linalg.solve(H, g)
    p = 1 / (1 + np.exp(-X @ w)); W = p * (1 - p)
    cov = np.linalg.inv(X.T @ (X * W[:, None]) + ridge * np.eye(X.shape[1]))
    se = np.sqrt(np.diag(cov))
    return pd.DataFrame({'coef': w[1:], 'z': (w / se)[1:]}, index=names).round(3)


def regress(C, label):
    feats = ['ls_emp', 'ls_iv', 'ls_d', 'logb', 'd_sh', 'otm', 'iv_rank', 'DTE', 'bull', 'width',
             'D_shrink', 'D_est', 'D_shiftG2', 'D_breach13_u', 'spy_r5', 'spy_rv20', 'D_emp_iv', 'D_iv_unif']
    d = C.dropna(subset=feats + ['EV']).copy()
    for c in ('ls_emp', 'ls_iv', 'ls_d'):
        d[c] = np.log(np.clip(d[c], 1e-4, 1 - 1e-4) / (1 - np.clip(d[c], 1e-4, 1 - 1e-4)))
    X = d[feats].values; X = (X - X.mean(0)) / (X.std(0) + 1e-12)
    print(f'\n--- logistic: is_loss ~ features, {label}, n={len(d):,}, base loss {d.is_loss.mean():.3f} ---')
    print(logit_fit(X, d.is_loss.values, feats).sort_values('z', key=abs, ascending=False).to_string())


def calib_by(C, col, label, nb=10):
    d = C.dropna(subset=['EV', col]).copy()
    d['credit'] = (d.net_credit * FILL).round(4); d['ml'] = (d.width - d.credit).round(4)
    d['pnl'] = pnl_of(d, d.credit.values, d.ml.values)
    qb = pd.qcut(d[col].rank(method='first'), nb, labels=False)
    g = d.groupby(qb).agg(lo=(col, 'min'), hi=(col, 'max'), n=('pnl', 'size'), loss=('is_loss', 'mean'),
                          pred_q=('q', 'mean'), ls_iv=('ls_iv', 'mean'), realized_ls=('loss_share', 'mean'),
                          pnl=('pnl', 'mean'), EV=('EV', 'mean'))
    print(f'\n--- calibration by {col} deciles ({label}) ---'); print(g.round(4).to_string())


if __name__ == '__main__':
    IS = add_market(pd.read_parquet(f'{SCR}/feat_IS.parquet'))
    OO = add_market(pd.read_parquet(f'{SCR}/feat_OOT.parquet'))
    IS.to_parquet(f'{SCR}/feat2_IS.parquet'); OO.to_parquet(f'{SCR}/feat2_OOT.parquet')
    v = IS.dropna(subset=['EV'])
    print(f'valid IS {len(v):,}; market riskier than history (iv) on {v.frac_iv.mean():.1%}; '
          f'mean ls_emp {v.ls_emp.mean():.4f} ls_iv {v.ls_iv.mean():.4f} ls_d {v.ls_d.mean():.4f} realized {v.loss_share.mean():.4f}')
    calib_by(IS, 'b', 'IS valid'); calib_by(IS, 'EV', 'IS valid')
    calib_by(IS, 'ls_iv', 'IS valid'); calib_by(IS, 'D_emp_iv', 'IS valid')
    regress(IS, 'IS all valid')
    top = IS.dropna(subset=['EV']).sort_values(['entry_date', 'EV'], ascending=[True, False]).groupby('entry_date').head(20)
    regress(top, 'IS top-20/day by EV')
    regress(OO, 'OOT all valid')
    for dc in ['D_emp_iv', 'D_iv_emp', 'D_emp_iv_S', 'D_iv_emp_S', 'D_emp_iv_H', 'D_emp_d', 'D_emp_d_S', 'D_emp_d_H',
               'D_iv_unif', 'D_d_unif', 'D_emp_unif']:
        print(f'\n=== {dc} ===')
        print('IS :', quintile_table(IS, dc)); print('OOT:', quintile_table(OO, dc))
        print(sweep(IS, OO, dc).to_string(index=False, float_format=fmt))
    print(f'\nDONE {time.time()-T0:.0f}s')
