"""Site payloads for strategy C: floor model credit >= 0.45 x width (both sides), bear calls only below the
prior-session SPY 100d SMA and IV < 0.35, pooled top 6 per day by GROUND (floor config.FABLE_GROUND_MIN, no quote gate),
entries only when the name has >= 252 sessions of close history (start 2021-01-04), booked 1.04 x model.
Writes live/data/backtest_equity.json and live/data/oot_equity.json (caller backs up first)."""
import os, sys, io, json, contextlib, warnings; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import ent_canon as ec, config as cfg, report_mid_canon as rmc, report_bear_regime as rbr
from bear_regime_sweep import add_earnings_gate, add_exdiv_gate, add_regimes, add_split_gate, add_split_window_gate, prepare, realize
HERE = Path(__file__).resolve().parent; KEY = ["ticker", "entry_date", "expiry_date", "spread_type", "short_strike", "long_strike"]
FLOOR, BEAR_IV, TOP = 0.45, 0.35, 6
# 2026-10-09 (user, new canon): GROUND floor on both sleeves before the pooled top-N cap --
# 1 bp of entropy-discounted Kelly growth. Fable mode had no threshold, so 15.9% of this book
# sat at GROUND <= 0. Mirrors config.FABLE_GROUND_MIN, which live/ranker.py reads.
GMIN = getattr(cfg, "FABLE_GROUND_MIN", None)
K = float(sys.argv[1]) if len(sys.argv) > 1 else float(ec.K)   # D_ent penalty in the rank key (user 2026-09-30: 24)
FILL = float(sys.argv[2]) if len(sys.argv) > 2 else float(ec.FILL_MULT)   # 2026-09-30 (user): site book at 1.00 x model; live booking keeps ec.FILL_MULT
with contextlib.redirect_stdout(io.StringIO()):
    c, spy = prepare(); c = add_regimes(c, spy); c = add_exdiv_gate(c); c = add_earnings_gate(c); c = add_split_gate(c); c = add_split_window_gate(c)
QCOLS = ["IV", "s_bid", "s_ask", "l_bid", "l_ask"]
c = c.merge(pd.read_parquet(HERE / "frame_quotes_oi1.parquet")[KEY + QCOLS], on=KEY, how="left")   # 2026-09-30: OI >= 1 frame

# 2026-10-01 (user): quote-reality gates at CANDIDATE level, so the daily top-N backfills.
#   touch = sell the short at its bid, buy the long at its ask -- the price you can take now.
#   best  = sell at the ask, buy at the bid -- the most the quoted book could ever pay.
# Dropped: a book that cannot pay anything (touch <= 0), quotes that cross across strikes
# (short ask below long bid), an inverted mid (the nearer strike worth less than the farther),
# and split-adjusted odd ladders (CSX 32.17/32.00, NVDA 196.88/196.25) which are thin orphan
# series. Ladder gaps on a normal $0.50 grid (EOG 76/74) are real and kept.
_touch = c.s_bid - c.l_ask
_best = c.s_ask - c.l_bid
_smid, _lmid = (c.s_bid + c.s_ask) / 2, (c.l_bid + c.l_ask) / 2
_half = lambda x: (np.round(x.to_numpy(float) * 100).astype(int) % 50) == 0
_keep = (c[QCOLS[1:]].notna().all(axis=1) & (_touch > 0) & (c.s_ask >= c.l_bid) & (_smid >= _lmid)
         & _half(c.short_strike) & _half(c.long_strike))
print(f"quote gates: dropped {int((~_keep).sum()):,} of {len(c):,} candidates "
      f"(touch<=0 {int((_touch <= 0).sum()):,}, crossed {int((c.s_ask < c.l_bid).sum()):,}, "
      f"inverted mid {int((_smid < _lmid).sum()):,}, off-ladder {int((~(_half(c.short_strike) & _half(c.long_strike))).sum()):,})")
c = c[_keep].copy()
# Credit can never exceed the best quote the book showed; the delta ceiling applies on top.
c["model_credit"] = np.minimum(c.model_credit.to_numpy(float), (c.s_ask - c.l_bid).to_numpy(float)).round(4)
c = rbr.apply_delta_cap(c)   # min(credit, |delta| x width) + EV/GROUND recomputed on the capped credit
cl = pd.read_parquet(ROOT / "output/daily_closes.parquet"); cl["date"] = pd.to_datetime(cl.date).dt.normalize()
cl = cl.dropna(subset=["close"]).sort_values(["ticker", "date"]); cl["n_before"] = cl.groupby("ticker").cumcount()
c = c.merge(cl[["ticker", "date", "n_before"]].rename(columns={"date": "entry_date"}), on=["ticker", "entry_date"], how="left")
c = c[c.n_before >= ec.WINDOW].copy()
c["GROUND"] = c.EV * np.exp(-K * c.D_ent)   # rank key at this K (prepare() scored at ec.K)
# 2026-10-01 (user): third exclusion beside earnings and ex-dividend -- corporate actions (split / reverse split / spinoff)
g = ~c.exdiv_hit & ~c.earnings_hit & ~c.split_hit & ~c.split_window_hit
cw = c.model_credit / c.width
elig = (c.spread_type.eq("bull_put") & g & (cw >= FLOOR)) | (c.spread_type.eq("bear_call") & g & (cw >= FLOOR) & (c.IV < BEAR_IV) & c.below_100)
if GMIN is not None:
    _gok = c.GROUND >= float(GMIN)
    print(f"GROUND floor {float(GMIN):g}: dropped {int((elig & ~_gok).sum()):,} of {int(elig.sum()):,} eligible candidates")
    elig = elig & _gok
sel = c[elig].sort_values(["entry_date", "GROUND"], ascending=[True, False]).groupby("entry_date", sort=False).head(TOP)
picks = rbr.enrich(realize(sel, 10**6, fill=FILL))
# 2026-10-01 (user): size the Kelly arms on GROUND+carry -- the stake that earns CARRY_RATE x DTE/365
# on the capital at risk while held. Display GROUND already carries it; selection stays on raw GROUND.
_carry = float(cfg.CARRY_RATE) * np.clip(picks.DTE.astype(float), 1, None) / 365.0
_b = picks.model_credit / (picks.width - picks.model_credit)
picks["w_star_carry"] = ec.kelly_carry(picks.p.values, picks.q.values, picks.ro.values, _b.values, _carry.values)
print(f"carry-adjusted Kelly stake: median {np.nanmedian(picks.w_star_carry):.3f} vs raw {np.nanmedian(picks.w_star):.3f}; finite {100 * np.isfinite(picks.w_star_carry).mean():.0f}%")
picks.to_parquet(HERE / f"picks_C_252_k{K:g}.parquet")

def captions(payload):
    k = payload["config"]
    k["selection"] = (f"strategy C: model credit >= {FLOOR:.2f}x width (both sides), pooled top-{TOP}/day by GROUND at k={K:g}" + (f" (GROUND >= {float(GMIN):g})" if GMIN is not None else " (no threshold)") + "; "
                      f"entries only once the name has {ec.WINDOW} sessions of history (start {picks.entry_date.min().date()})")
    k["regime"] = "bull puts every day; bear calls only below the prior-session SPY 100d SMA"
    k["bear_gates"] = (f"bear calls: IV < {BEAR_IV:.2f}, same credit floor, share the pooled top-{TOP}. Earnings, ex-dividend and "
                       "corporate-action (split / reverse split / spinoff) gates, entry through expiry+1, apply to both sleeves.")
    k["parity"] = "no bull parity veto; no bear parity veto"
    k["fill_basis"] = f"{FILL:.2f}\u00d7 min(smile-fit model credit, |short delta| \u00d7 width, best quoted credit); quote gates: touch > 0, no crossed or inverted quotes, $0.50 strike ladder; partial-WIN at 50% intrinsic; no commission"
    k["scoring"] = f"G = Kelly log-growth on P_real at the delta-capped model credit; GROUND = (e^G\u22121)\u00b7e^(\u2212k\u00b7D_ent), k = {K:g}"
    return payload
for lab, m, yr, path in (("IS", picks.entry_date.dt.year <= 2025, 2025, "backtest_equity.json"),
                         ("OOT", picks.entry_date.dt.year == 2026, 2026, "oot_equity.json")):
    p = picks[m]
    with contextlib.redirect_stdout(io.StringIO()):
        pay = captions(rbr.patch_config(rmc.build_payload(p, yr, f"{'backtest 2021-25' if lab == 'IS' else 'OOT 2026'} (strategy C: GROUND rank, bears below 100d)"), lab == "OOT"))
    (ROOT / "live/data" / path).write_text(json.dumps(pay, indent=2))
    s = pay["summary"]
    print(f"{lab}: {s['n_trades']} trades, qty2 ${s['strategy_final']:,.0f}, $-Sh {s['strategy_sharpe_dollar']}, DD {s['strategy_max_dd']}%, "
          f"qty1 ${s['qty1_final']:,.0f} DD {s['qty1_max_dd']}%, yield {s['strategy_yield']}%, window {s['window_start']}..{s['window_end']} -> live/data/{path}")
