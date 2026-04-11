#!/usr/bin/env python3
"""analyze_stock.py — Comprehensive stock analysis with Buy/Hold/Sell signal.

Phases:
  1. Quote & price   — Yahoo Finance v7 (no key)
  2. Fundamentals    — Yahoo Finance v7 (no key)
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
import sys
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# SSL context — handles macOS Python installations where system certs aren't
# linked. Tries certifi first, falls back to an unverified context for
# read-only public financial data APIs.
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

# Yahoo Finance requires a browser User-Agent + session cookies + a crumb token
# (anti-scraping measure introduced in 2024). We use a single persistent opener
# with a cookie jar so cookies flow automatically across all YF requests.
_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
}

import http.cookiejar as _cookiejar

_yf_jar = _cookiejar.CookieJar()
_yf_opener = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=_SSL_CTX),
    urllib.request.HTTPCookieProcessor(_yf_jar),
)
_yf_crumb = None  # cached after first call


def _yf_init():
    """Establish Yahoo Finance session (cookies + crumb). Called once per run."""
    global _yf_crumb
    if _yf_crumb is not None:
        return

    try:
        # Step 1: load Yahoo Finance home — this sets required session cookies
        _yf_opener.open(
            urllib.request.Request("https://finance.yahoo.com/", headers=_YF_HEADERS),
            timeout=15,
        )
        # Step 2: exchange session cookies for a crumb token
        with _yf_opener.open(
            urllib.request.Request(
                "https://query2.finance.yahoo.com/v1/test/getcrumb",
                headers=_YF_HEADERS,
            ),
            timeout=15,
        ) as resp:
            _yf_crumb = resp.read().decode("utf-8").strip()
    except Exception as exc:
        print(f"  [warn] Yahoo Finance session setup failed: {exc}", file=sys.stderr)
        _yf_crumb = ""  # sentinel: don't retry


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch(url):
    """Fetch a Yahoo Finance URL using the shared session opener."""
    _yf_init()
    if _yf_crumb and "crumb=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}crumb={_yf_crumb}"
    req = urllib.request.Request(url, headers=_YF_HEADERS)
    try:
        with _yf_opener.open(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"  [warn] HTTP {exc.code} for {url}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  [warn] {exc}", file=sys.stderr)
        return None


def _fetch_plain(url):
    """Fetch a non-Yahoo URL (e.g. Alpha Vantage) without session overhead."""
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"  [warn] HTTP {exc.code} for {url}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  [warn] {exc}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Phase 1 + 2 — Quote & Fundamentals (Yahoo Finance, no key)
# ---------------------------------------------------------------------------

def get_quote_and_fundamentals(symbol):
    """Fetch price, volume, fundamentals via Yahoo Finance.

    Tries v7/quote first (rich fundamentals), falls back to v8/chart
    (price + basic meta) if the first endpoint is rate-limited.
    """
    # Primary: v7/quote — returns fundamentals + price in one call
    url = f"https://query2.finance.yahoo.com/v7/finance/quote?symbols={symbol}"
    data = _fetch(url)

    if data:
        try:
            result = data["quoteResponse"]["result"]
            if result:
                return _parse_v7_quote(result[0])
        except (KeyError, IndexError):
            pass

    # Fallback: v8/chart — always-on endpoint with price + basic metadata
    print("  [info] Falling back to chart endpoint…", file=sys.stderr)
    chart_url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?interval=1d&range=1y&includePrePost=false"
    )
    chart_data = _fetch(chart_url)
    if chart_data:
        try:
            meta = chart_data["chart"]["result"][0]["meta"]
            return _parse_chart_meta(meta, symbol)
        except (KeyError, IndexError, TypeError):
            pass

    return None


def _parse_v7_quote(q):
    """Extract fields from a v7/quote result object."""
    def _get(key):
        return q.get(key)

    return {
        "symbol": _get("symbol"),
        "name": _get("longName") or _get("shortName"),
        "price": _get("regularMarketPrice"),
        "prev_close": _get("regularMarketPreviousClose"),
        "change_pct": _get("regularMarketChangePercent"),
        "day_high": _get("regularMarketDayHigh"),
        "day_low": _get("regularMarketDayLow"),
        "w52_high": _get("fiftyTwoWeekHigh"),
        "w52_low": _get("fiftyTwoWeekLow"),
        "volume": _get("regularMarketVolume"),
        "avg_volume": _get("averageDailyVolume3Month"),
        "market_cap": _get("marketCap"),
        "pe_trailing": _get("trailingPE"),
        "pe_forward": _get("forwardPE"),
        "eps": _get("epsTrailingTwelveMonths"),
        "analyst_target": _get("targetMeanPrice"),
        "analyst_rating": _get("averageAnalystRating"),
        "revenue_growth": _get("revenueGrowth"),
        "dividend_yield": _get("dividendYield"),
        "currency": _get("currency") or "USD",
    }


def _parse_chart_meta(meta, symbol):
    """Extract available fields from v8/chart meta (price-focused, fewer fundamentals)."""
    price = meta.get("regularMarketPrice") or meta.get("chartPreviousClose")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    change_pct = None
    if price and prev and prev != 0:
        change_pct = (price - prev) / prev * 100

    return {
        "symbol": meta.get("symbol", symbol),
        "name": meta.get("longName") or meta.get("shortName") or symbol,
        "price": price,
        "prev_close": prev,
        "change_pct": change_pct,
        "day_high": meta.get("regularMarketDayHigh"),
        "day_low": meta.get("regularMarketDayLow"),
        "w52_high": meta.get("fiftyTwoWeekHigh"),
        "w52_low": meta.get("fiftyTwoWeekLow"),
        "volume": meta.get("regularMarketVolume"),
        "avg_volume": meta.get("averageDailyVolume3Month") or meta.get("averageDailyVolume10Day"),
        "market_cap": meta.get("marketCap"),
        "pe_trailing": meta.get("trailingPE"),
        "pe_forward": None,
        "eps": meta.get("epsTrailingTwelveMonths"),
        "analyst_target": None,
        "analyst_rating": None,
        "revenue_growth": None,
        "dividend_yield": None,
        "currency": meta.get("currency") or "USD",
    }

    def _get(key):
        return q.get(key)

    price = _get("regularMarketPrice")
    prev_close = _get("regularMarketPreviousClose")
    change_pct = _get("regularMarketChangePercent")

    return {
        # Identity
        "symbol": _get("symbol") or symbol,
        "name": _get("longName") or _get("shortName") or symbol,
        # Price
        "price": price,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "day_high": _get("regularMarketDayHigh"),
        "day_low": _get("regularMarketDayLow"),
        "w52_high": _get("fiftyTwoWeekHigh"),
        "w52_low": _get("fiftyTwoWeekLow"),
        # Volume
        "volume": _get("regularMarketVolume"),
        "avg_volume": _get("averageDailyVolume3Month"),
        # Fundamentals
        "market_cap": _get("marketCap"),
        "pe_trailing": _get("trailingPE"),
        "pe_forward": _get("forwardPE"),
        "eps": _get("epsTrailingTwelveMonths"),
        "analyst_target": _get("targetMeanPrice"),
        "analyst_rating": _get("averageAnalystRating"),
        "revenue_growth": _get("revenueGrowth"),
        "profit_margins": _get("profitMargins"),
        "dividend_yield": _get("dividendYield"),
        "currency": _get("currency") or "USD",
    }


# ---------------------------------------------------------------------------
# Phase 3 — Technical Indicators (Alpha Vantage, optional key)
# ---------------------------------------------------------------------------

_AV_BASE = "https://www.alphavantage.co/query"


def get_technicals(symbol, av_key):
    """Fetch RSI-14, MACD, SMA-50 from Alpha Vantage."""

    def _av(function, extra):
        url = f"{_AV_BASE}?function={function}&symbol={symbol}&apikey={av_key}&{extra}"
        return _fetch_plain(url)

    result = {}

    # RSI-14
    rsi_data = _av("RSI", "interval=daily&time_period=14&series_type=close")
    if rsi_data and "Technical Analysis: RSI" in rsi_data:
        series = rsi_data["Technical Analysis: RSI"]
        latest = sorted(series.keys())[-1]
        result["rsi"] = float(series[latest]["RSI"])
        result["rsi_date"] = latest

    # MACD (default: fast=12, slow=26, signal=9)
    macd_data = _av("MACD", "interval=daily&series_type=close")
    if macd_data and "Technical Analysis: MACD" in macd_data:
        series = macd_data["Technical Analysis: MACD"]
        dates = sorted(series.keys())
        if len(dates) >= 2:
            cur = series[dates[-1]]
            prv = series[dates[-2]]
            macd_cur = float(cur["MACD"])
            sig_cur = float(cur["MACD_Signal"])
            macd_prv = float(prv["MACD"])
            sig_prv = float(prv["MACD_Signal"])

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
    """Each metric votes -1/0/+1. Signal = BUY (≥+2), HOLD (-1..+1), SELL (≤-2)."""
    votes = []  # list of (int, str)

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
            if above is True:
                votes.append((0, "MACD above signal line (no recent crossover)"))
            elif above is False:
                votes.append((0, "MACD below signal line (no recent crossover)"))

        # Price vs SMA-50
        sma_50 = technicals.get("sma_50")
        if sma_50 and price:
            if price > sma_50:
                votes.append((+1, f"Price ${price:.2f} above SMA-50 ${sma_50:.2f}"))
            else:
                votes.append((-1, f"Price ${price:.2f} below SMA-50 ${sma_50:.2f}"))

    total = sum(v for v, _ in votes)
    if total >= 2:
        signal = "BUY"
    elif total <= -2:
        signal = "SELL"
    else:
        signal = "HOLD"

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
    return str(n)


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

    # Price block
    price = data.get("price")
    chg = data.get("change_pct")
    chg_str = f"  ({chg:+.2f}% today)" if chg is not None else ""
    print(f"PRICE     ${price:.2f}{chg_str}" if price else "PRICE     N/A")

    w52h = data.get("w52_high")
    w52l = data.get("w52_low")
    if w52l and w52h:
        # Show where current price sits in the 52w range
        if price:
            pct_range = (price - w52l) / (w52h - w52l) * 100
            bar_len = 20
            filled = int(pct_range / 100 * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"52W       ${w52l:.2f} [{bar}] ${w52h:.2f}")
        else:
            print(f"52W       ${w52l:.2f} – ${w52h:.2f}")

    vol = data.get("volume")
    avg_vol = data.get("avg_volume")
    if vol:
        avg_str = f"  (avg {_fmt_vol(avg_vol)})" if avg_vol else ""
        print(f"Volume    {_fmt_vol(vol)}{avg_str}")

    # Fundamentals block
    print(f"\nFUNDAMENTALS")
    print(f"  Market Cap     {_fmt_cap(data.get('market_cap'))}")

    pe = data.get("pe_trailing") or data.get("pe_forward")
    pe_label = "(fwd)" if data.get("pe_trailing") is None and data.get("pe_forward") else ""
    if pe and pe > 0:
        pe_note = "  ⚠ elevated" if pe > 35 else ("  ✓ low" if pe < 15 else "")
        print(f"  P/E Ratio      {pe:.1f} {pe_label}{pe_note}")
    else:
        print(f"  P/E Ratio      N/A")

    eps = data.get("eps")
    print(f"  EPS (TTM)      ${eps:.2f}" if eps else "  EPS (TTM)      N/A")

    target = data.get("analyst_target")
    if target and price:
        upside = (target - price) / price * 100
        print(f"  Analyst Target ${target:.2f}  ({upside:+.1f}% upside)")
    elif target:
        print(f"  Analyst Target ${target:.2f}")

    rating = data.get("analyst_rating")
    if rating:
        print(f"  Analyst Rating {rating}")

    dy = data.get("dividend_yield")
    if dy:
        print(f"  Dividend Yield {_fmt_pct(dy)}")

    # Technicals block
    if technicals:
        print(f"\nTECHNICALS  (Alpha Vantage)")
        rsi = technicals.get("rsi")
        if rsi is not None:
            if rsi > 70:
                rsi_note = "  overbought ⚠"
            elif rsi < 30:
                rsi_note = "  oversold ✓"
            else:
                rsi_note = "  neutral"
            print(f"  RSI-14    {rsi:.1f}{rsi_note}")

        cross = technicals.get("macd_cross")
        if cross == "bullish":
            print(f"  MACD      bullish crossover ✓")
        elif cross == "bearish":
            print(f"  MACD      bearish crossover ⚠")
        elif cross == "none":
            above = technicals.get("macd_above")
            state = "above" if above else "below"
            print(f"  MACD      {state} signal line  (no crossover)")

        sma_50 = technicals.get("sma_50")
        if sma_50 and price:
            icon = "✓" if price > sma_50 else "⚠"
            direction = "above" if price > sma_50 else "below"
            print(f"  SMA-50    ${sma_50:.2f}  price {direction} {icon}")
    else:
        print(f"\nTECHNICALS  not available — pass --av-key for RSI, MACD, SMA")

    # Signal votes
    print(f"\nSIGNAL VOTES")
    for vote, reason in votes:
        marker = f"{vote:+d}" if vote != 0 else " 0"
        print(f"  {marker}  {reason}")
    print(f"  {'─'*40}")

    signal_labels = {"BUY": "▲ BUY", "HOLD": "◆ HOLD", "SELL": "▼ SELL"}
    print(f"  {total:+d}  →  {signal_labels[signal]}")

    # One-line reasoning
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
        print(
            json.dumps(
                {
                    "data": data,
                    "technicals": technicals,
                    "signal": sig,
                    "score": total,
                    "votes": [[v, r] for v, r in votes],
                },
                indent=2,
            )
        )
        return

    print_report(data, technicals, sig, total, votes)


if __name__ == "__main__":
    main()
