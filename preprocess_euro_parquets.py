"""Build euro-only option parquets without touching canonical GEPO parquets.

Reads Discount Option Data CSVs from data/DG_YYYYMonth/ and writes only under:

  output/euro_parquets/<year>_euro_last.parquet
  output/euro_parquets/<year>_euro_expiry.parquet

The default universe is config.EURO_INDEX_ROOTS: cash-settled index option
roots only. ETF options such as SPY/QQQ/IWM are intentionally excluded because
they are American-style and physically settled.

Safety: output files must not already exist. Use --suffix for a second build
instead of replacing a prior parquet.
"""
from __future__ import annotations

import argparse
import glob
import io
import os
import re
import subprocess
import sys
from functools import partial
from multiprocessing import Pool
from pathlib import Path

import pandas as pd
from tqdm import tqdm

import config


# Keep every vendor field the research lane could need. Re-reading 59 GB of raw
# CSV to recover a dropped column later is far more expensive than carrying it
# now, so this deliberately retains the FULL greek set plus liquidity/depth:
#   Rho      — completes the greeks (Delta/Gamma/Vega/Theta/Rho)
#   Volume   — liquidity gating (TODO #1); live templates already use
#              short_volume/long_volume
#   AskSize/BidSize — quoted depth, needed for the fill-quality work (TODO #10)
# OptionKey is the only vendor column intentionally omitted: it is just
# Symbol+Expiry+PutCall+Strike+DataDate concatenated, i.e. fully derivable.
REQUIRED_COLS = [
    "Symbol", "ExpirationDate", "AskPrice", "AskSize", "BidPrice", "BidSize",
    "LastPrice", "StrikePrice", "PutCall", "Volume", "OpenInterest",
    "UnderlyingPrice", "ImpliedVolatility",
    "Delta", "Gamma", "Vega", "Rho", "Theta",
    "DataDate",
]
EXPIRY_COLS = ["Symbol", "ExpirationDate", "UnderlyingPrice", "DataDate"]
NUMERIC_COLS = [
    "AskPrice", "AskSize", "BidPrice", "BidSize", "LastPrice", "StrikePrice",
    "Volume", "OpenInterest", "UnderlyingPrice", "ImpliedVolatility",
    "Delta", "Gamma", "Vega", "Rho", "Theta",
]
DEDUP_COLS = ["Symbol", "ExpirationDate", "StrikePrice", "PutCall", "DataDate"]


def _month_files(data_dir: str, start: pd.Timestamp, end: pd.Timestamp) -> list[str]:
    months = pd.date_range(start.replace(day=1), end, freq="MS")
    files: list[str] = []
    for d in months:
        folder = os.path.join(data_dir, d.strftime("DG_%Y%B"))
        if os.path.isdir(folder):
            files.extend(sorted(glob.glob(os.path.join(folder, "Greek_*_OData*.csv"))))
    return files


def _read_csv(path: str, usecols: list[str]) -> pd.DataFrame:
    try:
        return pd.read_csv(path, dtype=str, usecols=usecols)
    except Exception:
        try:
            return pd.read_csv(path, dtype=str, usecols=usecols,
                               engine="python", on_bad_lines="skip")
        except Exception as exc:
            print(f"  skip {os.path.basename(path)}: {exc}", flush=True)
            return pd.DataFrame()


def _read_csv_symbols(path: str, usecols: list[str],
                      symbols: set[str]) -> pd.DataFrame:
    """Read only the rows whose Symbol is in `symbols`, filtering as TEXT first.

    The euro universe is ~5 roots out of ~5,641 symbols per file, but parsing the
    whole CSV into pandas and filtering afterwards costs the full 59 GB anyway:
    the 2025 dry run spent 17 min on the options pass and 7 min on the expiry
    pass, and half of that was the OData1 files (A-KZR), which cannot contain
    NDX/RUT/RUTW/SPX/SPXW at all.

    grep discards >99% of the bytes before the parser sees them. Row format is
    `OptionKey,Symbol,...` and OptionKey never contains a comma, so anchoring
    `^[^,]*,<SYM>,` matches the Symbol field exactly and cannot hit a substring
    of some other column. Falls back to the plain full read if grep is missing.
    """
    if not symbols:
        return _read_csv(path, usecols)
    # Anchor at position 0, not at the Symbol field. OptionKey is
    # Symbol+Expiry+P/C+Strike+DataDate concatenated, so every row for SPXW
    # begins "SPXW...". A `^` anchor lets grep reject on the first character
    # instead of scanning each line for a comma (1.0s vs 1.5s per 116 MB file;
    # BSD grep -F is 4x slower still, so fixed-string matching is not the win
    # it is on GNU). Reducing to minimal prefixes ({NDX,RUT,RUTW,SPX,SPXW} ->
    # {NDX,RUT,SPX}) shrinks the alternation further.
    #
    # This is deliberately a SUPERSET filter: "^SPX" also admits SPXC/SPXL/etc.
    # Both callers re-filter on the exact Symbol field afterwards, so the extra
    # rows are dropped. Verified on 4 files spread across 2025: zero true rows
    # missed. Correctness never depends on the prefilter being tight, only on
    # it never being too narrow.
    prefixes = sorted(
        s for s in symbols
        if not any(o != s and s.startswith(o) for o in symbols)
    )
    pattern = "^(" + "|".join(re.escape(p) for p in prefixes) + ")"
    try:
        with open(path, "r") as fh:
            header = fh.readline()
        if not header:
            return pd.DataFrame()
        proc = subprocess.run(["grep", "-E", pattern, path],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        # grep exit 1 == no matching rows in this file (normal: OData1 holds no
        # index roots). Exit >1 is a real error -> fall back to the full read.
        if proc.returncode > 1:
            return _read_csv(path, usecols)
        body = proc.stdout.decode("utf-8", "replace")
        if not body.strip():
            return pd.DataFrame()
        return pd.read_csv(io.StringIO(header + body), dtype=str, usecols=usecols)
    except FileNotFoundError:
        return _read_csv(path, usecols)
    except Exception as exc:
        print(f"  prefilter fallback {os.path.basename(path)}: {exc}", flush=True)
        return _read_csv(path, usecols)


def _normalize_options(df: pd.DataFrame, symbols: set[str],
                       dte_min: int, dte_max: int) -> pd.DataFrame:
    if df.empty:
        return df
    df["Symbol"] = df["Symbol"].astype(str).str.upper().str.strip()
    df = df[df["Symbol"].isin(symbols)]
    if df.empty:
        return df

    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["DataDate"] = pd.to_datetime(df["DataDate"], errors="coerce")
    df["ExpirationDate"] = pd.to_datetime(df["ExpirationDate"], errors="coerce")
    df["PutCall"] = df["PutCall"].astype(str).str.lower().str.strip()
    df["MidPrice"] = (df["AskPrice"] + df["BidPrice"]) / 2.0
    df["AbsDelta"] = df["Delta"].abs()
    df["DTE"] = (df["ExpirationDate"] - df["DataDate"]).dt.days

    df = df[df["DataDate"].dt.dayofweek.between(0, 4)]
    df = df[df["DTE"].between(dte_min, dte_max)]
    df = df[df["UnderlyingPrice"] > 0]
    df = df.dropna(subset=[
        "DataDate", "ExpirationDate", "BidPrice", "AskPrice", "LastPrice",
        "StrikePrice", "Delta", "UnderlyingPrice",
    ])
    if df.empty:
        return df
    return df.drop_duplicates(subset=DEDUP_COLS)


def _process_options_file(path: str, symbols: set[str],
                          dte_min: int, dte_max: int) -> pd.DataFrame:
    return _normalize_options(_read_csv_symbols(path, REQUIRED_COLS, symbols),
                              symbols, dte_min, dte_max)


def _process_expiry_file(path: str, symbols: set[str]) -> pd.DataFrame:
    df = _read_csv_symbols(path, EXPIRY_COLS, symbols)
    if df.empty:
        return df
    df["Symbol"] = df["Symbol"].astype(str).str.upper().str.strip()
    df = df[df["Symbol"].isin(symbols)]
    if df.empty:
        return df
    df["DataDate"] = pd.to_datetime(df["DataDate"], errors="coerce")
    df["ExpirationDate"] = pd.to_datetime(df["ExpirationDate"], errors="coerce")
    df["UnderlyingPrice"] = pd.to_numeric(df["UnderlyingPrice"], errors="coerce")
    return df.dropna(subset=["DataDate", "ExpirationDate", "UnderlyingPrice"])


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


def _build_expiry_table(files: list[str], symbols: set[str], needed: pd.DataFrame,
                        workers: int) -> pd.DataFrame:
    chunks: list[pd.DataFrame] = []
    worker = partial(_process_expiry_file, symbols=symbols)
    with Pool(workers) as pool:
        for chunk in tqdm(pool.imap_unordered(worker, files, chunksize=4),
                          total=len(files), desc="Expiry"):
            if not chunk.empty:
                chunks.append(chunk)
    if not chunks:
        raise ValueError("No expiry data after filtering")

    full = pd.concat(chunks, ignore_index=True)
    expiry = full[full["DataDate"] == full["ExpirationDate"]].copy()
    expiry = expiry.rename(columns={"UnderlyingPrice": "ExpiryPrice"})
    expiry = expiry.drop_duplicates(subset=["Symbol", "ExpirationDate"])

    have = expiry[["Symbol", "ExpirationDate"]].drop_duplicates()
    missing = needed.merge(have, on=["Symbol", "ExpirationDate"],
                           how="left", indicator=True)
    missing = missing[missing["_merge"] == "left_only"].drop(columns="_merge")
    if missing.empty:
        return expiry

    print(f"Missing expiry-day spots: {len(missing):,}; using latest prior spot.", flush=True)
    spots = (full[["Symbol", "DataDate", "UnderlyingPrice"]]
             .drop_duplicates(subset=["Symbol", "DataDate"])
             .sort_values(["Symbol", "DataDate"]))
    filled = []
    for sym, exp_date in missing.itertuples(index=False):
        cand = spots[(spots["Symbol"] == sym) & (spots["DataDate"] <= exp_date)]
        if cand.empty:
            continue
        row = cand.iloc[-1]
        filled.append({
            "Symbol": sym,
            "ExpirationDate": exp_date,
            "ExpiryPrice": float(row["UnderlyingPrice"]),
            "DataDate": row["DataDate"],
        })
    if filled:
        expiry = pd.concat([expiry, pd.DataFrame(filled)], ignore_index=True)
    return (expiry[["Symbol", "ExpirationDate", "ExpiryPrice", "DataDate"]]
            .drop_duplicates(subset=["Symbol", "ExpirationDate"])
            .sort_values(["ExpirationDate", "Symbol"])
            .reset_index(drop=True))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build isolated euro-only parquets")
    p.add_argument("year", type=int)
    p.add_argument("--start", default=None, help="YYYY-MM-DD, defaults to Jan 1 of year")
    p.add_argument("--end", default=None, help="YYYY-MM-DD, defaults to Dec 31 of year")
    p.add_argument("--symbols", default=None,
                   help="Comma-separated override. Defaults to config.EURO_INDEX_ROOTS.")
    p.add_argument("--dte-min", type=int, default=0)
    p.add_argument("--dte-max", type=int, default=8)
    p.add_argument("--suffix", default="",
                   help="Optional output suffix, e.g. _2025q1. Never overwrites.")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--dry-run", action="store_true",
                   help="Read and summarize, but do not write parquets.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    start = pd.Timestamp(args.start or f"{args.year}-01-01")
    end = pd.Timestamp(args.end or f"{args.year}-12-31")
    symbols = ({s.strip().upper() for s in args.symbols.split(",") if s.strip()}
               if args.symbols else set(config.EURO_INDEX_ROOTS))
    suffix = args.suffix.strip()
    if suffix and not suffix.startswith("_"):
        suffix = "_" + suffix

    out_dir = Path(config.OUTPUT_DIR) / "euro_parquets"
    options_path = out_dir / f"{args.year}_euro_last{suffix}.parquet"
    expiry_path = out_dir / f"{args.year}_euro_expiry{suffix}.parquet"
    if not args.dry_run:
        for path in (options_path, expiry_path):
            if path.exists():
                raise FileExistsError(f"{path} already exists; refusing to overwrite")

    files = _month_files(config.DATA_DIR, start, end)
    if not files:
        raise FileNotFoundError(f"No vendor CSVs found for {start.date()} -> {end.date()}")
    workers = max(1, min(args.workers, len(files)))
    print(f"Euro preprocess {start.date()} -> {end.date()}")
    print(f"Symbols: {', '.join(sorted(symbols))}")
    print(f"DTE: {args.dte_min} -> {args.dte_max}; files: {len(files)}; workers: {workers}")
    print(f"Output dir: {out_dir}")

    chunks: list[pd.DataFrame] = []
    worker = partial(_process_options_file, symbols=symbols,
                     dte_min=args.dte_min, dte_max=args.dte_max)
    with Pool(workers) as pool:
        for chunk in tqdm(pool.imap_unordered(worker, files, chunksize=4),
                          total=len(files), desc="Options"):
            if not chunk.empty:
                chunks.append(chunk)
    if not chunks:
        raise ValueError("No euro option rows after filtering")

    df = (pd.concat(chunks, ignore_index=True)
          .drop_duplicates(subset=DEDUP_COLS)
          .sort_values(["DataDate", "Symbol", "ExpirationDate", "StrikePrice", "PutCall"])
          .reset_index(drop=True))
    needed = df[["Symbol", "ExpirationDate"]].drop_duplicates()

    print(f"Rows: {len(df):,}")
    print(f"Dates: {df['DataDate'].min().date()} -> {df['DataDate'].max().date()}")
    print("Rows by symbol:")
    print(df["Symbol"].value_counts().sort_index().to_string())
    print("Expiry weekdays:")
    print(df["ExpirationDate"].dt.day_name().value_counts().to_string())

    exp_files = _month_files(config.DATA_DIR, start, end + pd.Timedelta(days=31))
    expiry = _build_expiry_table(exp_files, symbols, needed, workers)
    print(f"Expiry rows: {len(expiry):,}")

    if args.dry_run:
        print("Dry run only; no parquet files written.")
        return 0

    _safe_write_parquet(df, options_path)
    _safe_write_parquet(expiry, expiry_path)
    print(f"Wrote {options_path} ({options_path.stat().st_size / 1e6:.1f} MB)")
    print(f"Wrote {expiry_path} ({expiry_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
