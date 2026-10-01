"""Equity curves of the site book's sizing arms, Backtest and OOT, straight from the deployed payloads."""
import json
from pathlib import Path
import pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt, matplotlib.ticker as mt
ROOT = Path(__file__).resolve().parents[2]; HERE = Path(__file__).resolve().parent
SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3"
ARMS = [("halfk", "½ Kelly (cap 5)", "#4a3aa7", 2.2), ("quarterk", "¼ Kelly (cap 5)", "#eb6834", 2.2),
        ("strategy", "qty 2", "#1baf7a", 1.8), ("qty1", "qty 1", "#2a78d6", 1.8), ("spy", "SPY", "#888780", 1.5)]
fig, axes = plt.subplots(2, 1, figsize=(7.4, 8.6), dpi=180, facecolor=SURF)
for ax, (f, title) in zip(axes, (("backtest", "Backtest 2021-25"), ("oot", "OOT 2026"))):
    p = json.load(open(ROOT / f"live/data/{f}_equity.json")); pts = pd.DataFrame(p["points"]); pts["date"] = pd.to_datetime(pts.date)
    s = p["summary"]; ax.set_facecolor(SURF)
    for key, lab, col, lw in ARMS:
        ax.plot(pts.date, pts[key], color=col, lw=lw, solid_capstyle="round", label=lab,
                ls=(0, (4, 3)) if key == "spy" else "-", zorder=3 if key.endswith("k") else 2)
        ax.annotate(f"${pts[key].iloc[-1]:,.0f}", (pts.date.iloc[-1], pts[key].iloc[-1]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8, color=col)
    sh = {k: s[f"{k}_sharpe_dollar"] for k, _, _, _ in ARMS}; dd = {k: s[f"{k}_max_dd"] for k, _, _, _ in ARMS}
    ax.set_title(f"{title}  —  $-Sharpe ½K {sh['halfk']:.2f} / ¼K {sh['quarterk']:.2f} / qty2 {sh['strategy']:.2f}"
                 f"   ·   max DD ½K {dd['halfk']:.0f}% / ¼K {dd['quarterk']:.0f}% / qty2 {dd['strategy']:.0f}%",
                 loc="left", fontsize=9, color=INK, pad=6)
    ax.grid(axis="y", color=GRID, lw=1); ax.set_axisbelow(True)
    for sp in ("top", "right"): ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"): ax.spines[sp].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8); ax.yaxis.set_major_formatter(mt.FuncFormatter(lambda v, _: f"${v/1000:,.0f}k"))
    ax.margins(x=0.10)
axes[0].legend(frameon=False, fontsize=8.5, loc="upper left", labelcolor=INK, ncol=2)
fig.suptitle("Site book sizing arms — strategy C, k=24, OI ≥ 1, 1.00× model fills, $10k start",
             color=INK, fontsize=10.5, x=0.055, ha="left", y=0.985)
fig.text(0.055, 0.008, "Kelly stake is carry-adjusted (CARRY_RATE 4% × DTE/365 on the capital at risk); qty capped at 5.",
         fontsize=8, color=INK2)
plt.tight_layout(rect=(0, 0.02, 1, 0.96)); out = HERE / "kelly_arms_equity.png"; fig.savefig(out, facecolor=SURF); print(out)
