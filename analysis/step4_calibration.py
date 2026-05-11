"""
Step 4 — Model calibration report.
Buckets trades by predicted p (decile) and computes realized win rate
with Wilson CI. Same for G vs avg dollar_pnl.

Output: output/calibration_report.html (self-contained, inline SVG).
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
from _helpers import load_trades, wilson_ci, OUTPUT_DIR

OUT_HTML = os.path.join(OUTPUT_DIR, "calibration_report.html")


# ── Decile builder ───────────────────────────────────────────────────────
def decile_table(df, value_col, target_col, target_kind):
    """
    Bucket df into 10 quantile buckets of value_col, return per-bucket stats.
    target_kind: 'win_rate' (binary) or 'mean' (continuous).
    """
    df = df[[value_col, target_col]].dropna().copy()
    df["decile"] = pd.qcut(df[value_col], q=10, labels=False, duplicates="drop")
    rows = []
    for d, sub in df.groupby("decile", observed=True):
        n = len(sub)
        x_mean = float(sub[value_col].mean())
        if target_kind == "win_rate":
            successes = int(sub[target_col].sum())
            y         = successes / n
            lo, hi    = wilson_ci(successes, n)
        else:
            y  = float(sub[target_col].mean())
            sd = float(sub[target_col].std(ddof=1)) if n > 1 else 0.0
            sem = sd / math.sqrt(n)
            lo, hi = y - 1.96 * sem, y + 1.96 * sem
        rows.append(dict(decile=int(d) + 1, n=n,
                         x_mean=x_mean, y=y, y_lo=lo, y_hi=hi))
    return pd.DataFrame(rows)


# ── Inline SVG renderer (self-contained, no external libs) ───────────────
GREEN  = "#1D9E75"
RED    = "#E24B4A"
GOLD   = "#B8860B"
OXBLOOD= "#6B1A1F"
GREY   = "#888780"
LIGHT  = "#E8E8E8"
BG     = "#0f0f0f"
CARD   = "#1a1a1a"


def _svg_calibration(decile_df, x_label, y_label, title,
                     diagonal=False, xlim=None, ylim=None,
                     accent=GREEN):
    W, H  = 640, 420
    pad_l = 70
    pad_r = 30
    pad_t = 50
    pad_b = 70
    pw, ph = W - pad_l - pad_r, H - pad_t - pad_b

    xs = decile_df["x_mean"].values
    ys = decile_df["y"].values
    los = decile_df["y_lo"].values
    his = decile_df["y_hi"].values

    if xlim is None:
        x_min, x_max = float(xs.min()), float(xs.max())
        margin = max(0.02 * (x_max - x_min), 1e-6)
        xlim = (x_min - margin, x_max + margin)
    if ylim is None:
        y_min = float(min(los.min(), ys.min()))
        y_max = float(max(his.max(), ys.max()))
        margin = max(0.05 * (y_max - y_min), 1e-6)
        ylim = (y_min - margin, y_max + margin)

    if diagonal:
        # Force same range so the diagonal is meaningful
        lo = min(xlim[0], ylim[0])
        hi = max(xlim[1], ylim[1])
        xlim = ylim = (lo, hi)

    def sx(v): return pad_l + (v - xlim[0]) / (xlim[1] - xlim[0]) * pw
    def sy(v): return pad_t + ph - (v - ylim[0]) / (ylim[1] - ylim[0]) * ph

    parts = []
    parts.append(f'<svg width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
                 f'xmlns="http://www.w3.org/2000/svg" '
                 f'style="background:{CARD};border-radius:12px;'
                 f'border:0.5px solid rgba(255,255,255,0.08);">')

    # Title
    parts.append(f'<text x="{pad_l}" y="28" fill="{LIGHT}" '
                 f'font-family="Helvetica Neue, Helvetica, Arial" '
                 f'font-size="13" font-weight="600">{title}</text>')

    # Axes
    parts.append(f'<line x1="{pad_l}" y1="{pad_t}" x2="{pad_l}" y2="{pad_t+ph}" '
                 f'stroke="{GREY}" stroke-width="0.5"/>')
    parts.append(f'<line x1="{pad_l}" y1="{pad_t+ph}" x2="{pad_l+pw}" y2="{pad_t+ph}" '
                 f'stroke="{GREY}" stroke-width="0.5"/>')

    # Y gridlines + labels
    for frac in [0, 0.25, 0.5, 0.75, 1.0]:
        v = ylim[0] + frac * (ylim[1] - ylim[0])
        y = sy(v)
        parts.append(f'<line x1="{pad_l}" y1="{y}" x2="{pad_l+pw}" y2="{y}" '
                     f'stroke="{GREY}" stroke-width="0.3" '
                     f'stroke-dasharray="2,3" opacity="0.4"/>')
        parts.append(f'<text x="{pad_l-10}" y="{y+4}" fill="{GREY}" '
                     f'font-size="11" text-anchor="end" '
                     f'font-family="Helvetica Neue, Helvetica, Arial">'
                     f'{v:.2f}</text>')

    # X labels
    for frac in [0, 0.25, 0.5, 0.75, 1.0]:
        v = xlim[0] + frac * (xlim[1] - xlim[0])
        x = sx(v)
        parts.append(f'<text x="{x}" y="{pad_t+ph+18}" fill="{GREY}" '
                     f'font-size="11" text-anchor="middle" '
                     f'font-family="Helvetica Neue, Helvetica, Arial">'
                     f'{v:.3f}</text>')

    # Axis labels
    parts.append(f'<text x="{pad_l + pw/2}" y="{H-15}" fill="{LIGHT}" '
                 f'font-size="12" text-anchor="middle" '
                 f'font-family="Helvetica Neue, Helvetica, Arial">{x_label}</text>')
    parts.append(f'<text x="20" y="{pad_t + ph/2}" fill="{LIGHT}" '
                 f'font-size="12" text-anchor="middle" '
                 f'font-family="Helvetica Neue, Helvetica, Arial" '
                 f'transform="rotate(-90 20 {pad_t + ph/2})">{y_label}</text>')

    # Diagonal (perfect calibration)
    if diagonal:
        x0 = sx(xlim[0]); y0 = sy(xlim[0])
        x1 = sx(xlim[1]); y1 = sy(xlim[1])
        parts.append(f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" '
                     f'stroke="{GOLD}" stroke-width="1" stroke-dasharray="4,4"/>')
        parts.append(f'<text x="{x1-8}" y="{y1+14}" fill="{GOLD}" font-size="10" '
                     f'text-anchor="end" '
                     f'font-family="Helvetica Neue, Helvetica, Arial">'
                     f'perfect calibration</text>')

    # CI bars
    for i in range(len(decile_df)):
        x = sx(xs[i])
        parts.append(f'<line x1="{x}" y1="{sy(los[i])}" x2="{x}" y2="{sy(his[i])}" '
                     f'stroke="{accent}" stroke-width="1.5" opacity="0.55"/>')

    # Connecting line
    pts = " ".join(f"{sx(xs[i]):.1f},{sy(ys[i]):.1f}" for i in range(len(decile_df)))
    parts.append(f'<polyline points="{pts}" fill="none" '
                 f'stroke="{accent}" stroke-width="1.6" opacity="0.85"/>')

    # Decile points
    for i in range(len(decile_df)):
        x, y = sx(xs[i]), sy(ys[i])
        parts.append(f'<circle cx="{x}" cy="{y}" r="4" fill="{accent}" '
                     f'stroke="{LIGHT}" stroke-width="0.5"/>')
        parts.append(f'<text x="{x}" y="{y-9}" fill="{LIGHT}" font-size="9" '
                     f'text-anchor="middle" '
                     f'font-family="Helvetica Neue, Helvetica, Arial">'
                     f'{decile_df["decile"].iloc[i]}</text>')

    parts.append("</svg>")
    return "\n".join(parts)


def _decile_table_html(decile_df, x_label, y_label, target_kind):
    cells = []
    for _, r in decile_df.iterrows():
        n_str = f'{int(r["n"]):,}'
        if target_kind == "win_rate":
            ystr = f'{r["y"]*100:5.2f}%'
            cistr = f'[{r["y_lo"]*100:.1f}%, {r["y_hi"]*100:.1f}%]'
        else:
            ystr = f'${r["y"]:,.2f}'
            cistr = f'±${(r["y_hi"] - r["y"]):.2f}'
        cells.append(
            f'<tr><td>{int(r["decile"])}</td>'
            f'<td>{n_str}</td>'
            f'<td>{r["x_mean"]:.4f}</td>'
            f'<td>{ystr}</td>'
            f'<td style="color:{GREY}">{cistr}</td></tr>'
        )
    html = (
        f'<table class="dt"><thead><tr>'
        f'<th>decile</th><th>n</th><th>mean {x_label}</th>'
        f'<th>realized {y_label}</th><th>95% CI</th>'
        f'</tr></thead><tbody>{"".join(cells)}</tbody></table>'
    )
    return html


def main():
    df = load_trades()
    print(f"Loaded {len(df):,} trades")

    # ── 1. p calibration ──────────────────────────────────────────
    p_dec = decile_table(df, "p", "is_win", target_kind="win_rate")

    p_pred_min, p_pred_max = float(p_dec["x_mean"].min()), float(p_dec["x_mean"].max())
    p_real_min, p_real_max = float(p_dec["y"].min()), float(p_dec["y"].max())

    # Calibration verdict
    diff = p_dec["y"] - p_dec["x_mean"]
    avg_diff = float(diff.mean())
    verdict = ("UNDERCONFIDENT (realized > predicted on average)"
               if avg_diff > 0.005 else
               "OVERCONFIDENT (realized < predicted on average)"
               if avg_diff < -0.005 else
               "WELL-CALIBRATED")
    verdict_color = GREEN if avg_diff > 0.005 else RED if avg_diff < -0.005 else GOLD

    p_svg = _svg_calibration(
        p_dec, x_label="predicted p (mean of decile)",
        y_label="realized win rate",
        title="Predicted p vs realized win rate (deciles, 95% Wilson CI)",
        diagonal=True, accent=GREEN,
    )

    # ── 2. G ranking ──────────────────────────────────────────────
    g_dec = decile_table(df, "G", "dollar_pnl", target_kind="mean")

    # Monotonicity test on G deciles
    y_arr = g_dec["y"].values
    rank_pairs = sum(1 for i in range(len(y_arr) - 1) if y_arr[i + 1] > y_arr[i])
    n_pairs = len(y_arr) - 1
    monotonic_pct = rank_pairs / n_pairs if n_pairs > 0 else 0
    g_verdict = ("RANKS WELL"        if monotonic_pct >= 0.78 else
                 "RANKS DIRECTIONALLY" if monotonic_pct >= 0.55 else
                 "DOES NOT RANK")

    g_svg = _svg_calibration(
        g_dec, x_label="predicted G (mean of decile)",
        y_label="avg $ P&L",
        title="Predicted G vs realized avg dollar P&L (deciles, 95% mean CI)",
        diagonal=False, accent=GOLD,
    )

    # ── 3. HTML ───────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>GEPO Calibration Report</title>
<style>
  body {{ background: {BG}; color: {LIGHT};
         font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
         margin: 0; padding: 32px; }}
  h1 {{ font-size: 22px; font-weight: 600; margin: 0 0 6px; letter-spacing: 0.3px; }}
  .subtitle {{ color: {GREY}; font-size: 13px; margin-bottom: 22px; }}
  .merc {{ color: {GOLD}; font-size: 24px; font-weight: 300; vertical-align: -2px;
            margin-right: 8px; }}
  .verdict {{ display: inline-block; padding: 6px 14px; border-radius: 6px;
              font-size: 13px; font-weight: 600; letter-spacing: 0.2px;
              border: 0.5px solid rgba(255,255,255,0.1); margin-bottom: 18px; }}
  .panel {{ background: {CARD}; border: 0.5px solid rgba(255,255,255,0.08);
            border-radius: 12px; padding: 18px; margin-bottom: 22px; }}
  .panel h2 {{ font-size: 16px; font-weight: 600; margin: 0 0 6px;
                color: {LIGHT}; letter-spacing: 0.2px; }}
  .panel .desc {{ color: {GREY}; font-size: 12px; margin-bottom: 14px;
                   line-height: 1.5; }}
  .row {{ display: flex; gap: 18px; align-items: flex-start; }}
  .row > svg {{ flex: 0 0 auto; }}
  .dt {{ font-size: 12px; border-collapse: collapse; flex: 1; }}
  .dt th {{ text-align: left; color: {GREY}; font-weight: 500;
            padding: 6px 10px; text-transform: lowercase; font-size: 10px;
            border-bottom: 1px solid rgba(255,255,255,0.1); letter-spacing: 0.5px; }}
  .dt td {{ padding: 5px 10px; border-bottom: 1px solid rgba(255,255,255,0.05); }}
  .dt tr:last-child td {{ border-bottom: none; }}
  .key {{ color: {GOLD}; }}
  .footer {{ color: {GREY}; font-size: 11px; margin-top: 16px; line-height: 1.5; }}
</style></head><body>

<h1><span class="merc">☿</span>GEPO Calibration Report</h1>
<div class="subtitle">model diagnostic on existing trade log — 2020-01-06 to 2026-05-04
&nbsp;&middot;&nbsp; n = {len(df):,}</div>

<div class="panel">
  <h2>1. Predicted <span class="key">p</span> vs realized win rate</h2>
  <div class="desc">
    Trades binned into deciles by the GEPO model's predicted win probability
    <span class="key">p</span>. Each point shows the decile's mean predicted
    <span class="key">p</span> on the x-axis and the realized win rate on the y-axis.
    Vertical bars are 95% Wilson CIs. Gold dashed line is the perfect-calibration diagonal.
    Above the line = model is <em>under</em>confident (real > predicted).
    Below = <em>over</em>confident.
  </div>
  <div class="verdict" style="background:rgba(232,232,232,0.04);color:{verdict_color}">
    Verdict: {verdict} &nbsp;&middot;&nbsp; mean (realized − predicted) = {avg_diff*100:+.2f} pp
  </div>
  <div class="row">
    {p_svg}
    {_decile_table_html(p_dec, "p", "win_rate", "win_rate")}
  </div>
</div>

<div class="panel">
  <h2>2. Predicted <span class="key">G</span> vs realized avg $ P&L</h2>
  <div class="desc">
    Same decile method but with <span class="key">G</span> on the x-axis and average
    dollar P&L on the y-axis. Tests whether higher predicted growth-rate actually
    produces higher realized return. CI bars are ±1.96·SEM of dollar P&L within decile.
    A monotonically rising curve means the signal ranks trades correctly.
  </div>
  <div class="verdict" style="background:rgba(232,232,232,0.04);color:{GOLD}">
    Verdict: {g_verdict} &nbsp;&middot;&nbsp; {rank_pairs}/{n_pairs} adjacent decile pairs in correct order
  </div>
  <div class="row">
    {g_svg}
    {_decile_table_html(g_dec, "G", "avg $ P&L", "mean")}
  </div>
</div>

<div class="footer">
  Decile boundaries determined by the empirical quantile of each variable.
  Wilson 95% intervals used for win-rate CIs; dollar P&L CIs are normal-approx
  (±1.96·SEM). Because trades cluster (avg ~5/week), per-trade independence is an
  approximation — annualized statistics are not reported on this page.
</div>

</body></html>
"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUT_HTML, "w") as f:
        f.write(html)
    print(f"\nWrote {OUT_HTML}")
    print(f"  p-calibration verdict:  {verdict}  (mean (real − pred) = {avg_diff*100:+.2f} pp)")
    print(f"  G-ranking verdict:      {g_verdict}  ({rank_pairs}/{n_pairs} pairs ascending)")
    print("\np decile table:")
    print(p_dec.to_string(index=False))
    print("\nG decile table:")
    print(g_dec.to_string(index=False))


if __name__ == "__main__":
    main()
