"""
Stage 1 proof-of-concept: pull AAPL's option chain for the next Friday
expiry, print bid/ask/IV/Greeks for each strike near ATM. Validates that
delayed market data + Greeks come through for paper trading.
"""
from datetime import datetime, timedelta
import warnings

import pandas as pd
from ib_insync import IB, Stock, Option

warnings.filterwarnings("ignore")


def next_friday(today):
    days = (4 - today.weekday()) % 7   # Friday is weekday 4
    if days == 0:
        days = 7
    return today + timedelta(days=days)


def main():
    ib = IB()
    import os
    port = int(os.environ.get("IB_PORT", 4001))
    ib.connect("127.0.0.1", port, clientId=1)
    ib.reqMarketDataType(3)            # 3 = delayed (free, no subscription)

    sym = "AAPL"
    today = datetime.now().date()
    target_friday = next_friday(today)
    expiry_str = target_friday.strftime("%Y%m%d")
    print(f"Target: {sym} {target_friday} (expiry_str={expiry_str})")

    # 1. Qualify the underlying stock
    stock = Stock(sym, "SMART", "USD")
    ib.qualifyContracts(stock)

    # 2. Get the spot price
    [stock_ticker] = ib.reqTickers(stock)
    spot = stock_ticker.marketPrice()
    if pd.isna(spot) or spot <= 0:
        spot = stock_ticker.close
    print(f"  spot: ${spot:.2f}")

    # 3. Get available option params (expirations, strikes)
    chains = ib.reqSecDefOptParams(stock.symbol, "", stock.secType, stock.conId)
    smart_chain = next((c for c in chains if c.exchange == "SMART"), None)
    if smart_chain is None:
        print("  no SMART chain found")
        return
    if expiry_str not in smart_chain.expirations:
        print(f"  expiry {expiry_str} not available; available are:")
        print(f"    {sorted(smart_chain.expirations)[:10]}...")
        return

    # 4. Pick strikes within ±5% of spot
    all_strikes = sorted(smart_chain.strikes)
    near_strikes = [s for s in all_strikes if 0.95 * spot <= s <= 1.05 * spot]
    print(f"  using {len(near_strikes)} strikes near ATM")

    # 5. Build option contracts (puts and calls)
    contracts = []
    for strike in near_strikes:
        for right in ["P", "C"]:
            contracts.append(Option(sym, expiry_str, strike, right, "SMART"))
    contracts = ib.qualifyContracts(*contracts)
    print(f"  qualified {len(contracts)} option contracts")

    # 6. Fetch market data (this may take 5-15 seconds for delayed data)
    tickers = ib.reqTickers(*contracts)

    # 7. Print results
    rows = []
    for c, t in zip(contracts, tickers):
        greeks = t.modelGreeks
        rows.append({
            "strike": c.strike,
            "right":  c.right,
            "bid":    t.bid if t.bid and t.bid > 0 else None,
            "ask":    t.ask if t.ask and t.ask > 0 else None,
            "IV":     greeks.impliedVol if greeks else None,
            "delta":  greeks.delta      if greeks else None,
            "gamma":  greeks.gamma      if greeks else None,
            "theta":  greeks.theta      if greeks else None,
            "vega":   greeks.vega       if greeks else None,
        })
    if not rows:
        print("\n  (no option rows collected — likely the data error above)")
    else:
        df = pd.DataFrame(rows).sort_values(["right", "strike"])
        print()
        print(df.to_string(
            index=False,
            float_format=lambda x: f"{x:.4f}" if pd.notna(x) else "—"))

    ib.disconnect()


if __name__ == "__main__":
    main()
