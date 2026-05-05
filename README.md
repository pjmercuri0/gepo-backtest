# GEPO Credit Spread Backtest

Backtest of a weekly credit spread strategy (bull put + bear call) on S&P100 tickers,
using GROUND (Growth Rate Over UNiform Divergence) scoring from Mercurio, Wu & Xie (2020).

## Strategy

For each ticker each week, GROUND scores two candidates:
- **Bull put spread** (bullish): sell ~50-delta put, buy one strike below
- **Bear call spread** (bearish): sell ~50-delta call, buy one strike above

The higher-GROUND side is selected. If neither clears the minimum threshold, no trade is taken.

## Setup

```bash
pip install -r requirements.txt
```

## Data

Purchase from: https://discountoptiondata.com
Product: **Bundled 2020–2025 with Greeks** ($149)
Delivery: Google Drive as zipped CSV files

Place all CSV files in the `data/` folder.

```
gepo-backtest/
└── data/
    ├── Greek_20210104_OData1.csv
    ├── Greek_20210104_OData2.csv
    ├── Greek_20210105_OData1.csv
    └── ...
```

## Run

```bash
python run.py
```

Results saved to `output/results.csv` and `output/equity_curve.png`.

## Configuration

Edit `config.py` to change:
- Tickers (default: S&P100 subset)
- Date range
- GROUND threshold
- Starting bankroll
- Delta target

## References

Mercurio, P.J., Wu, Y., & Xie, H. (2020). Option Portfolio Selection with Generalized
Entropic Portfolio Optimization. *Entropy*, 22(8), 805.
