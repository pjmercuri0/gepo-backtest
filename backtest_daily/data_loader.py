"""
data_loader.py
Reads preprocessed parquet files instead of raw CSVs.
Run preprocess.py first to generate them.
"""

import os
import pandas as pd
import config

OPTIONS_PARQUET = os.path.join(config.OUTPUT_DIR, "options_filtered_daily.parquet")
EXPIRY_PARQUET  = os.path.join(config.OUTPUT_DIR, "expiry_prices_daily.parquet")


def load_options_data() -> pd.DataFrame:
    if not os.path.exists(OPTIONS_PARQUET):
        raise FileNotFoundError(
            f"Preprocessed data not found at {OPTIONS_PARQUET}.\n"
            "Run: python3 preprocess.py"
        )
    print(f"Loading from {OPTIONS_PARQUET}...")
    df = pd.read_parquet(OPTIONS_PARQUET)
    print(f"Rows: {len(df):,}  |  Tickers: {df['Symbol'].nunique()}  |  "
          f"Dates: {df['DataDate'].min().date()} to {df['DataDate'].max().date()}")
    return df


def load_all_data_raw() -> pd.DataFrame:
    if not os.path.exists(EXPIRY_PARQUET):
        raise FileNotFoundError(
            f"Expiry data not found at {EXPIRY_PARQUET}.\n"
            "Run: python3 preprocess.py"
        )
    print(f"Loading expiry prices from {EXPIRY_PARQUET}...")
    df = pd.read_parquet(EXPIRY_PARQUET)
    return df


def get_expiry_prices(df_full: pd.DataFrame) -> pd.DataFrame:
    # Already filtered to expiry rows in preprocess.py
    return df_full[["Symbol", "ExpirationDate", "ExpiryPrice"]].drop_duplicates(
        subset=["Symbol", "ExpirationDate"]
    )


def get_weekly_entry_dates(df: pd.DataFrame) -> list:
    return sorted(df["DataDate"].unique())
