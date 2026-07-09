"""
preprocess.py — run once per date range change.
Scans only relevant month folders, keeps 3 strikes per group.
"""

import os
import re
import glob
import pandas as pd
from tqdm import tqdm
import config

REQUIRED_COLS = [
    "Symbol", "ExpirationDate", "AskPrice", "BidPrice", "LastPrice",
    "StrikePrice", "PutCall", "Delta", "ImpliedVolatility",
    "UnderlyingPrice", "OpenInterest", "Gamma", "Vega", "Theta",
    "DataDate"
]
EXPIRY_COLS   = ["Symbol", "ExpirationDate", "UnderlyingPrice", "DataDate"]
TICKERS       = set(config.SP100_TICKERS)


def _month_folders(data_dir: str, start: pd.Timestamp, end: pd.Timestamp) -> list:
    """Return CSV files only from month folders within [start, end]."""
    months = pd.date_range(start.replace(day=1), end, freq="MS")
    files  = []
    for d in months:
        folder = os.path.join(data_dir, d.strftime("DG_%Y%B"))
        if os.path.isdir(folder):
            files += sorted(glob.glob(os.path.join(folder, "Greek_*_OData*.csv")))
    return files


def _read_csv(f, usecols=None):
    kw = dict(usecols=usecols, dtype=str) if usecols else dict(dtype=str)
    try:
        return pd.read_csv(f, **kw)
    except Exception:
        try:
            return pd.read_csv(f, engine="python", on_bad_lines="skip", **kw)
        except Exception as e:
            print(f"  Skipping {os.path.basename(f)}: {e}")
            return pd.DataFrame()


def _select_strikes(grp):
    """Keep short strike + one below + one above per group."""
    grp = grp.sort_values("StrikePrice").reset_index(drop=True)
    eligible = grp[
        grp["AbsDelta"].between(config.DELTA_MIN, config.DELTA_MAX) &
        (grp["OpenInterest"] >= config.MIN_OPEN_INTEREST) &
        (grp["MidPrice"] > 0)
    ].copy()
    if eligible.empty:
        return pd.DataFrame()
    eligible["dist"] = (eligible["AbsDelta"] - config.DELTA_TARGET).abs()
    short_strike = eligible.loc[eligible["dist"].idxmin(), "StrikePrice"]
    pos = grp[grp["StrikePrice"] == short_strike].index[0]
    keep = {pos}
    if pos > 0:               keep.add(pos - 1)
    if pos < len(grp) - 1:   keep.add(pos + 1)
    return grp.loc[sorted(keep)]


def _process_file(f, entry_dow=None, dte_min=None, dte_max=None):
    """Filter one raw DG CSV down to qualifying rows.

    entry_dow: int day-of-week to keep (0=Mon … 4=Fri), or None for all weekdays.
               Defaults to config.ENTRY_DOW (Monday) to preserve original behavior.
    dte_min/dte_max: DTE window overrides; default to config.DTE_MIN/DTE_MAX.
    """
    if entry_dow is None:
        entry_dow = config.ENTRY_DOW
    if dte_min is None:
        dte_min = config.DTE_MIN
    if dte_max is None:
        dte_max = config.DTE_MAX

    df = _read_csv(f, REQUIRED_COLS)
    if df.empty:
        return df

    for col in ["AskPrice","BidPrice","StrikePrice","Delta","ImpliedVolatility",
                "UnderlyingPrice","OpenInterest","Gamma","Vega","Theta"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["DataDate"]        = pd.to_datetime(df["DataDate"],        errors="coerce")
    df["ExpirationDate"]  = pd.to_datetime(df["ExpirationDate"],  errors="coerce")
    df["PutCall"]         = df["PutCall"].str.lower().str.strip()
    df["MidPrice"]        = (df["AskPrice"] + df["BidPrice"]) / 2
    df["AbsDelta"]        = df["Delta"].abs()
    df["DTE"]             = (df["ExpirationDate"] - df["DataDate"]).dt.days

    df = df[df["Symbol"].isin(TICKERS)]
    if df.empty: return df
    if entry_dow == "all":
        # Keep every trading weekday (0=Mon … 4=Fri), drop weekends
        df = df[df["DataDate"].dt.dayofweek.between(0, 4)]
    else:
        df = df[df["DataDate"].dt.dayofweek == entry_dow]
    if df.empty: return df
    df = df[(df["DTE"] >= dte_min) & (df["DTE"] <= dte_max)]
    if df.empty: return df
    df = df[df["UnderlyingPrice"] > 0]
    if df.empty: return df
    df = df[df["ExpirationDate"].notna()]
    if df.empty: return df

    result = (
        df.groupby(["Symbol","DataDate","ExpirationDate","PutCall"], group_keys=False)
        .apply(_select_strikes)
    )
    if result.empty:
        return pd.DataFrame()
    return result.drop_duplicates(
        subset=["Symbol","ExpirationDate","StrikePrice","PutCall","DataDate"]
    )


def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Preprocess raw CSVs to parquet")
    p.add_argument("-s", "--start", default=config.START_DATE,
                   help="Start date YYYY-MM-DD")
    p.add_argument("-e", "--end",   default=config.END_DATE,
                   help="End date YYYY-MM-DD")
    p.add_argument("--all-weekdays", action="store_true",
                   help="Keep Mon-Fri entries (not just config.ENTRY_DOW). "
                        "Writes to options_filtered_daily.parquet so the "
                        "canonical Monday parquet is left intact.")
    p.add_argument("--dte-min", type=int, default=None,
                   help="Override config.DTE_MIN (useful with --all-weekdays "
                        "to widen the window so e.g. Thu/Fri entries can "
                        "target same-week Friday expiry).")
    p.add_argument("--dte-max", type=int, default=None,
                   help="Override config.DTE_MAX.")
    p.add_argument("-o", "--out-name", default=None,
                   help="Base name for output parquets (no extension). "
                        "Defaults to 'options_filtered' (Monday) or "
                        "'options_filtered_daily' (--all-weekdays).")
    p.add_argument("--append", action="store_true",
                   help="Merge new processed rows into the existing parquet "
                        "instead of overwriting. Dedupes by "
                        "(Symbol, DataDate, ExpirationDate, StrikePrice, PutCall) "
                        "for options and (Symbol, ExpirationDate) for expiry. "
                        "Use when adding a new year to an existing parquet "
                        "after the source raw data for prior years has been "
                        "deleted to save disk space.")
    return p.parse_args()


def main():
    args = parse_args()
    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    start = pd.Timestamp(args.start)
    end   = pd.Timestamp(args.end)
    entry_dow = "all" if args.all_weekdays else config.ENTRY_DOW
    dte_min = args.dte_min if args.dte_min is not None else config.DTE_MIN
    dte_max = args.dte_max if args.dte_max is not None else config.DTE_MAX
    base_name = args.out_name or ("options_filtered_daily" if args.all_weekdays else "options_filtered")

    print(f"Date range: {start.date()} to {end.date()}")
    print(f"Entry filter: {'all weekdays Mon-Fri' if args.all_weekdays else f'dayofweek={config.ENTRY_DOW} (Monday)'}")
    print(f"DTE window: [{dte_min}, {dte_max}]")
    print(f"Output base: {base_name}")

    # Options files — only relevant months
    opt_files = _month_folders(config.DATA_DIR, start, end)
    print(f"Options files to scan: {len(opt_files)}")

    chunks = []
    for f in tqdm(opt_files, desc="Options"):
        chunk = _process_file(f, entry_dow=entry_dow, dte_min=dte_min, dte_max=dte_max)
        if not chunk.empty:
            chunks.append(chunk)

    if not chunks:
        raise ValueError("No data after filtering.")

    df = pd.concat(chunks, ignore_index=True)
    print(f"\nNew rows: {len(df):,}  |  Tickers: {df['Symbol'].nunique()}  |  DataDates: {df['DataDate'].nunique()}")
    print(f"PutCall:\n{df['PutCall'].value_counts().to_string()}")

    out = os.path.join(config.OUTPUT_DIR, f"{base_name}.parquet")

    # Append mode: merge new rows into existing parquet, dedupe, write back.
    # Useful when the source raw data for prior years has been deleted to
    # save disk space and only the parquet remains.
    if args.append and os.path.exists(out):
        existing = pd.read_parquet(out)
        before = len(existing)
        new_dates  = set(df["DataDate"].unique())
        prev_dates = set(existing["DataDate"].unique())
        overlap    = new_dates & prev_dates
        if overlap:
            print(f"  [append] WARNING: {len(overlap)} DataDates exist in both; "
                  f"new rows take precedence on dedup")
        merged = pd.concat([existing, df], ignore_index=True)
        # Keep="last" so the newly-processed rows win on dedup collisions.
        merged = merged.drop_duplicates(
            subset=["Symbol","ExpirationDate","StrikePrice","PutCall","DataDate"],
            keep="last",
        ).sort_values(["DataDate","Symbol","ExpirationDate","StrikePrice","PutCall"]).reset_index(drop=True)
        print(f"  [append] existing {before:,} + new {len(df):,} - dups = {len(merged):,} merged rows "
              f"({merged['DataDate'].min().date()} → {merged['DataDate'].max().date()})")
        df = merged

    if args.all_weekdays:
        dow_names = {0:"Mon",1:"Tue",2:"Wed",3:"Thu",4:"Fri"}
        dow_counts = df["DataDate"].dt.dayofweek.value_counts().sort_index()
        print(f"Day-of-week ({'merged' if args.append else 'new'}):\n" + "\n".join(f"  {dow_names[d]}: {c}" for d, c in dow_counts.items()))

    df.to_parquet(out, index=False)
    print(f"Saved: {out}  ({os.path.getsize(out)/1e6:.1f} MB)")

    # Expiry prices — include one extra month for weekly expiry resolution
    exp_files = _month_folders(config.DATA_DIR, start, end + pd.Timedelta(days=31))
    print(f"\nExpiry files to scan: {len(exp_files)}")

    exp_chunks = []
    for f in tqdm(exp_files, desc="Expiry"):
        df_e = _read_csv(f, EXPIRY_COLS)
        if df_e.empty: continue
        df_e["DataDate"]        = pd.to_datetime(df_e["DataDate"],        errors="coerce")
        df_e["ExpirationDate"]  = pd.to_datetime(df_e["ExpirationDate"],  errors="coerce")
        df_e["UnderlyingPrice"] = pd.to_numeric(df_e["UnderlyingPrice"],  errors="coerce")
        df_e = df_e[df_e["Symbol"].isin(TICKERS)]
        exp_chunks.append(df_e)

    df_full = pd.concat(exp_chunks, ignore_index=True)

    # Primary: expiry-day spot (DataDate == ExpirationDate)
    df_exp = df_full[df_full["DataDate"] == df_full["ExpirationDate"]].copy()
    df_exp = df_exp.drop_duplicates(subset=["Symbol", "ExpirationDate"])
    df_exp = df_exp.rename(columns={"UnderlyingPrice": "ExpiryPrice"})

    # Fallback: for any (Symbol, ExpirationDate) still missing, use the
    # most recent DataDate <= ExpirationDate from the full panel.
    # Handles vendor file gaps (e.g. 2022-05-27).
    needed  = df[["Symbol", "ExpirationDate"]].drop_duplicates()
    have    = df_exp[["Symbol", "ExpirationDate"]].drop_duplicates()
    missing = needed.merge(have, on=["Symbol", "ExpirationDate"],
                           how="left", indicator=True)
    missing = missing[missing["_merge"] == "left_only"].drop(columns="_merge")

    if not missing.empty:
        print(f"\nMissing expiry-day spots: {len(missing):,} pairs. "
              f"Using prior trading day's UnderlyingPrice...")
        spots = (df_full[["Symbol", "DataDate", "UnderlyingPrice"]]
                 .dropna()
                 .drop_duplicates(subset=["Symbol", "DataDate"])
                 .sort_values(["Symbol", "DataDate"]))
        filled = []
        for sym, exp_date in missing.itertuples(index=False):
            cand = spots[(spots["Symbol"] == sym) &
                         (spots["DataDate"] <= exp_date)]
            if cand.empty: continue
            row = cand.iloc[-1]
            filled.append({
                "Symbol":         sym,
                "ExpirationDate": exp_date,
                "ExpiryPrice":    float(row["UnderlyingPrice"]),
                "DataDate":       row["DataDate"],
            })
        if filled:
            df_exp = pd.concat([df_exp, pd.DataFrame(filled)], ignore_index=True)
            print(f"  Filled {len(filled):,} rows.")

    exp_name = "expiry_prices_daily" if args.all_weekdays else "expiry_prices"
    if args.out_name:
        # Mirror options base_name: 'foo' → 'foo_expiry_prices'
        exp_name = f"{args.out_name}_expiry"
    out2 = os.path.join(config.OUTPUT_DIR, f"{exp_name}.parquet")

    if args.append and os.path.exists(out2):
        existing_exp = pd.read_parquet(out2)
        before_exp = len(existing_exp)
        # keep="first" (existing wins) because if raw data for a prior period
        # has been deleted, the new scan may re-derive that period's expiry
        # prices via fallback (prior-trading-day spot) which is less accurate
        # than the original expiry-day spot already in the parquet.
        merged_exp = pd.concat([existing_exp, df_exp], ignore_index=True)
        merged_exp = merged_exp.drop_duplicates(
            subset=["Symbol","ExpirationDate"], keep="first",
        ).sort_values(["ExpirationDate","Symbol"]).reset_index(drop=True)
        print(f"  [append] expiry: existing {before_exp:,} + new {len(df_exp):,} - dups = {len(merged_exp):,} merged "
              f"({merged_exp['ExpirationDate'].min().date()} → {merged_exp['ExpirationDate'].max().date()})")
        df_exp = merged_exp

    df_exp.to_parquet(out2, index=False)
    print(f"Saved: {out2}  ({os.path.getsize(out2)/1e6:.1f} MB)")

    print("\nDone. Run python3 run.py")


if __name__ == "__main__":
    main()
