#!/usr/bin/env python3
"""analyze_stock.py — Comprehensive stock analysis with Buy/Hold/Sell signal.

Phases:
  1. Quote & price   — yfinance (auto-installed, wraps Yahoo Finance)
  2. Fundamentals    — yfinance (P/E, EPS, market cap, analyst targets)
  3. Technicals      — Alpha Vantage RSI/MACD/SMA (optional free key)
  4. Signal          — Weighted vote → BUY / HOLD / SELL

Usage:
    python analyze_stock.py AAPL
    python analyze_stock.py AAPL --av-key YOUR_KEY
    python analyze_stock.py MSFT --json
    ALPHA_VANTAGE_API_KEY=xyz python analyze_stock.py TSLA
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
import urllib.error
import urllib.request


# ---------------------------------------------------------------------------
# yfinance — auto-install if missing
# ---------------------------------------------------------------------------

def _ensure_yfinance():
    try:
        import yfinance
        return yfinance
    except ImportError:
        print("Installing yfinance…", file=sys.stderr)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "yfinance", "--quiet"],
            stdout=subprocess.DEVNULL,
        )
        import yfinance
        return yfinance


# ---------------------------------------------------------------------------
# SSL context for Alpha Vantage requests
# ---------------------------------------------------------------------------

def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        pass
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_SSL_CTX = _ssl_context()


# ---------------------------------------------------------------------------
# Phase 1 + 2 — Quote & Fundamentals via yfinance
# ---------------------------------------------------------------------------

def get_quote_and_fundamentals(symbol):
    """Fetch price, volume, and fundamentals using yfinance."""
    yf = _ensure_yfinance()
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
    except Exception as exc:
        print(f"  [warn] yfinance error: {exc}", file=sys.stderr)
        return None

    if not info or not info.get("currentPrice") and not info.get("regularMarketPrice"):
        return None

    def _get(*keys):
        for k in keys:
            v = info.get(k)
            if v is not None:
                return v
        return None

    price = _get("currentPrice", "regularMarketPrice")
    prev_close = _get("previousClose", "regularMarketPreviousClose")
    change_pct = None
    if price and prev_close and prev_close != 0:
        change_pct = (price - prev_close) / prev_close * 100

    return {
        "symbol": info.get("symbol", symbol),
        "name": _get("longName", "shortName") or symbol,
        "price": price,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "day_high": _get("dayHigh", "regularMarketDayHigh"),
        "day_low": _get("dayLow", "regularMarketDayLow"),
        "w52_high": info.get("fiftyTwoWeekHigh"),
        "w52_low": info.get("fiftyTwoWeekLow"),
        "volume": _get("volume", "regularMarketVolume"),
        "avg_volume": _get("averageVolume", "averageDailyVolume3Month"),
        "market_cap": info.get("marketCap"),
        "pe_trailing": info.get("trailingPE"),
        "pe_forward": info.get("forwardPE"),
        "eps": _get("trailingEps", "epsTrailingTwelveMonths"),
        "analyst_target": _get("targetMeanPrice"),
        "analyst_rating": _get("recommendationKey"),
        "revenue_growth": info.get("revenueGrowth"),
        "profit_margins": info.get("profitMargins"),
        "dividend_yield": info.get("dividendYield"),
        "currency": info.get("currency", "USD"),
    }


# ---------------------------------------------------------------------------
# Phase 3 — Technical Indicators (Alpha Vantage, optional free key)
# ---------------------------------------------------------------------------

_AV_BASE = "https://www.alphavantage.co/query"


def _av_fetch(url):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"  [warn] HTTP {exc.code} from Alpha Vantage", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  [warn] {exc}", file=sys.stderr)
        return None


def get_technicals(symbol, av_key):
    """Fetch RSI-14, MACD, SMA-50 from Alpha Vantage."""

    def _av(function, extra):
        return _av_fetch(f"{_AV_BASE}?function={function}&symbol={symbol}&apikey={av_key}&{extra}")

    result = {}

    # RSI-14
    rsi_data = _av("RSI", "interval=daily&time_period=14&series_type=close")
    if rsi_data and "Technical Analysis: RSI" in rsi_data:
        series = rsi_data["Technical Analysis: RSI"]
        latest = sorted(series.keys())[-1]
        result["rsi"] = float(series[latest]["RSI"])

    # MACD
    macd_data = _av("MACD", "interval=daily&series_type=close")
    if macd_data and "Technical Analysis: MACD" in macd_data:
        series = macd_data["Technical Analysis: MACD"]
        dates = sorted(series.keys())
        if len(dates) >= 2:
            cur = series[dates[-1]]
            prv = series[dates[-2]]
            macd_cur, sig_cur = float(cur["MACD"]), float(cur["MACD_Signal"])
            macd_prv, sig_prv = float(prv["MACD"]), float(prv["MACD_Signal"])
            result["macd"] = macd_cur
            result["macd_signal_line"] = sig_cur
            if macd_prv < sig_prv and macd_cur >= sig_cur:
                result["macd_cross"] = "bullish"
            elif macd_prv > sig_prv and macd_cur <= sig_cur:
                result["macd_cross"] = "bearish"
            else:
                result["macd_cross"] = "none"
                result["macd_above"] = macd_cur > sig_cur

    # SMA-50
    sma_data = _av("SMA", "interval=daily&time_period=50&series_type=close")
    if sma_data and "Technical Analysis: SMA" in sma_data:
        series = sma_data["Technical Analysis: SMA"]
        latest = sorted(series.keys())[-1]
        result["sma_50"] = float(series[latest]["SMA"])

    return result or None


# ---------------------------------------------------------------------------
# Phase 4 — Scoring → BUY / HOLD / SELL
# ---------------------------------------------------------------------------

def score(data, technicals):
    """Each metric votes -1/0/+1. Signal: BUY (≥+2), HOLD (-1..+1), SELL (≤-2)."""
    votes = []
    price = data.get("price")

    # P/E ratio
    pe = data.get("pe_trailing") or data.get("pe_forward")
    if pe is not None and pe > 0:
        if pe < 15:
            votes.append((+1, f"P/E {pe:.1f} — undervalued territory"))
        elif pe > 35:
            votes.append((-1, f"P/E {pe:.1f} — elevated valuation"))
        else:
            votes.append((0, f"P/E {pe:.1f} — fair value range (15–35)"))

    # Analyst price target
    target = data.get("analyst_target")
    if target and price:
        upside = (target - price) / price
        if upside > 0.20:
            votes.append((+1, f"Analyst target ${target:.2f} ({upside*100:+.1f}% upside)"))
        elif upside < -0.10:
            votes.append((-1, f"Analyst target ${target:.2f} ({upside*100:+.1f}% downside)"))
        else:
            votes.append((0, f"Analyst target ${target:.2f} ({upside*100:+.1f}%)"))

    if technicals:
        # RSI-14
        rsi = technicals.get("rsi")
        if rsi is not None:
            if rsi < 30:
                votes.append((+1, f"RSI-14 {rsi:.1f} — oversold (< 30)"))
            elif rsi > 70:
                votes.append((-1, f"RSI-14 {rsi:.1f} — overbought (> 70)"))
            else:
                votes.append((0, f"RSI-14 {rsi:.1f} — neutral (30–70)"))

        # MACD crossover
        cross = technicals.get("macd_cross")
        if cross == "bullish":
            votes.append((+1, "MACD bullish crossover — momentum turning up"))
        elif cross == "bearish":
            votes.append((-1, "MACD bearish crossover — momentum turning down"))
        elif cross == "none":
            above = technicals.get("macd_above")
            label = "above" if above else "below"
            votes.append((0, f"MACD {label} signal line (no recent crossover)"))

        # Price vs SMA-50
        sma_50 = technicals.get("sma_50")
        if sma_50 and price:
            if price > sma_50:
                votes.append((+1, f"Price ${price:.2f} above SMA-50 ${sma_50:.2f}"))
            else:
                votes.append((-1, f"Price ${price:.2f} below SMA-50 ${sma_50:.2f}"))

    total = sum(v for v, _ in votes)
    signal = "BUY" if total >= 2 else "SELL" if total <= -2 else "HOLD"
    return signal, total, votes


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_cap(n):
    if n is None:
        return "N/A"
    if n >= 1e12:
        return f"${n/1e12:.2f}T"
    if n >= 1e9:
        return f"${n/1e9:.2f}B"
    if n >= 1e6:
        return f"${n/1e6:.2f}M"
    return f"${n:,.0f}"


def _fmt_vol(n):
    if n is None:
        return "N/A"
    if n >= 1e9:
        return f"{n/1e9:.2f}B"
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.1f}K"
    return str(int(n))


def _fmt_pct(n):
    return f"{n*100:.1f}%" if n is not None else "N/A"


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(data, technicals, signal, total, votes):
    W = 52
    line = "─" * W
    symbol = data.get("symbol", "?")
    name = data.get("name", symbol)

    print(f"\n{symbol} — {name}")
    print(line)

    price = data.get("price")
    chg = data.get("change_pct")
    chg_str = f"  ({chg:+.2f}% today)" if chg is not None else ""
    print(f"PRICE     ${price:.2f}{chg_str}" if price else "PRICE     N/A")

    w52h, w52l = data.get("w52_high"), data.get("w52_low")
    if w52l and w52h and price:
        pct = (price - w52l) / (w52h - w52l) * 100
        filled = int(pct / 100 * 20)
        bar = "█" * filled + "░" * (20 - filled)
        print(f"52W       ${w52l:.2f} [{bar}] ${w52h:.2f}")

    vol, avg_vol = data.get("volume"), data.get("avg_volume")
    if vol:
        avg_str = f"  (avg {_fmt_vol(avg_vol)})" if avg_vol else ""
        print(f"Volume    {_fmt_vol(vol)}{avg_str}")

    print(f"\nFUNDAMENTALS")
    print(f"  Market Cap     {_fmt_cap(data.get('market_cap'))}")

    pe = data.get("pe_trailing") or data.get("pe_forward")
    if pe and pe > 0:
        pe_note = "  ⚠ elevated" if pe > 35 else ("  ✓ low" if pe < 15 else "")
        print(f"  P/E Ratio      {pe:.1f}{pe_note}")
    else:
        print(f"  P/E Ratio      N/A")

    eps = data.get("eps")
    print(f"  EPS (TTM)      ${eps:.2f}" if eps else "  EPS (TTM)      N/A")

    target = data.get("analyst_target")
    if target and price:
        upside = (target - price) / price * 100
        print(f"  Analyst Target ${target:.2f}  ({upside:+.1f}% upside)")

    rating = data.get("analyst_rating")
    if rating:
        print(f"  Analyst Rating {rating.upper()}")

    dy = data.get("dividend_yield")
    if dy:
        print(f"  Dividend Yield {_fmt_pct(dy)}")

    if technicals:
        print(f"\nTECHNICALS  (Alpha Vantage)")
        rsi = technicals.get("rsi")
        if rsi is not None:
            rsi_note = "  overbought ⚠" if rsi > 70 else ("  oversold ✓" if rsi < 30 else "  neutral")
            print(f"  RSI-14    {rsi:.1f}{rsi_note}")
        cross = technicals.get("macd_cross")
        if cross == "bullish":
            print("  MACD      bullish crossover ✓")
        elif cross == "bearish":
            print("  MACD      bearish crossover ⚠")
        elif cross == "none":
            above = technicals.get("macd_above")
            print(f"  MACD      {'above' if above else 'below'} signal line")
        sma_50 = technicals.get("sma_50")
        if sma_50 and price:
            icon = "✓" if price > sma_50 else "⚠"
            direction = "above" if price > sma_50 else "below"
            print(f"  SMA-50    ${sma_50:.2f}  price {direction} {icon}")
    else:
        print(f"\nTECHNICALS  not available — pass --av-key for RSI, MACD, SMA")

    print(f"\nSIGNAL VOTES")
    for vote, reason in votes:
        marker = f"{vote:+d}" if vote != 0 else " 0"
        print(f"  {marker}  {reason}")
    print(f"  {'─'*40}")

    labels = {"BUY": "▲ BUY", "HOLD": "◆ HOLD", "SELL": "▼ SELL"}
    print(f"  {total:+d}  →  {labels[signal]}")

    bull = [r for v, r in votes if v > 0]
    bear = [r for v, r in votes if v < 0]
    if signal == "BUY":
        print(f"\n  Reasoning: {'; '.join(bull[:2])}.")
        if bear:
            print(f"  Watch: {bear[0]}.")
    elif signal == "SELL":
        print(f"\n  Reasoning: {'; '.join(bear[:2])}.")
        if bull:
            print(f"  Upside risk: {bull[0]}.")
    else:
        if bull and bear:
            print(f"\n  Mixed signals: {bull[0]} offset by {bear[0]}.")
        else:
            print(f"\n  No strong directional signal.")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Stock analysis with Buy/Hold/Sell signal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("symbol", help="Ticker symbol (e.g. AAPL, MSFT, TSLA, BRK-B)")
    parser.add_argument(
        "--av-key",
        default=os.environ.get("ALPHA_VANTAGE_API_KEY"),
        metavar="KEY",
        help="Alpha Vantage API key for RSI/MACD/SMA (or set ALPHA_VANTAGE_API_KEY)",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    print(f"Fetching data for {symbol}…", file=sys.stderr)

    data = get_quote_and_fundamentals(symbol)
    if not data or not data.get("price"):
        print(
            f"Error: could not fetch data for '{symbol}'. "
            "Check the ticker symbol and try again.",
            file=sys.stderr,
        )
        sys.exit(1)

    technicals = None
    if args.av_key:
        print("Fetching technical indicators (RSI, MACD, SMA-50)…", file=sys.stderr)
        technicals = get_technicals(symbol, args.av_key)
        if not technicals:
            print("  [warn] Alpha Vantage returned no data — check key or rate limit", file=sys.stderr)

    sig, total, votes = score(data, technicals)

    if args.json:
        print(json.dumps({
            "data": data, "technicals": technicals,
            "signal": sig, "score": total,
            "votes": [[v, r] for v, r in votes],
        }, indent=2))
        return

    print_report(data, technicals, sig, total, votes)


if __name__ == "__main__":
    main()
