"""
data_loader.py
Reads all CSV files from DATA_DIR, filters to relevant tickers,
date range, and weekly expiry contracts. Returns a clean DataFrame
ready for spread construction.
"""

import os
import glob
import pandas as pd
from tqdm import tqdm
import config


def load_options_data() -> pd.DataFrame:
    """
    Load all option CSV files from DATA_DIR.
    Returns a cleaned DataFrame with one row per option contract.
    """
    csv_files = sorted(glob.glob(os.path.join(config.DATA_DIR, "*.csv")))
    if not csv_files:
        raise FileNotFoundError(
            f"No CSV files found in {config.DATA_DIR}.\n"
            "Please place the Discount Option Data CSV files there."
        )

    print(f"Found {len(csv_files)} CSV files. Loading...")

    chunks = []
    for f in tqdm(csv_files, desc="Loading files"):
        try:
            df = pd.read_csv(f, low_memory=False)
            chunks.append(df)
        except Exception as e:
            print(f"  Warning: could not read {f}: {e}")

    if not chunks:
        raise ValueError("No data loaded. Check your CSV files.")

    df = pd.concat(chunks, ignore_index=True)
    print(f"Raw rows loaded: {len(df):,}")

    df = _clean(df)
    df = _filter(df)

    print(f"Rows after filtering: {len(df):,}")
    print(f"Tickers: {df['Symbol'].nunique()}")
    print(f"Date range: {df['DataDate'].min()} to {df['DataDate'].max()}")
    return df


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Standardise column types and derived columns."""
    # Normalise column names
    df.columns = df.columns.str.strip()

    # Numeric coercion
    for col in ["AskPrice", "BidPrice", "StrikePrice", "Delta",
                "ImpliedVolatility", "UnderlyingPrice", "OpenInterest",
                "Gamma", "Vega", "Theta"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Dates
    df["DataDate"]       = pd.to_datetime(df["DataDate"],       errors="coerce")
    df["ExpirationDate"] = pd.to_datetime(df["ExpirationDate"], errors="coerce")

    # Mid price — what we use for premium calculations
    df["MidPrice"] = (df["AskPrice"] + df["BidPrice"]) / 2

    # Absolute delta (puts have negative delta)
    df["AbsDelta"] = df["Delta"].abs()

    # Days to expiry
    df["DTE"] = (df["ExpirationDate"] - df["DataDate"]).dt.days

    # Normalise PutCall
    df["PutCall"] = df["PutCall"].str.lower().str.strip()

    return df


def _filter(df: pd.DataFrame) -> pd.DataFrame:
    """Apply all filters to narrow to tradeable weekly contracts."""

    # 1. Tickers
    df = df[df["Symbol"].isin(config.SP100_TICKERS)]

    # 2. Date range
    df = df[
        (df["DataDate"] >= config.START_DATE) &
        (df["DataDate"] <= config.END_DATE)
    ]

    # 3. Entry day of week (Monday by default)
    df = df[df["DataDate"].dt.dayofweek == config.ENTRY_DOW]

    # 4. Weekly expiry window
    df = df[
        (df["DTE"] >= config.DTE_MIN) &
        (df["DTE"] <= config.DTE_MAX)
    ]

    # 5. Valid delta
    df = df[
        df["AbsDelta"].between(config.DELTA_MIN, config.DELTA_MAX)
    ]

    # 6. Valid mid price
    df = df[df["MidPrice"] > 0]

    # 7. Open interest filter (liquidity)
    if "OpenInterest" in df.columns:
        df = df[df["OpenInterest"] >= config.MIN_OPEN_INTEREST]

    # 8. Valid expiry
    df = df[df["ExpirationDate"].notna()]

    df = df.reset_index(drop=True)
    return df


def get_weekly_entry_dates(df: pd.DataFrame) -> list:
    """Return sorted list of unique entry dates in the dataset."""
    return sorted(df["DataDate"].unique())


def get_expiry_prices(df_full: pd.DataFrame) -> pd.DataFrame:
    """
    Build a lookup table of (Symbol, ExpirationDate) -> closing price.
    Uses the UnderlyingPrice on the DataDate closest to ExpirationDate.
    """
    # Find rows where DataDate == ExpirationDate (expiry day data)
    expiry_df = df_full[
        df_full["DataDate"] == df_full["ExpirationDate"]
    ][["Symbol", "ExpirationDate", "UnderlyingPrice"]].drop_duplicates()

    expiry_df = expiry_df.rename(columns={"UnderlyingPrice": "ExpiryPrice"})
    return expiry_df
