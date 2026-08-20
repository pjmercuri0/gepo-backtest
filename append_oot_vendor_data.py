"""Append new vendor CSV rows into the existing 2026 OOT combined parquet.

This intentionally updates output/2026_sp500_last_oot_combined.parquet in
place, after creating a timestamped backup. It does not create a replacement
OOT parquet name.
"""

from __future__ import annotations

import argparse
import calendar
import re
import shutil
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

import config
from fast_preprocess import COLS, DAILY_EXPIRY, SP500


OUT_PATH = Path("output/2026_sp500_last_oot_combined.parquet")
DEDUP_COLS = ["Symbol", "DataDate", "ExpirationDate", "StrikePrice", "PutCall"]
NUMERIC_COLS = [
    "AskPrice",
    "BidPrice",
    "LastPrice",
    "StrikePrice",
    "OpenInterest",
    "UnderlyingPrice",
    "ImpliedVolatility",
    "Delta",
    "Gamma",
    "Vega",
    "Theta",
]
FILE_DATE_RE = re.compile(r"Greek_(\d{8})_")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Append July/August/etc vendor data to the existing OOT combined parquet."
    )
    p.add_argument("--year", type=int, default=2026)
    p.add_argument(
        "--start",
        default=None,
        help="First DataDate to append. Default: one day after current OOT parquet max DataDate.",
    )
    p.add_argument(
        "--end",
        default=None,
        help="Last DataDate to append. Default: latest DataDate available in scanned files.",
    )
    p.add_argument("--dte-max", type=int, default=8, help="Keep rows with 0 <= DTE <= this value.")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument(
        "--extract-zips",
        action="store_true",
        help="Non-destructively extract data/DG_YYYYMonth.zip if the month folder is missing.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Process and report the merge size, but do not write the parquet.",
    )
    return p.parse_args()


def _safe_extract_zip(zip_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            if member.is_dir():
                continue
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ValueError(f"Refusing unsafe zip member: {member.filename}")
            # Some vendor zips contain a top-level folder, some do not. Flatten only
            # when the member already lives under the expected month folder.
            if member_path.parts and member_path.parts[0] == target_dir.name:
                rel = Path(*member_path.parts[1:])
            else:
                rel = member_path
            dest = target_dir / rel
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)


def _month_dirs(year: int, start: pd.Timestamp, end: pd.Timestamp | None, extract_zips: bool) -> list[Path]:
    if end is None:
        end = pd.Timestamp(f"{year}-12-31")
    months = pd.date_range(start.replace(day=1), end.replace(day=1), freq="MS")
    dirs = []
    for month_start in months:
        folder = Path("data") / f"DG_{year}{calendar.month_name[month_start.month]}"
        zip_path = Path("data") / f"{folder.name}.zip"
        if extract_zips and not folder.is_dir() and zip_path.exists():
            print(f"Extracting {zip_path} -> {folder} (skip existing files)")
            _safe_extract_zip(zip_path, folder)
        if folder.is_dir():
            dirs.append(folder)
    return dirs


def _csv_data_date(path: Path) -> pd.Timestamp | None:
    match = FILE_DATE_RE.search(path.name)
    if not match:
        return None
    return pd.to_datetime(match.group(1), format="%Y%m%d")


def _filter_csvs_by_filename_date(csvs: list[Path], start: pd.Timestamp, end: pd.Timestamp | None) -> list[Path]:
    kept = []
    skipped = 0
    for path in csvs:
        dt = _csv_data_date(path)
        if dt is not None and dt < start:
            skipped += 1
            continue
        if dt is not None and end is not None and dt > end:
            skipped += 1
            continue
        kept.append(path)
    if skipped:
        print(f"Skipped {skipped} CSV files outside append window by filename date.")
    return kept


def _read_one_csv(args: tuple[str, set[str], pd.Timestamp, pd.Timestamp | None, int]):
    path, tickers, start, end, dte_max = args
    try:
        df = pd.read_csv(path, usecols=COLS, low_memory=False)
    except Exception as exc:
        return path, None, str(exc)

    df = df[df["Symbol"].isin(tickers)].copy()
    if df.empty:
        return path, df, None

    for col in NUMERIC_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["DataDate"] = pd.to_datetime(df["DataDate"], errors="coerce")
    df["ExpirationDate"] = pd.to_datetime(df["ExpirationDate"], errors="coerce")
    df["PutCall"] = df["PutCall"].astype(str).str.lower().str.strip()
    df = df.dropna(subset=["DataDate", "ExpirationDate", "BidPrice", "AskPrice", "Delta"])
    if df.empty:
        return path, df, None

    df = df[df["DataDate"] >= start]
    if end is not None:
        df = df[df["DataDate"] <= end]
    if df.empty:
        return path, df, None

    df["DTE"] = (df["ExpirationDate"] - df["DataDate"]).dt.days
    df = df[(df["DTE"] >= 0) & (df["DTE"] <= dte_max)]
    if df.empty:
        return path, df, None

    df["MidPrice"] = (df["BidPrice"] + df["AskPrice"]) / 2.0
    df["AbsDelta"] = df["Delta"].abs()
    return path, df, None


def _collect_new_rows(csvs: list[Path], tickers: set[str], start: pd.Timestamp, end: pd.Timestamp | None, dte_max: int, workers: int) -> pd.DataFrame:
    pieces = []
    t0 = time.time()
    tasks = [(str(path), tickers, start, end, dte_max) for path in csvs]
    if workers <= 1:
        for i, task in enumerate(tasks, start=1):
            path, df, err = _read_one_csv(task)
            if err:
                print(f"  skip {path}: {err}")
                continue
            if df is not None and not df.empty:
                pieces.append(df)
            if i % 25 == 0 or i == len(tasks):
                rows = sum(len(p) for p in pieces)
                elapsed = max(time.time() - t0, 0.001)
                print(f"  {i}/{len(tasks)} files, {rows:,} rows, {i / elapsed:.1f} files/s")
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_read_one_csv, task) for task in tasks]
            for i, fut in enumerate(as_completed(futures), start=1):
                path, df, err = fut.result()
                if err:
                    print(f"  skip {path}: {err}")
                    continue
                if df is not None and not df.empty:
                    pieces.append(df)
                if i % 25 == 0 or i == len(futures):
                    rows = sum(len(p) for p in pieces)
                    elapsed = max(time.time() - t0, 0.001)
                    print(f"  {i}/{len(futures)} files, {rows:,} rows, {i / elapsed:.1f} files/s")
    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces, ignore_index=True)


def main() -> int:
    args = _parse_args()
    if not OUT_PATH.exists():
        print(f"ERROR: missing {OUT_PATH}", file=sys.stderr)
        return 2

    existing = pd.read_parquet(OUT_PATH)
    existing["DataDate"] = pd.to_datetime(existing["DataDate"])
    existing["ExpirationDate"] = pd.to_datetime(existing["ExpirationDate"])
    existing["PutCall"] = existing["PutCall"].astype(str).str.lower().str.strip()

    current_max = existing["DataDate"].max()
    start = pd.Timestamp(args.start) if args.start else current_max + pd.Timedelta(days=1)
    end = pd.Timestamp(args.end) if args.end else None

    dirs = _month_dirs(args.year, start, end, args.extract_zips)
    csvs = []
    for folder in dirs:
        csvs.extend(sorted(folder.glob("Greek_*.csv")))
        csvs.extend(sorted(folder.glob("Greek_*_OData*.csv")))
    csvs = sorted(set(csvs))
    csvs = _filter_csvs_by_filename_date(csvs, start, end)

    tickers = set(SP500) | set(DAILY_EXPIRY) | set(config.SP100_TICKERS)

    print(f"Existing: {len(existing):,} rows, {existing['DataDate'].min().date()} -> {current_max.date()}")
    print(f"Append window: {start.date()} -> {end.date() if end is not None else 'latest available'}")
    print(f"Month folders: {', '.join(str(d) for d in dirs) if dirs else '(none)'}")
    print(f"CSV files: {len(csvs)}")
    print(f"Ticker set: {len(tickers)} symbols; DTE window: 0..{args.dte_max}")

    if not csvs:
        print("No vendor CSV files found. Dump/extract data under data/DG_2026July or data/DG_2026August.")
        return 1

    new = _collect_new_rows(csvs, tickers, start, end, args.dte_max, args.workers)
    if new.empty:
        print("No new rows after filters; parquet left unchanged.")
        return 0

    # Match existing column order where possible and keep any extra new columns at end.
    for col in existing.columns:
        if col not in new.columns:
            new[col] = float("nan")
    new = new[list(existing.columns) + [c for c in new.columns if c not in existing.columns]]

    before = len(existing)
    merged = pd.concat([existing, new], ignore_index=True)
    before_dedup = len(merged)
    merged = (
        merged.drop_duplicates(subset=DEDUP_COLS, keep="last")
        .sort_values(["DataDate", "Symbol", "ExpirationDate", "StrikePrice", "PutCall"])
        .reset_index(drop=True)
    )
    dups = before_dedup - len(merged)

    print(f"New rows: {len(new):,}, dates {new['DataDate'].min().date()} -> {new['DataDate'].max().date()}")
    print(f"Merge: {before:,} existing + {len(new):,} new - {dups:,} duplicate keys = {len(merged):,} rows")
    print(f"Merged range: {merged['DataDate'].min().date()} -> {merged['DataDate'].max().date()}")

    if args.dry_run:
        print("Dry run only; parquet left unchanged.")
        return 0

    stamp = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
    backup = OUT_PATH.with_name(f"{OUT_PATH.name}.bak_before_append_{stamp}")
    tmp = OUT_PATH.with_name(f"{OUT_PATH.name}.tmp_{stamp}")
    shutil.copy2(OUT_PATH, backup)
    merged.to_parquet(tmp, index=False, compression="snappy")
    tmp.replace(OUT_PATH)
    print(f"Backup: {backup}")
    print(f"Wrote:  {OUT_PATH} ({OUT_PATH.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
