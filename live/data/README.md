# live/data — snapshot scoring + drift comparison

## Directory layout

```
live/data/
├── scored/                                  # output of analyze_chain.py
│   └── YYYY-MM-DD/
│       ├── HHMM.parquet                     # canonical scored candidates (joinable)
│       ├── HHMM.csv                         # same data, human-readable
│       └── HHMM.html                        # top picks + summary report
└── drift/                                   # output of drift_compare.py
    └── {b_date}_{b_time}__{a_date}_{a_time}.{csv,html}
```

Snapshot time is HHMM in 24h Eastern Time. EOD = `1600`. Intraday OPRA snapshots
should land at `1530`, `1545`, `1600` etc.

## Workflows

### One-off scoring of a daily EOD chain

```bash
python3 analyze_chain.py --in data/DG_YYYYMMDD.zip
# writes live/data/scored/YYYY-MM-DD/1600.{parquet,csv,html}
```

### Score an OPRA intraday snapshot

```bash
python3 analyze_chain.py --in data/opra/2026-05-20/1530.parquet --time 1530
# writes live/data/scored/2026-05-20/1530.{parquet,csv,html}
```

### Drift between two snapshots (signal-time vs fill-time)

```bash
python3 drift_compare.py \
  --before live/data/scored/2026-05-20/1530.parquet \
  --after  live/data/scored/2026-05-20/1600.parquet
# writes live/data/drift/2026-05-20_1530__2026-05-20_1600.{csv,html}
```

### Drift carry between days (held position re-pricing)

```bash
python3 drift_compare.py \
  --before live/data/scored/2026-05-19/1600.parquet \
  --after  live/data/scored/2026-05-20/1600.parquet
```

## OPRA-adapter spec

When OPRA intraday data starts flowing, the chain snapshot must be reduced to a
flat row-per-option DataFrame with at minimum these columns:

| column              | type     | notes                                       |
|---------------------|----------|---------------------------------------------|
| `Symbol`            | str      | underlying ticker, e.g. "AAPL"              |
| `ExpirationDate`    | datetime | option expiry                               |
| `StrikePrice`       | float    | strike, in $                                |
| `PutCall`           | str      | "call" or "put" (lower-case)                |
| `BidPrice`          | float    | NBBO bid at snapshot timestamp              |
| `AskPrice`          | float    | NBBO ask at snapshot timestamp              |
| `OpenInterest`      | int      | most recent OI value (daily settles)        |
| `ImpliedVolatility` | float    | as fraction, e.g. 0.42 for 42%              |
| `Delta`             | float    | signed (calls > 0, puts < 0)                |
| `UnderlyingPrice`   | float    | underlying mid at snapshot                  |
| `DataDate`          | datetime | the snapshot calendar date                  |

Optional but useful: `Gamma`, `Vega`, `Theta`, `LastPrice`, `Volume`, `BidSize`,
`AskSize`.

Save as parquet to `data/opra/YYYY-MM-DD/HHMM.parquet`. Then point
`analyze_chain.py --in` at it; the loader auto-detects the format.

If the OPRA stream lands in a different shape, write a tiny adapter that
reshapes to this schema and emits a parquet. Keep the adapter local to the
ingestion path; downstream tools never look at the raw OPRA format.

## What lives where

| concern                                | tool                          |
|----------------------------------------|-------------------------------|
| score one snapshot                     | `analyze_chain.py`            |
| compare two snapshots (drift)          | `drift_compare.py`            |
| compare snapshot to backtest record    | TODO once we have a use case  |
| compare snapshot to live ranker output | TODO once live ranker archives|

The first two are sufficient to investigate:
- **Intraday signal-to-fill drift** (`1530` vs `1600` on the same day)
- **Overnight carry drift** (`1600` yesterday vs `1600` today)
- **Bid/ask half-spread cost at any one timestamp** (single-snapshot field)

When the live ranker starts archiving its picks at signal time, we add a third
tool that joins ranker output to the corresponding OPRA snapshot and reports
per-pick drift.
