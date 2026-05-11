# GEPO Backtest — Bucket Analysis

Descriptive analysis on the existing trade log to identify where edge
lives in the parameter space. Read-only on the trade log; backtest
logic untouched.

## Trade log

`../output/all_trades.csv` (1,636 rows after current code state).
Source config: Greek-only baseline + SPY 100d regime + quartile fills + qty 2.

## Run order

```bash
python3 step1_headline.py        # output/headline_validation.txt
python3 step2_buckets.py         # output/bucket_summary.csv (1-D, all dims)
python3 step3_interactions.py    # output/interaction_tables.xlsx (2-way)
python3 step4_calibration.py     # output/calibration_report.html (key)
python3 step5_dashboard.py       # output/diagnostic_report.html (full)
```

## External data fetched (cached)

- `vix_daily.parquet` — official CBOE VIX daily history (cached on first run)
- SPY daily — uses repo's existing `data/spy_us_d.csv`
- Per-ticker IV percentile rank — derived from the options parquet, trailing 52 weekly samples (~ 252 trading days)

## Bucket dimensions

1. `short_delta` — fixed bins [0.35-0.40, 0.40-0.45, 0.45-0.50, 0.50-0.55]
2. `credit_ratio` — quartiles
3. `theta_credit_ratio` — quartiles
4. `p` (predicted win prob) — quartiles + deciles for calibration
5. `G` (predicted growth) — quartiles + deciles for ranking check
6. `DKL` — quartiles
7. `spread_type` — bull_put / bear_call
8. `vix_regime` — VIX <15, 15-20, 20-25, ≥25 on entry_date
9. `iv_pctile_q` — per-ticker IV rank vs trailing 52 weeks (winsor 99%)
10. `spy_gap` — Friday→Monday SPY gap_pct, 5 buckets

## Sample-size policy

Cells with `n < 15` are marked "insufficient sample" and excluded from
metric computation. Sweet-spot search requires `n ≥ 30`.

## Caveats

- Per-trade Sharpe annualized via × √52 is approximate; trades cluster
  (avg ~5/week), so per-trade independence is violated.
- Wilson 95% intervals on win-rate; normal-approx (±1.96·SEM) on
  dollar-pnl means.
- IV percentile rank uses Monday-sampled data only (the parquet was
  filtered to `ENTRY_DOW = 0`).

## Requirements

```
pandas>=1.3
numpy>=1.20
scipy>=1.7
openpyxl>=3.0
yfinance>=0.2
requests>=2.28
```

Already installed via `python3 -m pip install --user -r requirements.txt`
in the parent project's environment.
