"""Wide-coverage preprocess for empirical pool.

Skips the strike-narrowing filter that preprocess.py applies (which keeps
only delta 0.35-0.65 strikes for backtest candidate selection). Keeps:
  - All SP100 symbols
  - DTE >= 0 (includes expiry-day snapshots so build_production_pool can
    derive expiry_close via DataDate==ExpirationDate self-join)
  - All weekdays Mon-Fri
  - All strikes (no delta / OI / midprice filter)

Output: output/<year>_sp500_last.parquet — same schema as the canonical files,
but built without strike narrowing so the empirical lookup gets full coverage
across delta buckets 0-9.

Usage:
  python preprocess_empirical.py --start 2026-01-01 --end 2026-06-30 --year 2026
"""
import argparse, glob, os, sys
import pandas as pd
from tqdm import tqdm
from multiprocessing import Pool
from functools import partial
import config

REQUIRED_COLS = [
    "Symbol", "ExpirationDate", "AskPrice", "BidPrice", "LastPrice",
    "StrikePrice", "PutCall", "Delta", "ImpliedVolatility",
    "UnderlyingPrice", "OpenInterest", "Gamma", "Vega", "Theta",
    "DataDate"
]
TICKERS = set(config.SP100_TICKERS)


def month_folders(data_dir, start, end):
    months = pd.date_range(start.replace(day=1), end, freq="MS")
    files = []
    for d in months:
        folder = os.path.join(data_dir, d.strftime("DG_%Y%B"))
        if os.path.isdir(folder):
            files += sorted(glob.glob(os.path.join(folder, "Greek_*_OData*.csv")))
    return files


def _read_csv(f):
    try:
        return pd.read_csv(f, dtype=str, usecols=REQUIRED_COLS)
    except Exception:
        try:
            return pd.read_csv(f, dtype=str, usecols=REQUIRED_COLS,
                               engine="python", on_bad_lines="skip")
        except Exception as e:
            print(f"  skip {os.path.basename(f)}: {e}")
            return pd.DataFrame()


def process_file(f, dte_min, dte_max):
    df = _read_csv(f)
    if df.empty: return df
    for col in ["AskPrice","BidPrice","LastPrice","StrikePrice","Delta",
                "ImpliedVolatility","UnderlyingPrice","OpenInterest",
                "Gamma","Vega","Theta"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["DataDate"]       = pd.to_datetime(df["DataDate"], errors="coerce")
    df["ExpirationDate"] = pd.to_datetime(df["ExpirationDate"], errors="coerce")
    df["PutCall"]        = df["PutCall"].str.lower().str.strip()
    df["MidPrice"]       = (df["AskPrice"] + df["BidPrice"]) / 2
    df["AbsDelta"]       = df["Delta"].abs()
    df["DTE"]            = (df["ExpirationDate"] - df["DataDate"]).dt.days

    df = df[df["Symbol"].isin(TICKERS)]
    if df.empty: return df
    df = df[df["DataDate"].dt.dayofweek.between(0, 4)]  # Mon-Fri (need Fri for expiry_close)
    df = df[df["DTE"].between(dte_min, dte_max)]
    df = df[df["UnderlyingPrice"] > 0]
    df = df[df["ExpirationDate"].notna()]
    return df


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--start", required=True)
    p.add_argument("--end",   required=True)
    p.add_argument("--year",  type=int, required=True)
    p.add_argument("--dte-min", type=int, default=0)
    p.add_argument("--dte-max", type=int, default=8)
    args = p.parse_args()

    start = pd.Timestamp(args.start)
    end   = pd.Timestamp(args.end)
    print(f"Range: {start.date()} → {end.date()}  DTE [{args.dte_min}, {args.dte_max}]")
    print(f"Output: output/{args.year}_sp500_last.parquet (no strike filter)")

    files = month_folders(config.DATA_DIR, start, end)
    print(f"Scanning {len(files)} CSV files...")

    chunks = []
    n_workers = min(4, len(files))
    print(f"Processing with {n_workers} parallel workers...", flush=True)
    worker_fn = partial(process_file, dte_min=args.dte_min, dte_max=args.dte_max)
    with Pool(n_workers) as pool:
        for c in tqdm(pool.imap_unordered(worker_fn, files, chunksize=4),
                      total=len(files), desc="Options"):
            if not c.empty:
                chunks.append(c)

    if not chunks:
        print("No data after filtering."); sys.exit(1)
    df = pd.concat(chunks, ignore_index=True)
    print(f"\nRows: {len(df):,}  Tickers: {df['Symbol'].nunique()}  Dates: {df['DataDate'].nunique()}")
    print(f"DTE: {df['DTE'].min()} to {df['DTE'].max()}")
    print(f"Delta deciles (abs_delta * 10): "
          f"{dict((df['AbsDelta']*10).astype(int).clip(0,9).value_counts().sort_index())}")

    out = f"output/{args.year}_sp500_last.parquet"
    df.to_parquet(out, index=False)
    print(f"Wrote {out}  ({os.path.getsize(out)/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
