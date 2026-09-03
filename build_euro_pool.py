"""Build the euro-only empirical pool without touching master_pool.parquet.

Inputs:
  output/euro_parquets/<year>_euro_last*.parquet

Outputs:
  output/euro_parquets/euro_pool.parquet
  output/euro_parquets/euro_iv_rank.parquet

Safety: both output parquets are created only if missing. Use --suffix to write
another named build instead of replacing a prior one.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")
import config
from iv_rank import compute_iv_rank


def _output_paths(suffix: str) -> tuple[Path, Path]:
    suffix = suffix.strip()
    if suffix and not suffix.startswith("_"):
        suffix = "_" + suffix
    base = Path(config.OUTPUT_DIR) / "euro_parquets"
    return (
        base / f"euro_pool{suffix}.parquet",
        base / f"euro_iv_rank{suffix}.parquet",
    )


def _year_files(pattern: str) -> list[str]:
    return sorted(glob.glob(pattern))


def _safe_write(df: pd.DataFrame, path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"{path} already exists; refusing to overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists():
        raise FileExistsError(f"{tmp} already exists; remove stale temp manually")
    try:
        df.to_parquet(tmp, index=False, compression="snappy")
        if path.exists():
            raise FileExistsError(f"{path} appeared during write; refusing to overwrite")
        tmp.rename(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def process_year_parquet(path: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_parquet(path)
    df["Symbol"] = df["Symbol"].astype(str).str.upper().str.strip()
    df = df[df["Symbol"].isin(set(config.EURO_INDEX_ROOTS))].copy()
    if df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df["DataDate"] = pd.to_datetime(df["DataDate"], errors="coerce")
    df["ExpirationDate"] = pd.to_datetime(df["ExpirationDate"], errors="coerce")
    df["PutCall"] = df["PutCall"].astype(str).str.lower().str.strip()
    df["DTE"] = pd.to_numeric(df["DTE"], errors="coerce")
    df["Delta"] = pd.to_numeric(df["Delta"], errors="coerce")
    df["ImpliedVolatility"] = pd.to_numeric(df["ImpliedVolatility"], errors="coerce")
    df["StrikePrice"] = pd.to_numeric(df["StrikePrice"], errors="coerce")
    df["UnderlyingPrice"] = pd.to_numeric(df["UnderlyingPrice"], errors="coerce")

    # Same IV=0 exclusion as the pool below: a zero here is an unsolved vendor
    # quote, not a real 0% vol, and would drag the ATM IV percentile down.
    iv_rank_seed = (df.dropna(subset=["Delta", "ImpliedVolatility"])
                      .loc[lambda x: x["ImpliedVolatility"] > 0]
                      .assign(abs_delta=lambda x: x["Delta"].abs())
                      [["Symbol", "DataDate", "abs_delta", "ImpliedVolatility"]])

    expiry_close = (df[df["DataDate"] == df["ExpirationDate"]]
                    .groupby(["Symbol", "ExpirationDate"])["UnderlyingPrice"]
                    .first())
    pool = df[df["DTE"].between(1, 4)].copy()
    pool = pool[pool["DataDate"].dt.dayofweek.isin([0, 1, 2, 3])]
    # No Friday-expiry filter here. Daily-expiry index products are the point.
    pool["expiry_close"] = pool.set_index(["Symbol", "ExpirationDate"]).index.map(expiry_close.get)
    pool = pool.dropna(subset=[
        "expiry_close", "Delta", "ImpliedVolatility", "StrikePrice", "PutCall",
    ])
    # Drop non-positive IV. The vendor reports ImpliedVolatility=0 for strikes it
    # could not solve (12.8% of 2025 euro rows; 20.4% of pool rows before this
    # filter) — these are not 0% vol, they are missing values encoded as zero.
    # Left in, they pile a fifth of the pool onto a single point and collapse the
    # IV quantile edges, so empirical_runner.build_window_tables raises
    # "Bin edges must be unique: [0.0, 0.0, ...]". Filtering here keeps the fix
    # inside the euro lane; empirical_runner.py is shared with the SP100
    # production pipeline and must not be changed for a euro-only data quirk.
    pool = pool[pool["ImpliedVolatility"] > 0]
    pool["abs_delta"] = pool["Delta"].abs()
    pool["itm"] = np.where(
        pool["PutCall"].eq("put"),
        pool["expiry_close"] < pool["StrikePrice"],
        pool["expiry_close"] > pool["StrikePrice"],
    ).astype(int)
    keep = pool[[
        "Symbol", "DataDate", "ExpirationDate", "DTE", "PutCall",
        "abs_delta", "ImpliedVolatility", "itm",
    ]].copy()
    keep = keep.rename(columns={"PutCall": "putcall_norm"})
    keep["DTE"] = keep["DTE"].astype(int)
    keep["delta_bucket"] = (keep["abs_delta"] * 10).astype(int).clip(0, 9)
    keep["iv_capped"] = keep["ImpliedVolatility"].clip(upper=3.0)
    return keep, iv_rank_seed


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build euro-only empirical pool")
    p.add_argument("--pattern", default="output/euro_parquets/[0-9][0-9][0-9][0-9]_euro_last*.parquet")
    p.add_argument("--suffix", default="", help="Optional output suffix, never overwrites")
    p.add_argument("--dry-run", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    pool_path, iv_rank_path = _output_paths(args.suffix)
    if not args.dry_run:
        for path in (pool_path, iv_rank_path):
            if path.exists():
                raise FileExistsError(f"{path} already exists; refusing to overwrite")

    files = _year_files(args.pattern)
    if not files:
        raise FileNotFoundError(f"No euro parquets matched {args.pattern}")
    print(f"Found {len(files)} euro year parquets:")
    for fp in files:
        print(f"  {fp}")

    frames: list[pd.DataFrame] = []
    iv_seeds: list[pd.DataFrame] = []
    for fp in files:
        m = re.search(r"(\d{4})_euro", fp)
        label = m.group(1) if m else fp
        print(f"\n-- {label} --", flush=True)
        rows, iv_seed = process_year_parquet(fp)
        print(f"  pool rows: {len(rows):,}; IV seed rows: {len(iv_seed):,}", flush=True)
        if not rows.empty:
            frames.append(rows)
        if not iv_seed.empty:
            iv_seeds.append(iv_seed)
    if not frames:
        raise ValueError("No pool rows after filtering")

    pool = pd.concat(frames, ignore_index=True)
    all_iv_seed = pd.concat(iv_seeds, ignore_index=True) if iv_seeds else pd.DataFrame()
    iv_rank = compute_iv_rank(all_iv_seed) if not all_iv_seed.empty else pd.DataFrame()
    if not iv_rank.empty:
        pool = pool.merge(iv_rank[["Symbol", "DataDate", "iv_rank_bucket"]],
                          on=["Symbol", "DataDate"], how="left")
    else:
        pool["iv_rank_bucket"] = np.nan

    pool = pool.sort_values(["DataDate", "Symbol", "ExpirationDate", "DTE"]).reset_index(drop=True)
    print(f"\nTotal euro pool: {len(pool):,} rows")
    print(f"Expiration range: {pool['ExpirationDate'].min().date()} -> {pool['ExpirationDate'].max().date()}")
    print("Rows by symbol:")
    print(pool["Symbol"].value_counts().sort_index().to_string())

    if args.dry_run:
        print("Dry run only; no parquet files written.")
        return 0

    _safe_write(pool, pool_path)
    _safe_write(iv_rank, iv_rank_path)
    print(f"Wrote {pool_path}")
    print(f"Wrote {iv_rank_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
