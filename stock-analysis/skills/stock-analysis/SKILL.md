---
name: stock-analysis
description: Analyze any publicly traded stock — price, fundamentals (P/E, EPS, market cap, analyst targets), and optional technical indicators (RSI, MACD, SMA-50) — then produce a data-driven Buy/Hold/Sell signal with reasoning. Uses Yahoo Finance (no key needed) + optional free Alpha Vantage key for technicals.
version: 1.0.0
author: hermes-agent
license: MIT
prerequisites:
  commands: [python3]
required_environment_variables:
  - name: ALPHA_VANTAGE_API_KEY
    prompt: Alpha Vantage API key
    help: Free key at https://www.alphavantage.co/support/#api-key — unlocks RSI-14, MACD, and SMA-50 technical indicators (25 calls/day free)
    required_for: technical indicators (RSI, MACD, SMA-50)
metadata:
  hermes:
    tags: [Finance, Stocks, Investment, Trading, Technical Analysis, Fundamentals, Buy/Sell Signal]
---

# Stock Analysis

Comprehensive stock research with a data-driven **Buy / Hold / Sell** signal. Works immediately with no API key — add a free Alpha Vantage key to unlock technical indicators.

## When to Use

- "Analyze AAPL", "What's the outlook for Tesla?", "Should I buy Microsoft?"
- "Is Nvidia overvalued?", "Is TSLA overbought right now?"
- Quick price check: "What is Amazon trading at?"
- Comparing two stocks: "AAPL vs MSFT — which looks stronger?"
- Pre-earnings or macro research on any public company

## Quick Reference

| Task | Command |
|------|---------|
| Full analysis (no key) | `python scripts/analyze_stock.py AAPL` |
| Full analysis + technicals | `python scripts/analyze_stock.py AAPL --av-key $ALPHA_VANTAGE_API_KEY` |
| Compare two stocks | Run for each ticker, compare signal scores |
| JSON output for parsing | `python scripts/analyze_stock.py AAPL --json` |

## Procedure

### Step 1 — Run the analysis

Always start with the helper script. It runs four phases automatically:

**Without Alpha Vantage key (price + fundamentals only):**
```bash
python scripts/analyze_stock.py {TICKER}
```

**With Alpha Vantage key (full analysis including technicals):**
```bash
python scripts/analyze_stock.py {TICKER} --av-key $ALPHA_VANTAGE_API_KEY
```

The four phases:
1. **Quote** — current price, day change %, volume vs average, 52-week range
2. **Fundamentals** — P/E ratio, EPS, market cap, analyst consensus target and rating
3. **Technicals** — RSI-14, MACD crossover signal, price vs SMA-50 *(requires Alpha Vantage key)*
4. **Signal** — each metric casts a vote → **BUY / HOLD / SELL** with score and reasoning

### Step 2 — Interpret the signal

After the script runs, present the output and add brief commentary:

- **BUY (score ≥ +2):** Which metrics are driving it? A score of +4 or +5 is a strong signal; +2 is marginal — note the risks.
- **SELL (score ≤ -2):** Is this overvaluation, technical breakdown, or both? Note if the analyst target disagrees.
- **HOLD (-1 to +1):** Explain the tension — e.g. strong momentum but rich valuation, or cheap stock with deteriorating technicals.

Always close with: *"This is quantitative analysis based on current data, not financial advice."*

### Step 3 — Offer follow-up

After presenting the signal, offer:
- **Compare a peer**: run the same analysis on a direct competitor (e.g. for AAPL → MSFT or GOOGL)
- **Sector context**: note where P/E sits relative to the industry (tech typically 20–40; value stocks typically 10–20)
- **52-week context**: the bar chart in the output shows exactly where in its range the stock sits

## Scoring Logic

Each metric votes **-1 / 0 / +1**:

| Metric | Bullish (+1) | Neutral (0) | Bearish (-1) |
|--------|-------------|-------------|--------------|
| P/E ratio | < 15 | 15–35 | > 35 |
| Analyst target | > 20% upside | within ±20% | > 10% downside |
| RSI-14 *(AV key)* | < 30 oversold | 30–70 | > 70 overbought |
| MACD *(AV key)* | Bullish crossover | No crossover | Bearish crossover |
| Price vs SMA-50 *(AV key)* | Above | — | Below |

**Signal thresholds:** Sum ≥ +2 → **BUY** | -1 to +1 → **HOLD** | ≤ -2 → **SELL**

Without an Alpha Vantage key only 2 metrics vote (P/E and analyst target), so maximum score is ±2.

## Pitfalls

- **Yahoo Finance rate limits**: If you get empty results or a 429 error, wait 30 seconds and retry. The script sends a browser User-Agent to reduce this.
- **Ticker not found**: Use the exchange-qualified format if needed — `BRK-B` not `BRK.B`, `0700.HK` for Hong Kong stocks.
- **P/E not available**: Negative earnings stocks (early-stage, biotech) have no P/E — the metric is skipped rather than penalized.
- **Alpha Vantage free tier**: 25 API calls/day. Each full analysis uses 3 calls (RSI + MACD + SMA). Don't run more than 8 full analyses per day on the free key.
- **Pre-market / after-hours**: Yahoo Finance returns the last regular session price. If the user asks during extended hours, note this caveat.
- **Non-US stocks**: Yahoo Finance supports most global exchanges. Use the local ticker format (e.g. `ASML.AS` for Euronext Amsterdam).

## Verification

The analysis completed successfully if:
- A price displays with a today % change
- The 52-week range bar chart brackets the current price
- SIGNAL VOTES section shows at least 2 metrics
- Output ends with **BUY / HOLD / SELL** and a one-line reasoning summary

## Rate Limits

| Source | Limit | Notes |
|--------|-------|-------|
| Yahoo Finance | ~60 req/min | No key needed |
| Alpha Vantage free | 25 calls/day | 3 calls per full analysis = ~8 analyses/day |
| Alpha Vantage paid | 75–1200 req/min | Premium plans at alphavantage.co |
