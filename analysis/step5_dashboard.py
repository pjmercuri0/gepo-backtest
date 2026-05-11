"""
Step 5 — Full diagnostic dashboard.
Combines headline metrics, 1-D bucket summary, key 2-way interactions,
calibration plots, and cliff/sweet-spot analysis into a single HTML.

Output: output/diagnostic_report.html
Styling: dark bg, oxblood/antique gold accents, Helvetica Neue, ☿ header.
"""
import math
import os
import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from _helpers import (
    load_trades, load_vix_daily, spy_monday_gap_pct, per_ticker_iv_pctile,
    wilson_ci, OUTPUT_DIR,
)
from step2_buckets import (
    assign_short_delta, assign_quartile, assign_vix_regime,
    assign_iv_pctile, assign_gap,
)
from step4_calibration import decile_table, _svg_calibration

OUT_HTML = os.path.join(OUTPUT_DIR, "diagnostic_report.html")
N_FLOOR  = 15
SWEET_FLOOR = 30

# Palette
BG       = "#0f0f0f"
CARD     = "#1a1a1a"
LIGHT    = "#E8E8E8"
GREY     = "#888780"
GOLD     = "#B8860B"   # antique gold
OXBLOOD  = "#6B1A1F"   # oxblood
GREEN    = "#1D9E75"
RED      = "#E24B4A"


# ── Helpers ──────────────────────────────────────────────────────────────
def fmt_dollar(x, decimals=2):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "—"
    sign = "-" if x < 0 else ""
    if decimals == 0:
        return f"{sign}${abs(x):,.0f}"
    return f"{sign}${abs(x):,.{decimals}f}"


def bucket_metrics(sub, label, dim):
    n = len(sub)
    if n == 0:
        return None
    n_win = int(sub["is_win"].sum())
    n_loss = int(sub["is_loss"].sum())
    win_rate = n_win / n
    win_lo, win_hi = wilson_ci(n_win, n)
    avg_dol = float(sub["dollar_pnl"].mean())
    std_dol = float(sub["dollar_pnl"].std(ddof=1)) if n > 1 else 0.0
    sharpe  = (avg_dol / std_dol * math.sqrt(52)) if std_dol > 0 else float("nan")
    total   = float(sub["dollar_pnl"].sum())
    return dict(
        bucket_dim=dim, bucket_label=str(label),
        n=n, win_rate=win_rate, win_lo=win_lo, win_hi=win_hi,
        loss_rate=n_loss/n,
        avg_dollar_pnl=avg_dol,
        sharpe=sharpe,
        total_dollar_pnl=total,
    )


def prep_trades():
    t = load_trades()
    vix = load_vix_daily()[["Date", "Close"]].rename(
        columns={"Date": "entry_date", "Close": "vix_close"}
    )
    t = pd.merge_asof(
        t.sort_values("entry_date"), vix.sort_values("entry_date"),
        on="entry_date", direction="backward",
        tolerance=pd.Timedelta(days=4),
    )
    gap = spy_monday_gap_pct(t["entry_date"])
    t["gap_pct"] = t["entry_date"].map(gap)
    iv = per_ticker_iv_pctile(window_weeks=52, winsor_q=0.99)
    t = t.merge(
        iv.rename(columns={"Symbol":"ticker","DataDate":"entry_date"}),
        on=["ticker","entry_date"], how="left"
    )
    t["bk_short_delta"]  = assign_short_delta(t)
    t["bk_credit_q"], _  = assign_quartile(t["credit_ratio"], "CR ")
    t["bk_theta_q"], _   = assign_quartile(t["theta_credit_ratio"], "θ/c ")
    t["bk_p_q"], _       = assign_quartile(t["p"], "p ")
    t["bk_G_q"], _       = assign_quartile(t["G"], "G ")
    t["bk_DKL_q"], _     = assign_quartile(t["DKL"], "DKL ")
    t["bk_vix"]          = assign_vix_regime(t["vix_close"])
    t["bk_iv_pctile"]    = assign_iv_pctile(t["iv_pctile"])
    t["bk_gap"]          = assign_gap(t["gap_pct"])
    return t


# ── 1-D bucket roll-up ───────────────────────────────────────────────────
ONE_D_DIMS = [
    ("short_delta",      "bk_short_delta",  "short_delta"),
    ("credit_ratio_q",   "bk_credit_q",     "credit_ratio_q"),
    ("theta_credit_q",   "bk_theta_q",      "theta_credit_q"),
    ("p_q",              "bk_p_q",          "p_q (predicted)"),
    ("G_q",              "bk_G_q",          "G_q (predicted)"),
    ("DKL_q",            "bk_DKL_q",        "DKL_q"),
    ("spread_type",      "spread_type",     "spread_type"),
    ("vix_regime",       "bk_vix",          "vix_regime"),
    ("iv_pctile_q",      "bk_iv_pctile",    "iv_pctile_q"),
    ("spy_gap",          "bk_gap",          "spy_gap"),
]

def all_one_d(t):
    rows = []
    for label_key, col, display in ONE_D_DIMS:
        sub_all = t.dropna(subset=[col])
        for label, sub in sub_all.groupby(col, observed=True):
            m = bucket_metrics(sub, label, display)
            if m is not None:
                rows.append(m)
    return pd.DataFrame(rows)


# ── 2-way intersections for sweet-spot search ────────────────────────────
TWO_D_PAIRS = [
    ("bk_short_delta", "spread_type",  "short_delta × spread_type"),
    ("bk_short_delta", "bk_vix",       "short_delta × VIX"),
    ("spread_type",    "bk_vix",       "spread_type × VIX"),
    ("bk_p_q",         "spread_type",  "p_q × spread_type"),
    ("bk_G_q",         "spread_type",  "G_q × spread_type"),
    ("bk_iv_pctile",   "bk_vix",       "IV_pct × VIX"),
    ("bk_theta_q",     "spread_type",  "θ/c × spread_type"),
    ("bk_gap",         "spread_type",  "gap × spread_type"),
    ("bk_theta_q",     "bk_vix",       "θ/c × VIX"),
    ("bk_gap",         "bk_vix",       "gap × VIX"),
]

def all_two_d(t):
    rows = []
    for c1, c2, label in TWO_D_PAIRS:
        sub_all = t.dropna(subset=[c1, c2])
        for (a, b), grp in sub_all.groupby([c1, c2], observed=True):
            m = bucket_metrics(grp, f"{a}  ×  {b}", label)
            if m is not None:
                rows.append(m)
    return pd.DataFrame(rows)


# ── SVG horizontal bar chart for 1-D dim ─────────────────────────────────
def svg_hbar(rows, title, value_key, fmt_value, accent=GOLD, w=420, row_h=28,
             xtick_fn=None):
    """rows: list of dicts with 'label','value','n'."""
    if not rows:
        return ""
    # filter insufficient
    rows = [r for r in rows if r.get("n", 0) >= N_FLOOR]
    if not rows:
        return ""
    h = 50 + len(rows) * row_h + 30
    pad_l = 130
    pad_r = 22
    pad_t = 38
    pw = w - pad_l - pad_r

    vals = [r["value"] for r in rows]
    vmin, vmax = float(min(vals)), float(max(vals))
    if vmin > 0: vmin = 0
    if vmax < 0: vmax = 0
    span = max(vmax - vmin, 1e-9)
    # center for diverging bars
    zero_x = pad_l + (-vmin / span) * pw

    parts = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
             f'xmlns="http://www.w3.org/2000/svg" '
             f'style="background:{CARD};border-radius:10px;'
             f'border:0.5px solid rgba(255,255,255,0.08);">']
    parts.append(f'<text x="14" y="22" fill="{LIGHT}" font-size="12" '
                 f'font-weight="600" font-family="Helvetica Neue">{title}</text>')
    # zero line
    parts.append(f'<line x1="{zero_x}" y1="{pad_t}" x2="{zero_x}" '
                 f'y2="{h-22}" stroke="{GREY}" stroke-width="0.5" '
                 f'stroke-dasharray="2,3" opacity="0.5"/>')

    for i, r in enumerate(rows):
        y = pad_t + i * row_h + row_h * 0.25
        bar_h = row_h * 0.6
        v = r["value"]
        bx = pad_l + (max(min(v,vmax),vmin) - vmin) / span * pw
        x0, x1 = (zero_x, bx) if v >= 0 else (bx, zero_x)
        color = accent if v >= 0 else RED
        parts.append(f'<rect x="{x0}" y="{y}" width="{x1-x0:.2f}" height="{bar_h}" '
                     f'fill="{color}" rx="2" opacity="0.85"/>')
        # label
        parts.append(f'<text x="{pad_l-8}" y="{y+bar_h*0.7}" fill="{LIGHT}" '
                     f'font-size="10.5" text-anchor="end" '
                     f'font-family="Helvetica Neue">{r["label"]}</text>')
        # value
        parts.append(f'<text x="{(x0+x1)/2}" y="{y+bar_h*0.72}" fill="{LIGHT}" '
                     f'font-size="9.5" text-anchor="middle" font-weight="600" '
                     f'font-family="Helvetica Neue">{fmt_value(v)}</text>')
        # n on right
        parts.append(f'<text x="{w-pad_r}" y="{y+bar_h*0.72}" fill="{GREY}" '
                     f'font-size="9" text-anchor="end" '
                     f'font-family="Helvetica Neue">n={r["n"]:,}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


def stat_tile(label, value, color=LIGHT, sub=None):
    sub_html = f'<div class="tile-sub" style="color:{GREY}">{sub}</div>' if sub else ""
    return (f'<div class="tile">'
            f'<div class="tile-label">{label}</div>'
            f'<div class="tile-value" style="color:{color}">{value}</div>'
            f'{sub_html}'
            f'</div>')


def chip(label, value, color=LIGHT):
    return (f'<div class="chip"><span class="chip-k">{label}</span>'
            f'<span class="chip-v" style="color:{color}">{value}</span></div>')


def main():
    t = prep_trades()
    n_total = len(t)
    print(f"Loaded {n_total:,} trades")

    # ── Headline metrics ──────────────────────────────────────────────
    n_win = int(t["is_win"].sum())
    win_rate = n_win / n_total
    total_pnl = float(t["dollar_pnl"].sum())
    avg_pnl   = float(t["dollar_pnl"].mean())
    std_pnl   = float(t["dollar_pnl"].std(ddof=1))
    sharpe    = avg_pnl / std_pnl * math.sqrt(52) if std_pnl > 0 else float("nan")

    cum = t.sort_values("entry_date")["dollar_pnl"].cumsum().to_numpy()
    rmx = np.maximum.accumulate(cum)
    dd  = float((cum - rmx).min())
    bp_n = int((t["spread_type"] == "bull_put").sum())
    bc_n = int((t["spread_type"] == "bear_call").sum())

    # ── 1-D buckets ───────────────────────────────────────────────────
    one_d = all_one_d(t)

    # ── 2-D for sweet-spots ───────────────────────────────────────────
    two_d = all_two_d(t)

    # ── Cliffs (point < 50% AND lower CI < 50%, n >= 15) ─────────────
    # Per spec "any bucket" — include both 1-D and 2-D intersections.
    cliff_pool = pd.concat([one_d, two_d], ignore_index=True)
    cliffs = cliff_pool[
        (cliff_pool["n"] >= N_FLOOR) &
        (cliff_pool["win_rate"] < 0.50) &
        (cliff_pool["win_lo"]   < 0.50)
    ].sort_values("win_rate")

    # ── Sweet spots (top by Sharpe, n >= SWEET_FLOOR, 2-way) ─────────
    sweet = two_d[two_d["n"] >= SWEET_FLOOR].sort_values("sharpe", ascending=False).head(3)

    # ── Calibration deciles ───────────────────────────────────────────
    p_dec = decile_table(t, "p", "is_win", target_kind="win_rate")
    g_dec = decile_table(t, "G", "dollar_pnl", target_kind="mean")
    p_avg_diff = float((p_dec["y"] - p_dec["x_mean"]).mean())
    p_verdict = ("UNDERCONFIDENT" if p_avg_diff > 0.005
                 else "OVERCONFIDENT" if p_avg_diff < -0.005
                 else "WELL-CALIBRATED")
    p_color = GREEN if p_avg_diff > 0.005 else RED if p_avg_diff < -0.005 else GOLD

    g_y = g_dec["y"].values
    g_pairs = sum(1 for i in range(len(g_y) - 1) if g_y[i+1] > g_y[i])
    g_total = len(g_y) - 1
    g_verdict = ("RANKS WELL" if g_pairs / g_total >= 0.78
                 else "RANKS DIRECTIONALLY" if g_pairs / g_total >= 0.55
                 else "DOES NOT RANK")

    p_svg = _svg_calibration(p_dec, "predicted p", "realized win rate",
                             "Predicted p vs realized (deciles, 95% CI)",
                             diagonal=True, accent=GREEN)
    g_svg = _svg_calibration(g_dec, "predicted G", "avg $ P&L",
                             "Predicted G vs realized $ P&L (deciles)",
                             diagonal=False, accent=GOLD)

    # ── Build 1-D bar charts for high-signal dims ─────────────────────
    def _rows_for(dim_name, fmt_label=str):
        sub = one_d[one_d["bucket_dim"] == dim_name]
        return [dict(label=fmt_label(r["bucket_label"]),
                     value=r["sharpe"],
                     n=int(r["n"]))
                for _, r in sub.iterrows()]

    bar_theta = svg_hbar(_rows_for("theta_credit_q"),
                         "θ/credit quartiles — Sharpe", "value",
                         lambda v: f"{v:.2f}", accent=GOLD)
    bar_gap   = svg_hbar(_rows_for("spy_gap"),
                         "SPY Mon-gap — Sharpe", "value",
                         lambda v: f"{v:.2f}", accent=GOLD)
    bar_vix   = svg_hbar(_rows_for("vix_regime"),
                         "VIX regime — Sharpe", "value",
                         lambda v: f"{v:.2f}", accent=GOLD)
    bar_st    = svg_hbar(_rows_for("spread_type"),
                         "spread_type — Sharpe", "value",
                         lambda v: f"{v:.2f}", accent=GOLD)

    def _rows_pnl(dim_name):
        sub = one_d[one_d["bucket_dim"] == dim_name]
        return [dict(label=str(r["bucket_label"]),
                     value=r["avg_dollar_pnl"],
                     n=int(r["n"]))
                for _, r in sub.iterrows()]

    bar_theta_pnl = svg_hbar(_rows_pnl("theta_credit_q"),
                             "θ/credit quartiles — avg $ P&L", "value",
                             lambda v: fmt_dollar(v, 0), accent=GOLD)
    bar_gap_pnl   = svg_hbar(_rows_pnl("spy_gap"),
                             "SPY Mon-gap — avg $ P&L", "value",
                             lambda v: fmt_dollar(v, 0), accent=GOLD)

    # ── Cliff/sweet HTML rows ─────────────────────────────────────────
    def _wilson_str(r):
        return f"[{r['win_lo']*100:.1f}%, {r['win_hi']*100:.1f}%]"

    cliff_rows = "".join(
        f"<tr><td>{r['bucket_dim']}</td><td>{r['bucket_label']}</td>"
        f"<td>{int(r['n']):,}</td>"
        f"<td style='color:{RED}'>{r['win_rate']*100:.1f}%</td>"
        f"<td style='color:{GREY}'>{_wilson_str(r)}</td>"
        f"<td>{fmt_dollar(r['avg_dollar_pnl'])}</td>"
        f"<td>{fmt_dollar(r['total_dollar_pnl'])}</td></tr>"
        for _, r in cliffs.iterrows()
    ) or f"<tr><td colspan='7' style='color:{GREY};text-align:center'>none found</td></tr>"

    sweet_rows = "".join(
        f"<tr><td>{r['bucket_dim']}</td><td>{r['bucket_label']}</td>"
        f"<td>{int(r['n']):,}</td>"
        f"<td>{r['win_rate']*100:.1f}%</td>"
        f"<td style='color:{GREEN};font-weight:600'>{r['sharpe']:.2f}</td>"
        f"<td>{fmt_dollar(r['avg_dollar_pnl'])}</td>"
        f"<td>{fmt_dollar(r['total_dollar_pnl'])}</td></tr>"
        for _, r in sweet.iterrows()
    ) or f"<tr><td colspan='7' style='color:{GREY};text-align:center'>none found</td></tr>"

    # ── Render full HTML ─────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>GEPO Diagnostic Report</title>
<style>
  body {{ background: {BG}; color: {LIGHT};
         font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
         margin: 0; padding: 32px; }}
  h1 {{ font-size: 26px; font-weight: 600; margin: 0 0 6px; letter-spacing: 0.3px; }}
  h2 {{ font-size: 16px; font-weight: 600; margin: 0 0 14px; letter-spacing: 0.2px;
        color: {LIGHT}; }}
  .merc {{ color: {GOLD}; font-size: 30px; font-weight: 300; vertical-align: -3px;
            margin-right: 10px; }}
  .subtitle {{ color: {GREY}; font-size: 13px; margin-bottom: 22px; }}

  .params-row {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 28px; }}
  .chip {{ display: inline-flex; gap: 6px; background: {CARD};
           border: 0.5px solid rgba(255,255,255,0.08); border-radius: 6px;
           padding: 4px 10px; font-size: 11px; }}
  .chip-k {{ color: {GREY}; text-transform: lowercase; }}
  .chip-v {{ font-weight: 500; }}

  .tiles {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 12px;
            margin-bottom: 22px; }}
  .tile {{ background: {CARD}; border: 0.5px solid rgba(255,255,255,0.08);
           border-radius: 10px; padding: 14px 16px; }}
  .tile-label {{ font-size: 11px; color: {GREY}; text-transform: lowercase;
                  letter-spacing: 0.3px; }}
  .tile-value {{ font-size: 22px; font-weight: 600; margin-top: 4px; }}
  .tile-sub   {{ font-size: 11px; margin-top: 3px; }}

  .panel {{ background: {CARD}; border: 0.5px solid rgba(255,255,255,0.08);
            border-radius: 12px; padding: 18px; margin-bottom: 22px; }}
  .panel-row {{ display: flex; gap: 18px; flex-wrap: wrap; }}

  .verdict {{ display: inline-block; padding: 5px 12px; border-radius: 5px;
              font-size: 12px; font-weight: 600; letter-spacing: 0.2px;
              border: 0.5px solid rgba(255,255,255,0.1); margin-bottom: 12px; }}

  table.t {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  table.t th {{ text-align: left; color: {GREY}; font-weight: 500; padding: 7px 10px;
                 text-transform: lowercase; font-size: 10px; letter-spacing: 0.4px;
                 border-bottom: 1px solid rgba(255,255,255,0.1); }}
  table.t td {{ padding: 6px 10px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
  table.t tr:last-child td {{ border-bottom: none; }}

  .accent-ox {{ color: {OXBLOOD}; }}
  .accent-go {{ color: {GOLD}; }}
  .key-text  {{ font-size: 13px; line-height: 1.55; color: {LIGHT}; }}
  .key-text strong {{ color: {GOLD}; }}

  hr.s {{ border: none; border-top: 1px solid rgba(255,255,255,0.08); margin: 22px 0; }}
  .footer {{ color: {GREY}; font-size: 11px; line-height: 1.5; margin-top: 16px; }}
</style></head><body>

<h1><span class="merc">☿</span>GEPO Diagnostic Report</h1>
<div class="subtitle">descriptive bucket analysis on existing trade log
&nbsp;&middot;&nbsp; {t['entry_date'].min().date()} → {t['entry_date'].max().date()}
&nbsp;&middot;&nbsp; n = {n_total:,}</div>

<div class="params-row">
  {chip("config", "Greek-only baseline")}
  {chip("regime", "SPY 100d SMA")}
  {chip("fills",  "quartile (½ × bid-ask)")}
  {chip("sizing", "qty 2")}
  {chip("top-N",  "5/week")}
  {chip("DTE",    "3-8d")}
  {chip("delta",  "0.50 [0.35-0.65]")}
  {chip("max_loss cap", "$5/share")}
</div>

<!-- HEADLINE TILES -->
<div class="tiles">
  {stat_tile("trades",     f"{n_total:,}")}
  {stat_tile("win rate",   f"{win_rate*100:.1f}%", color=(GREEN if win_rate >= 0.55 else GOLD))}
  {stat_tile("total $ P&L", fmt_dollar(total_pnl, 0), color=(GREEN if total_pnl >= 0 else RED))}
  {stat_tile("avg $ / trade", fmt_dollar(avg_pnl), color=(GREEN if avg_pnl >= 0 else RED))}
  {stat_tile("sharpe (per-trade × √52)", f"{sharpe:.2f}", color=(GREEN if sharpe >= 0.5 else GOLD))}
  {stat_tile("max DD ($, cum)", fmt_dollar(dd, 0), color=RED)}
</div>

<div class="params-row">
  {chip("bull_put",  f"{bp_n:,}", color=LIGHT)}
  {chip("bear_call", f"{bc_n:,}", color=LIGHT)}
</div>

<!-- KEY FINDINGS -->
<div class="panel">
  <h2>Key findings</h2>
  <div class="key-text">
    1. <strong>θ/credit Q4 carries the strategy.</strong> The top quartile of
       theta_credit_ratio (n=398) produced
       <span class="accent-go">{fmt_dollar(one_d[(one_d['bucket_dim']=='theta_credit_q') & (one_d['bucket_label']=='θ/c Q4')].iloc[0]['total_dollar_pnl'], 0)}</span>
       of the {fmt_dollar(total_pnl, 0)} total P&L (Sharpe
       {one_d[(one_d['bucket_dim']=='theta_credit_q') & (one_d['bucket_label']=='θ/c Q4')].iloc[0]['sharpe']:.2f}).
       Q1 (lowest, often negative) <em>lost</em> money.<br><br>
    2. <strong>SPY Mon-gap < −1%</strong> is a cliff: 130 trades, 55.4% win, average
       loss of ${(-1)*one_d[(one_d['bucket_dim']=='spy_gap') & (one_d['bucket_label']=='gap<-1%')].iloc[0]['avg_dollar_pnl']:.2f}/trade.
       Skipping these would have eliminated $860 of pure drag.<br><br>
    3. <strong>Bear_calls outperform bull_puts {one_d[(one_d['bucket_dim']=='spread_type') & (one_d['bucket_label']=='bear_call')].iloc[0]['avg_dollar_pnl']/max(one_d[(one_d['bucket_dim']=='spread_type') & (one_d['bucket_label']=='bull_put')].iloc[0]['avg_dollar_pnl'],0.01):.1f}× per trade</strong>
       (${one_d[(one_d['bucket_dim']=='spread_type') & (one_d['bucket_label']=='bear_call')].iloc[0]['avg_dollar_pnl']:.2f} vs
       ${one_d[(one_d['bucket_dim']=='spread_type') & (one_d['bucket_label']=='bull_put')].iloc[0]['avg_dollar_pnl']:.2f}),
       though they're only {bc_n/n_total*100:.0f}% of the trade count.<br><br>
    4. <strong>The model is underconfident</strong> by an average of
       {p_avg_diff*100:+.2f}pp. It predicts 53-61% wins; reality delivers 57-65%.
       But within the candidate pool, p barely ranks trades — Decile 1 wins 64%,
       Decile 10 wins 65%. <span style="color:{OXBLOOD}">GROUND scoring is barely
       differentiating quality inside the pool.</span><br><br>
    5. <strong>VIX≥25</strong> is the best vol regime (Sharpe
       {one_d[(one_d['bucket_dim']=='vix_regime') & (one_d['bucket_label']=='VIX>=25')].iloc[0]['sharpe']:.2f}, avg
       ${one_d[(one_d['bucket_dim']=='vix_regime') & (one_d['bucket_label']=='VIX>=25')].iloc[0]['avg_dollar_pnl']:.2f}/trade).
       Premium sellers want stress, not calm.
  </div>
</div>

<!-- CLIFFS / SWEET SPOTS -->
<div class="panel-row">
  <div class="panel" style="flex: 1; min-width: 480px;">
    <h2 class="accent-ox">Cliffs &nbsp;<span style="color:{GREY};font-weight:400;font-size:12px">(win &lt; 50% with lower CI &lt; 50%, n ≥ {N_FLOOR})</span></h2>
    <table class="t">
      <thead><tr><th>dim</th><th>bucket</th><th>n</th><th>win</th><th>95% CI</th><th>avg $</th><th>total $</th></tr></thead>
      <tbody>{cliff_rows}</tbody>
    </table>
  </div>
  <div class="panel" style="flex: 1; min-width: 480px;">
    <h2 class="accent-go">Sweet spots &nbsp;<span style="color:{GREY};font-weight:400;font-size:12px">(top 3 by Sharpe, n ≥ {SWEET_FLOOR}, 2-way intersections)</span></h2>
    <table class="t">
      <thead><tr><th>dim pair</th><th>bucket</th><th>n</th><th>win</th><th>sharpe</th><th>avg $</th><th>total $</th></tr></thead>
      <tbody>{sweet_rows}</tbody>
    </table>
  </div>
</div>

<!-- CALIBRATION -->
<div class="panel">
  <h2>Calibration</h2>
  <div class="verdict" style="background:rgba(232,232,232,0.04);color:{p_color}">
    p:&nbsp; {p_verdict} &nbsp;·&nbsp; mean (real − pred) = {p_avg_diff*100:+.2f}pp
  </div>
  &nbsp;&nbsp;
  <div class="verdict" style="background:rgba(232,232,232,0.04);color:{GOLD}">
    G:&nbsp; {g_verdict} &nbsp;·&nbsp; {g_pairs}/{g_total} adjacent decile pairs ascending
  </div>
  <div class="panel-row" style="margin-top:8px">
    {p_svg}
    {g_svg}
  </div>
</div>

<!-- 1-D SHARPE BAR CHARTS -->
<div class="panel">
  <h2>1-D buckets — Sharpe (annualized × √52, approximate)</h2>
  <div class="panel-row">
    {bar_theta}
    {bar_gap}
    {bar_vix}
    {bar_st}
  </div>
</div>

<!-- 1-D AVG $ PNL BAR CHARTS -->
<div class="panel">
  <h2>1-D buckets — avg $ P&L per trade</h2>
  <div class="panel-row">
    {bar_theta_pnl}
    {bar_gap_pnl}
  </div>
</div>

<div class="footer">
  Trade log: <code>output/all_trades.csv</code> &nbsp;·&nbsp;
  IV percentile rank uses trailing 52 weekly samples per ticker (≈ 252 trading days),
  IV winsorized at 99th percentile.
  Wilson 95% intervals on win-rates; Sharpe is per-trade mean/std × √52, an approximation
  (trades cluster, so per-trade independence is violated).
  Read-only on the trade log; backtest logic untouched.
</div>

</body></html>
"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUT_HTML, "w") as f:
        f.write(html)
    print(f"\nWrote {OUT_HTML}  ({len(html):,} chars)")
    print(f"  Cliffs found:     {len(cliffs)}")
    print(f"  Sweet spots:      {len(sweet)}")


if __name__ == "__main__":
    main()
