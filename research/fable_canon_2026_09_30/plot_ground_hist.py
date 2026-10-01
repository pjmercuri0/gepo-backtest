"""Distribution of the value the Backtest tab displays: GROUND + carry, in basis points."""
import json
from pathlib import Path
import numpy as np, pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
ROOT = Path(__file__).resolve().parents[2]; HERE = Path(__file__).resolve().parent
SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3"
DOW = {"Mon": 4, "Tue": 3, "Wed": 2, "Thu": 1, "Fri": 1}
COL = {"Mon": "#2a78d6", "Tue": "#1baf7a", "Wed": "#eda100", "Thu": "#eb6834"}
t = pd.DataFrame(json.load(open(ROOT / "live/data/backtest_equity.json"))["trades"])
t["d3"] = t.dow.str[:3]; t["carry"] = 1e4 * 0.04 * t.d3.map(DOW) / 365.0; t["disp"] = 1e4 * t.ground + t.carry
fig, axes = plt.subplots(2, 1, figsize=(7.4, 7.6), dpi=180, facecolor=SURF)
bins = np.linspace(-5, 60, 66)
ax = axes[0]; ax.set_facecolor(SURF)
ax.hist(t.disp.clip(-5, 60), bins=bins, color="#2a78d6", edgecolor=SURF, linewidth=0.4)
med = t.disp.median(); ax.axvline(med, color=INK2, lw=1.4, ls=(0, (4, 3)))
ax.annotate(f"median {med:.1f}", (med, ax.get_ylim()[1] * 0.92), xytext=(6, 0), textcoords="offset points", fontsize=8.5, color=INK)
ax.set_title(f"Backtest: GROUND + carry on {len(t):,} trades  ·  5% below {t.disp.quantile(.05):.1f}, 95% below {t.disp.quantile(.95):.0f}, max {t.disp.max():.0f}",
             loc="left", fontsize=9.5, color=INK, pad=6)
ax = axes[1]; ax.set_facecolor(SURF)
for d in ("Mon", "Tue", "Wed", "Thu"):
    x = t[t.d3 == d]
    ax.hist(x.disp.clip(-5, 60), bins=bins, histtype="step", linewidth=2, color=COL[d],
            label=f"{d} · {int(DOW[d])} DTE · carry {x.carry.iloc[0]:.2f}")
ax.set_title("same values by entry weekday — carry is a per-day constant, so each weekday has its own floor",
             loc="left", fontsize=9.5, color=INK, pad=6)
ax.legend(frameon=False, fontsize=8.5, labelcolor=INK)
for ax in axes:
    ax.grid(axis="y", color=GRID, lw=1); ax.set_axisbelow(True)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8.5); ax.set_xlim(-5, 60)
axes[1].set_xlabel("GROUND + carry (bps), clipped at 60 for the plot", color=INK2, fontsize=9)
fig.text(0.055, 0.008, f"{100*(t.disp>60).mean():.1f}% of trades sit above 60 bps (max {t.disp.max():.0f}); raw GROUND is negative on {100*(t.ground<0).mean():.0f}% of them.",
         fontsize=8, color=INK2)
plt.tight_layout(rect=(0, 0.025, 1, 1)); out = HERE / "ground_carry_hist.png"; fig.savefig(out, facecolor=SURF); print(out)
