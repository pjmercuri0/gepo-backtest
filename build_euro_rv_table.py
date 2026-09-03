"""Build the euro-lane realized-volatility lookup.

`output/rv_table.parquet` only ever covered `SP100_TICKERS`, so it holds RV for
SPXW and RUTW and for no other index root. The euro backtest only *looks up*
that table, so SPX, NDX and RUT score nothing at all (SESSION_HANDOFF §0.17
finding 1).

RV is recoverable from the euro parquets themselves: `UnderlyingPrice` is
retained per (Symbol, DataDate), which is the only input `rv_table` needs. This
script derives the table with the production estimator and writes it under
`output/euro_parquets/`.

**It does not touch `output/rv_table.parquet`.** That file is live production
data and is opened read-only here, purely to cross-check the shared roots.

Caveat carried forward from §0.17 finding 3: RUT/RUTW `UnderlyingPrice` looks
corrupted in the vendor data, so their derived RV is wrong here for the same
reason it is wrong in production. This script reports the symptom; it does not
repair it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import config
import rv_table

PROD_RV = "output/rv_table.parquet"
# rv_table needs WINDOW_DAYS of returns to fill a window; values before that are
# built from a short window and are not comparable to a table that had prior
# years to warm up on.
WARMUP_ROWS = rv_table.WINDOW_DAYS + 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build the euro-lane RV lookup table")
    p.add_argument("--years", default=None,
                   help="Comma-separated years. Default: every euro year parquet found.")
    p.add_argument("--symbols", default=None,
                   help="Comma-separated root override. Default: whatever is in the parquets.")
    p.add_argument("--suffix", default="",
                   help="Optional output suffix. Never overwrites an existing table.")
    p.add_argument("--dry-run", action="store_true",
                   help="Derive and report, but do not write the parquet.")
    return p.parse_args()


def _suffix(raw: str) -> str:
    raw = (raw or "").strip()
    if raw and not raw.startswith("_"):
        raw = "_" + raw
    return raw


def _safe_write_parquet(df: pd.DataFrame, path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"{path} already exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists():
        raise FileExistsError(f"{tmp} already exists; remove stale temp file manually")
    try:
        df.to_parquet(tmp, index=False, compression="snappy")
        if path.exists():
            raise FileExistsError(f"{path} appeared during write; refusing to overwrite")
        tmp.rename(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _find_year_files(out_dir: Path, years: list[int] | None) -> list[Path]:
    files = sorted(out_dir.glob("*_euro_last*.parquet"))
    if years:
        wanted = {str(y) for y in years}
        files = [f for f in files if f.name.split("_")[0] in wanted]
    return files


def load_spots(files: list[Path], symbols: set[str] | None) -> pd.DataFrame:
    """Read only the three columns rv_table needs, deduped to one row per day."""
    frames = []
    for fp in files:
        df = pd.read_parquet(fp, columns=["Symbol", "DataDate", "UnderlyingPrice"])
        if symbols:
            df = df[df["Symbol"].isin(symbols)]
        frames.append(df.drop_duplicates(subset=["Symbol", "DataDate"]))
        print(f"  {fp.name}: {len(frames[-1]):,} symbol-days", flush=True)
    if not frames:
        raise FileNotFoundError("No euro year parquets matched")
    spot = pd.concat(frames, ignore_index=True)
    spot["DataDate"] = pd.to_datetime(spot["DataDate"], errors="coerce")
    before = len(spot)
    spot = config.drop_bad_spot_days(spot)
    if len(spot) < before:
        print(f"  dropped {before - len(spot):,} known-bad vendor spot rows", flush=True)
    return (spot.dropna(subset=["DataDate", "UnderlyingPrice"])
                .drop_duplicates(subset=["Symbol", "DataDate"])
                .sort_values(["Symbol", "DataDate"])
                .reset_index(drop=True))


def cross_check(euro: pd.DataFrame) -> None:
    """Compare shared roots against production, read-only. Never writes."""
    try:
        prod = pd.read_parquet(PROD_RV)
    except FileNotFoundError:
        print(f"WARNING: {PROD_RV} not found; skipping cross-check")
        return
    prod["DataDate"] = pd.to_datetime(prod["DataDate"], errors="coerce")
    merged = euro.merge(prod, on=["Symbol", "DataDate"], how="inner",
                        suffixes=("_euro", "_prod")).dropna(
                            subset=["rv_30d_euro", "rv_30d_prod"])
    shared = sorted(merged["Symbol"].unique())
    if not shared:
        print("No overlapping (Symbol, DataDate) with production; nothing to cross-check")
        return

    print(f"\nCross-check vs {PROD_RV} (read-only) — shared roots: {', '.join(shared)}")
    print(f"{'root':<7}{'n':>7}{'corr':>10}{'mean_euro':>12}{'mean_prod':>12}{'max_absdiff':>14}")
    for sym in shared:
        g = merged[merged["Symbol"] == sym]
        # Drop the warm-up rows: production had prior years to fill its window,
        # this table starts cold at the first euro parquet.
        g = g.sort_values("DataDate").iloc[WARMUP_ROWS:]
        if len(g) < 2:
            print(f"{sym:<7}{len(g):>7}{'n/a':>10}")
            continue
        corr = g["rv_30d_euro"].corr(g["rv_30d_prod"])
        diff = (g["rv_30d_euro"] - g["rv_30d_prod"]).abs().max()
        print(f"{sym:<7}{len(g):>7}{corr:>10.4f}{g['rv_30d_euro'].mean():>12.4f}"
              f"{g['rv_30d_prod'].mean():>12.4f}{diff:>14.2e}")


def report_symbols(euro: pd.DataFrame, spot: pd.DataFrame) -> None:
    print(f"\n{'root':<7}{'days':>7}{'rv_n':>8}{'rv_mean':>10}{'rv_max':>10}"
          f"{'spot_min':>11}{'spot_max':>11}")
    for sym in sorted(euro["Symbol"].unique()):
        g = euro[euro["Symbol"] == sym]
        s = spot[spot["Symbol"] == sym]["UnderlyingPrice"]
        rv = g["rv_30d"].dropna()
        print(f"{sym:<7}{len(g):>7}{len(rv):>8}{rv.mean():>10.4f}{rv.max():>10.4f}"
              f"{s.min():>11.2f}{s.max():>11.2f}")


def main() -> int:
    args = parse_args()
    years = ([int(y.strip()) for y in args.years.split(",") if y.strip()]
             if args.years else None)
    symbols = ({s.strip().upper() for s in args.symbols.split(",") if s.strip()}
               if args.symbols else None)

    out_dir = Path(config.OUTPUT_DIR) / "euro_parquets"
    out_path = out_dir / f"euro_rv_table{_suffix(args.suffix)}.parquet"
    if not args.dry_run and out_path.exists():
        raise FileExistsError(f"{out_path} already exists; refusing to overwrite")

    files = _find_year_files(out_dir, years)
    if not files:
        raise FileNotFoundError(f"No *_euro_last*.parquet under {out_dir}")
    print(f"Source parquets: {len(files)}")

    spot = load_spots(files, symbols)
    print(f"Spot rows: {len(spot):,}  "
          f"({spot['DataDate'].min().date()} -> {spot['DataDate'].max().date()})")

    euro = rv_table.compute_rv_table(spot)
    print(f"RV rows: {len(euro):,}  non-null: {euro['rv_30d'].notna().sum():,}  "
          f"(window={rv_table.WINDOW_DAYS}d, min_obs={rv_table.MIN_OBS})")

    report_symbols(euro, spot)
    cross_check(euro)

    if args.dry_run:
        print("\nDry run only; no parquet written.")
        return 0

    _safe_write_parquet(euro, out_path)
    print(f"\nWrote {out_path} ({out_path.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
