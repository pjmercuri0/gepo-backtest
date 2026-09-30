"""Three small multiples of the strategy C k sweep: $-Sharpe, max drawdown, final equity (IS vs OOT)."""
import sys
from pathlib import Path
import pandas as pd, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
HERE = Path(__file__).resolve().parent
df = pd.read_csv(HERE / "k_sweep_C_ext.csv").sort_values("k")
df["IS_dd"] = -df.IS_dd; df["OOT_dd"] = -df.OOT_dd   # depth, positive: higher = worse
SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e8e7e3"
C_IS, C_OOT = "#2a78d6", "#eb6834"
panels = [("$-Sharpe (weekly P&L)", "IS_dsh", "OOT_dsh", "{:.2f}"),
          ("Max drawdown DEPTH, qty 2 from $10k  (higher = worse)", "IS_dd", "OOT_dd", "{:.0f}%"),
          ("Yield on dollars risked", "IS_yield", "OOT_yield", "{:.1f}%")]
fig, axes = plt.subplots(3, 1, figsize=(7.2, 10.5), dpi=180, facecolor=SURF, sharex=True)
fig.suptitle("Strategy C, k sweep: GROUND = EV · e^(−k·D_ent)\npooled top 6/day by GROUND, 252-session start, booked 1.04× model",
             color=INK, fontsize=10.5, x=0.06, ha="left", y=0.99)
for ax, (title, a, b, fmt) in zip(axes, panels):
    ax.set_facecolor(SURF)
    for col, color, name in ((a, C_IS, "IS 2021-25"), (b, C_OOT, "OOT 2026")):
        y = df[col].values
        ax.plot(df.k, y, color=color, lw=2, solid_joinstyle="round", solid_capstyle="round", label=name, zorder=3)
        ax.scatter(df.k, y, s=34, color=color, edgecolor=SURF, linewidth=2, zorder=4)
        ax.annotate(fmt.format(y[-1]), (df.k.values[-1], y[-1]), xytext=(6, 0), textcoords="offset points", va="center", fontsize=8.5, color=INK)
    k4 = df[df.k == 4]
    ax.axvline(4, color=GRID, lw=1, zorder=1); ax.axvline(24, color=GRID, lw=1, zorder=1)
    ax.set_title(title, loc="left", fontsize=10, color=INK, pad=6)
    ax.grid(axis="y", color=GRID, lw=1); ax.set_axisbelow(True)
    for s in ("top", "right"): ax.spines[s].set_visible(False)
    for s in ("left", "bottom"): ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=8.5)
    if "%" in fmt: ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:.1f}%" if "Yield" in title else f"{v:.0f}%"))
    axes[0].legend(frameon=False, fontsize=9, loc="center right", labelcolor=INK)
axes[0].text(4, axes[0].get_ylim()[1], " k=4 canon", fontsize=8, color=INK2, va="top")
axes[0].text(24, axes[0].get_ylim()[1], " k=24", fontsize=8, color=INK2, va="top")
axes[-1].set_xlabel("k", color=INK2); axes[-1].set_xticks(df.k.values)
fig.text(0.06, 0.008, f"Overlap of picks with k=4: {df.overlap_k4.min():.0f}% to 100%. OOT is 808 trades.", fontsize=8, color=INK2)
plt.tight_layout(rect=(0, 0.02, 1, 0.955))
out = HERE / "k_sweep_C.png"; fig.savefig(out, facecolor=SURF); print(out)
