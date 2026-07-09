"""
Fast parallel preprocess for a single year. Reads vendor CSVs concurrently,
filters by an externally-provided ticker set, includes LastPrice + standard
columns. Writes a parquet at output/<out_name>.parquet.

Usage:
  python3 fast_preprocess.py 2022 --tickers sp500 -o 2022_sp500_last
  python3 fast_preprocess.py 2022 --tickers all   -o 2022_all_last
  python3 fast_preprocess.py 2022 --tickers sp100 -o 2022_sp100_last
"""
import argparse, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

# Hardcoded SP500 tickers (as of 2024-ish; close enough — the OI/liquidity
# filter in build_candidates downstream will trim any thinly-traded names).
SP500 = """
A AAL AAP AAPL ABBV ABC ABMD ABT ACGL ACN ADBE ADI ADM ADP ADSK AEE AEP AES
AFL AIG AIZ AJG AKAM ALB ALGN ALK ALL ALLE AMAT AMCR AMD AME AMGN AMP AMT
AMZN ANET ANSS ANTM AON AOS APA APD APH APTV ARE ATO ATVI AVB AVGO AVY AWK
AXP AZO BA BAC BAX BBWI BBY BDX BEN BF.B BIIB BIO BK BKNG BKR BLK BLL BMY
BR BRK.B BRO BSX BWA BXP C CAG CAH CARR CAT CB CBOE CBRE CCI CCL CDAY CDNS
CDW CE CERN CF CFG CHD CHRW CHTR CI CINF CL CLX CMA CMCSA CME CMG CMI CMS
CNC CNP COF COG COO COP COST CPB CPRT CPT CRL CRM CSCO CSX CTAS CTLT CTRA
CTSH CTVA CTXS CVS CVX CZR D DAL DD DE DFS DG DGX DHI DHR DIS DISCA DISCK
DISH DLR DLTR DOV DOW DPZ DRE DRI DTE DUK DVA DVN DXC DXCM EA EBAY ECL ED
EFX EIX EL EMN EMR ENPH EOG EPAM EQIX EQR ES ESS ETN ETR ETSY EVRG EW EXC
EXPD EXPE EXR F FANG FAST FB FBHS FCX FDX FE FFIV FIS FISV FITB FLT FMC
FOX FOXA FRC FRT FTNT FTV GD GE GILD GIS GL GLW GM GNRC GOOG GOOGL GPC GPN
GRMN GS GWW HAL HAS HBAN HCA HD HES HIG HII HLT HOLX HON HPE HPQ HRL HSIC
HST HSY HUM HWM IBM ICE IDXX IEX IFF ILMN INCY INTC INTU IP IPG IPGP IQV
IR IRM ISRG IT ITW IVZ J JBHT JCI JKHY JNJ JNPR JPM K KEY KEYS KHC KIM KLAC
KMB KMI KMX KO KR L LDOS LEG LEN LH LHX LIN LKQ LLY LMT LNC LNT LOW LRCX
LUMN LUV LVS LW LYB LYV MA MAA MAR MAS MCD MCHP MCK MCO MDLZ MDT MET META
MGM MHK MKC MKTX MLM MMC MMM MNST MO MOH MOS MPC MPWR MRK MRNA MRO MS MSCI
MSFT MSI MTB MTCH MTD MU NCLH NDAQ NDSN NEE NEM NFLX NI NKE NLOK NLSN NOC
NOV NOW NRG NSC NTAP NTRS NUE NVDA NVR NWL NWS NWSA NXPI O ODFL OGN OKE
OMC ORCL ORLY OTIS OXY PAYC PAYX PCAR PEAK PEG PENN PEP PFE PFG PG PGR PH
PHM PKG PKI PLD PM PNC PNR PNW POOL PPG PPL PRU PSA PSX PTC PVH PWR PXD PYPL
QCOM QRVO RCL RE REG REGN RF RHI RJF RL RMD ROK ROL ROP ROST RSG RTX SBAC
SBNY SBUX SCHW SEDG SEE SHW SIVB SJM SLB SNA SNPS SO SPG SPGI SRE STE STT
STX STZ SWK SWKS SYF SYK SYY T TAP TDG TDY TECH TEL TER TFC TFX TGT TJX TMO
TMUS TPR TRGP TRMB TROW TRV TSCO TSLA TSN TT TTWO TWTR TXN TXT TYL UA UAA
UAL UDR UHS ULTA UNH UNP UPS URI USB V VFC VIAC VLO VMC VNO VRSK VRSN VRTX
VTR VTRS VZ WAB WAT WBA WDC WEC WELL WFC WHR WM WMB WMT WRB WRK WST WU WY
WYNN XEL XLNX XOM XRAY XYL YUM ZBH ZBRA ZION ZTS
""".split()

# ETF + index daily-expiration tickers
DAILY_EXPIRY = "SPY QQQ IWM SPXW RUTW".split()

COLS = ["Symbol", "DataDate", "ExpirationDate", "StrikePrice", "PutCall",
        "AskPrice", "BidPrice", "LastPrice", "OpenInterest", "UnderlyingPrice",
        "ImpliedVolatility", "Delta", "Gamma", "Vega", "Theta"]


def process_one_csv(args):
    """Worker: read one CSV, filter to ticker set, return small df."""
    fp, ticker_set, dte_max = args
    try:
        df = pd.read_csv(fp, usecols=COLS, low_memory=False)
    except Exception as e:
        return fp, None, str(e)
    df = df[df["Symbol"].isin(ticker_set)]
    # Coerce all numeric columns to float (some vendor rows have mixed types)
    for c in ["AskPrice","BidPrice","LastPrice","StrikePrice","OpenInterest",
              "UnderlyingPrice","ImpliedVolatility","Delta","Gamma","Vega","Theta"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["BidPrice", "AskPrice", "Delta"])
    df["DataDate"]       = pd.to_datetime(df["DataDate"],       errors="coerce")
    df["ExpirationDate"] = pd.to_datetime(df["ExpirationDate"], errors="coerce")
    df = df.dropna(subset=["DataDate", "ExpirationDate"])
    df["DTE"] = (df["ExpirationDate"] - df["DataDate"]).dt.days
    df = df[(df["DTE"] >= 0) & (df["DTE"] <= dte_max)]
    return fp, df, None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("year", type=int)
    p.add_argument("--tickers", choices=["sp100", "sp500", "all"], default="sp500")
    p.add_argument("--dte-max", type=int, default=7)
    p.add_argument("-o", "--out-name", required=True)
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()

    # Build ticker set
    if args.tickers == "sp500":
        import config
        tickers = set(SP500) | set(DAILY_EXPIRY) | set(config.SP100_TICKERS)
    elif args.tickers == "sp100":
        import config
        tickers = set(config.SP100_TICKERS)
    else:  # all
        tickers = None  # process everything

    # Collect all CSV files for the year
    months = ["", "January","February","March","April","May","June",
              "July","August","September","October","November","December"]
    csvs = []
    for m in range(1, 13):
        folder = Path("data") / f"DG_{args.year}{months[m]}"
        if not folder.is_dir(): continue
        csvs.extend(sorted(folder.glob("Greek_*.csv")))
    print(f"Year {args.year}: {len(csvs)} CSV files, target ticker set: "
          f"{'all' if tickers is None else f'{len(tickers)} tickers'}", flush=True)

    if tickers is None:
        # If "all", skip the in-worker ticker filter — return everything
        tickers_pass = set()  # sentinel — workers will not filter
    else:
        tickers_pass = tickers

    t0 = time.time()
    pieces = []
    n_done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futures = [ex.submit(process_one_csv, (str(fp), tickers_pass, args.dte_max))
                   for fp in csvs]
        for fut in as_completed(futures):
            fp, df, err = fut.result()
            n_done += 1
            if err:
                print(f"  ✗ {fp}: {err}", flush=True)
                continue
            if df is not None and not df.empty:
                pieces.append(df)
            if n_done % 25 == 0 or n_done == len(csvs):
                elapsed = time.time() - t0
                rate = n_done / elapsed if elapsed > 0 else 0
                eta = (len(csvs) - n_done) / rate if rate > 0 else 0
                print(f"  {n_done}/{len(csvs)} ({100*n_done/len(csvs):.0f}%)  "
                      f"rate {rate:.1f}/s  eta {eta:.0f}s  "
                      f"collected {sum(len(p) for p in pieces):,} rows",
                      flush=True)

    if not pieces:
        print("ERROR: no rows collected", flush=True)
        return 1

    print("\nConcatenating + writing parquet...", flush=True)
    df = pd.concat(pieces, ignore_index=True)
    print(f"  final rows: {len(df):,}", flush=True)
    print(f"  unique tickers: {df['Symbol'].nunique():,}", flush=True)
    print(f"  date range: {df['DataDate'].min().date()} → {df['DataDate'].max().date()}", flush=True)

    out_path = Path("output") / f"{args.out_name}.parquet"
    df.to_parquet(out_path, index=False, compression="snappy")
    print(f"\n✓ Wrote {out_path}  ({out_path.stat().st_size/1024/1024:.1f} MB)", flush=True)
    print(f"Total elapsed: {time.time()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
