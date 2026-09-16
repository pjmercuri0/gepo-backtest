"""Checks for the 52/104-expiry P_real variants vs the canon 252-session P_real, ALL on full-session closes:
fill sensitivity, k x thr sweep, direction split. IS and OOT. Reads the per-candidate p/q/ro
parquets written by p_real_delta_matched.py (--closes store) and featATM6 for the canon."""
import sys, warnings; warnings.filterwarnings('ignore'); sys.path.insert(0, '.')
import numpy as np, pandas as pd, ent_canon as ec, report_ent_canon as rec, report_mid_canon as rmc
pd.set_option('display.width', 250)
KEY = ['ticker', 'entry_date', 'spread_type', 'short_strike', 'long_strike', 'DTE']

def load(win):
    C = pd.read_parquet(rec.FRAME); C = C[C.win == win].dropna(subset=['EV']).copy()
    C['entry_date'] = pd.to_datetime(C.entry_date).dt.normalize(); C['expiry_date'] = pd.to_datetime(C.expiry_date); C['width'] = (C.short_strike - C.long_strike).abs()
    S = pd.read_parquet(f'research/dkl_2026_09_13/p_real_delta_matched_{win}_store.parquet')[KEY + ['p_ex', 'q_ex', 'ro_ex', 'p_e52', 'q_e52', 'ro_e52', 'p_e104', 'q_e104', 'ro_e104']]
    S['entry_date'] = pd.to_datetime(S.entry_date).dt.normalize()
    M = C.merge(S, on=KEY, how='left'); assert M.p_e104.notna().sum() > 0.9 * len(M), 'merge failed'
    return M

def book(F, p, q, ro, k, thr, fill):
    b = F.model_credit.values / (F.width.values - F.model_credit.values)
    w, ell = ec.kelly(np.nan_to_num(p), np.nan_to_num(q), np.nan_to_num(ro), b); ell[~np.isfinite(p)] = np.nan
    d = F.copy(); d['EV'] = np.exp(ell) - 1; d['GROUND'] = d.EV * np.exp(-k * d.D_ent)
    sel = d[d.GROUND >= thr].sort_values(['entry_date', 'GROUND'], ascending=[True, False]).groupby('entry_date').head(5).copy()
    sel['credit'] = (sel.model_credit * fill).round(4); sel['max_loss_adj'] = (sel.width - sel.credit).round(4); sel = sel[sel.max_loss_adj > 0].copy()
    sel['_outcome'] = sel.apply(rec._outcome, axis=1); sel['pnl_per_contract'] = sel.apply(rec._pnl, axis=1)
    sel['max_loss_dollar'] = sel.max_loss_adj * 100; sel['realize_date'] = sel.expiry_date; sel['entry_date_dt'] = sel.entry_date
    sel['DKL'] = sel.D_ent; sel['G'] = np.log1p(sel.EV); sel['w_star'] = np.nan
    return sel.sort_values('entry_date_dt').reset_index(drop=True)

def st(sel, ey):
    if len(sel) < 20: return dict(n=len(sel))
    s = rmc.build_payload(sel, ey, '')['summary']; wag = sel.max_loss_dollar.sum(); pnl = sel.pnl_per_contract.sum()
    return dict(n=len(sel), pnl=round(pnl), yld=round(100 * pnl / wag, 2), sh=s['qty1_sharpe_weekly'], dd=s['qty1_max_dd'])

def run_checks():
  for win, ey in (('IS', 2025), ('OOT', 2026)):
      F = load(win)
      V = {'canon 252s': (F.p_ex.values, F.q_ex.values, F.ro_ex.values), '52 exp': (F.p_e52.values, F.q_e52.values, F.ro_e52.values), '104 exp': (F.p_e104.values, F.q_e104.values, F.ro_e104.values)}   # all full-session
      print(f'\n######## {win}: fill sensitivity (k=1, thr=0.01)')
      rows = []
      for fill in (1.00, 1.02, 1.04, 1.06, 1.08, 1.10):
          r = {'fill': fill}
          for name, (p, q, ro) in V.items():
              x = st(book(F, p, q, ro, ec.K, ec.THR, fill), ey); r.update({f'{name} {k}': v for k, v in x.items()})
          rows.append(r)
      print(pd.DataFrame(rows).to_string(index=False))
      print(f'\n######## {win}: k x thr sweep at fill 1.08 (cells: Sharpe / DD% / n)')
      for name, (p, q, ro) in V.items():
          print(f'-- {name}')
          tab = {}
          for k in (0.0, 0.5, 1.0, 1.5, 2.0):
              row = {}
              for thr in (0.005, 0.01, 0.015, 0.02, 0.03):
                  x = st(book(F, p, q, ro, k, thr, 1.08), ey); row[thr] = f"{x.get('sh','-')}/{x.get('dd','-')}/{x['n']}"
              tab[k] = row
          print(pd.DataFrame(tab).T.rename_axis('k \\ thr').to_string())
      print(f'\n######## {win}: direction split at canon cell (k=1, thr=0.01, fill 1.08)')
      for name, (p, q, ro) in V.items():
          sel = book(F, p, q, ro, ec.K, ec.THR, 1.08)
          for side, g in sel.groupby('spread_type'):
              x = st(g.reset_index(drop=True), ey); print(f'   {name:22s} {side:9s} n={x["n"]:4d} share={100*len(g)/len(sel):4.1f}%  pnl=${x.get("pnl",0):>6}  yield={x.get("yld","-")}%  Sh={x.get("sh","-")}  DD={x.get("dd","-")}%')


if __name__ == '__main__':
    run_checks()
