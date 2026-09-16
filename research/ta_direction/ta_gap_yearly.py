"""Yearly stats, canon vs canon + market-gap drift (gamma 1). 2021-2025 walk-forward (beta fitted on earlier
years only; 2021 has just 2020-07..12 to fit on), 2026 beta frozen from all of 2020-25. 2020 has no prior data.

    python3 research/ta_direction/ta_gap_yearly.py <bars2026_dir>
"""
import sys, io, contextlib
exec(compile(open('research/ta_direction/ta_predict_oot.py').read().split("rows = []\nfor feat in")[0], 'ta_predict_oot.py[1-3]', 'exec'))
x = np.full(len(C), np.nan); x[hit] = MKALL['mkt_gap'].values[pp[hit]]
MU = np.zeros(len(C)); BETA = {}
for Yr in (2021, 2022, 2023, 2024, 2025, 2026):
    tr = TRAIN_OK & np.isfinite(x) & (YC < Yr)
    m, s = x[tr].mean(), x[tr].std(); z = np.clip((x - m) / s, -4, 4); yc = Y[tr] - Y[tr].mean(); b_ = (z[tr] @ yc) / (z[tr] @ z[tr])
    te = YC == Yr; MU[te] = np.nan_to_num(b_ * z[te] * SDD[te] * np.sqrt(DSESS[te])); BETA[Yr] = (round(b_, 4), int(tr.sum()))
ALLM = ISM | OOTM
S_B = book_mask(G0v, ALLM); S_G = book_mask(ground(MU), ALLM)
def m_(sel, ey):
    with contextlib.redirect_stdout(io.StringIO()): return metr(C[sel], ey)
PW = (C._outcome.values == 'WIN') | ((C._outcome.values == 'PARTIAL') & (PN > 0))   # win incl. partial wins (P&L > 0 after haircut)
def row(lab, beta, M, ey):
    b, g = m_(S_B & M, ey), m_(S_G & M, ey)
    return dict(period=lab, beta=beta, n_c=b['n'], n_g=g['n'], pnl_c=b['pnl'], pnl_g=g['pnl'], dPnL=g['pnl'] - b['pnl'],
                win_c=b['win'], win_g=g['win'], winp_c=round(100 * PW[S_B & M].mean(), 1), winp_g=round(100 * PW[S_G & M].mean(), 1),
                yld_c=b['yld'], yld_g=g['yld'], sh_c=b['sh'], sh_g=g['sh'], dd_c=b['dd'], dd_g=g['dd'])
print('partial outcomes with P&L > 0 / all partials (canon 2021-26):', int(((C._outcome.values == 'PARTIAL') & (PN > 0) & S_B).sum()), '/', int(((C._outcome.values == 'PARTIAL') & S_B).sum()))
rows = [row(str(Yr), BETA[Yr][0], YC == Yr, Yr) for Yr in (2021, 2022, 2023, 2024, 2025)]
rows += [row('2021-25', '', np.isin(YC, [2021, 2022, 2023, 2024, 2025]), 2025), row('2026', BETA[2026][0], YC == 2026, 2026)]
pd.set_option('display.width', 250)
print('\nbeta per year (value, training stock-days):', BETA)
print(pd.DataFrame(rows).to_string(index=False))
raise SystemExit
for Yr in (2020, 2021, 2022, 2023, 2024, 2025, 2026):
    M = YC == Yr
    b, g = m_(S_B & M, Yr), m_(S_G & M, Yr)
    rows.append(dict(year=Yr, beta=BETA.get(Yr, ('-',))[0], n_c=b['n'], n_g=g['n'], pnl_c=b['pnl'], pnl_g=g['pnl'], dPnL=g['pnl'] - b['pnl'],
                     win_c=b['win'], win_g=g['win'], yld_c=b['yld'], yld_g=g['yld'], sh_c=b['sh'], sh_g=g['sh'], dd_c=b['dd'], dd_g=g['dd']))
for lab, yrs, ey in (('2022-25', [2022, 2023, 2024, 2025], 2025), ('2021-26', [2021, 2022, 2023, 2024, 2025, 2026], 2026)):
    M = np.isin(YC, yrs); b, g = m_(S_B & M, ey), m_(S_G & M, ey)
    rows.append(dict(year=lab, beta='', n_c=b['n'], n_g=g['n'], pnl_c=b['pnl'], pnl_g=g['pnl'], dPnL=g['pnl'] - b['pnl'], win_c=b['win'], win_g=g['win'],
                     yld_c=b['yld'], yld_g=g['yld'], sh_c=b['sh'], sh_g=g['sh'], dd_c=b['dd'], dd_g=g['dd']))
pd.set_option('display.width', 250)
print('\nbeta per year (value, training stock-days):', BETA)
print(pd.DataFrame(rows).to_string(index=False))
