"""
TradingView Technical Analysis — 26-Indicator Consensus.

Uses tradingview-ta to fetch real-time technical analysis summaries
for any ticker. Returns the aggregate buy/sell/neutral recommendation
from 26 technical indicators (all MAs + all oscillators).

This is like getting a second opinion from 26 analysts instantly.
"""

from __future__ import annotations

import logging
from typing import Any

from tradingview_ta import TA_Handler, Interval

logger = logging.getLogger(__name__)

from mcp_server.cache import smart_cache

# Map user-friendly interval names to tradingview-ta constants
INTERVAL_MAP = {
    "1m": Interval.INTERVAL_1_MINUTE,
    "5m": Interval.INTERVAL_5_MINUTES,
    "15m": Interval.INTERVAL_15_MINUTES,
    "1h": Interval.INTERVAL_1_HOUR,
    "4h": Interval.INTERVAL_4_HOURS,
    "1d": Interval.INTERVAL_1_DAY,
    "1w": Interval.INTERVAL_1_WEEK,
    "1M": Interval.INTERVAL_1_MONTH,
}

# Crypto ticker prefixes that indicate a crypto asset (vs. a forex pair).
_CRYPTO_PREFIXES = (
    "BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "BNB", "MATIC", "DOT",
    "AVAX", "LINK", "LTC", "BCH", "XLM", "TRX", "ATOM",
)

# Default exchanges to try per TradingView screener.
_DEFAULT_EXCHANGES = {
    "america": ["NASDAQ", "NYSE", "AMEX"],
    "cfd":     ["OANDA", "FXCM", "FOREXCOM"],
    "crypto":  ["BINANCE", "COINBASE", "KRAKEN", "BITSTAMP"],
}


def _detect_screeners(ticker: str) -> list[str]:
    """Return an ordered list of TradingView screeners to try for ``ticker``.

    TradingView's TA endpoint is partitioned by *screener* (asset class),
    not just by symbol. The wrong screener always returns
    ``"Exchange or symbol not found"`` even when the symbol is valid
    elsewhere. We rank screeners by symbol shape so common cases resolve
    on the first try.

    Detection rules (first match wins):

    - ``BTCUSD`` / ``ETHUSD`` / ``...USDT`` → ``crypto`` first
    - 6-char ``XYZUSD`` that isn't crypto → ``cfd`` (forex/commodities)
    - TradingView-native ``!`` suffix (e.g. ``GC1!``) → ``cfd``
    - yfinance-style ``=`` suffix (e.g. ``GC=F``) → ``america`` then ``cfd``
    - Anything else → ``america`` (US equities)
    """
    t = ticker.upper().strip()

    # Crypto: explicit prefix or USDT suffix
    if any(t.startswith(p) for p in _CRYPTO_PREFIXES) or t.endswith("USDT"):
        return ["crypto", "cfd", "america"]

    # Forex/commodity CFD: 6-char pair ending in USD (XAUUSD, EURUSD, GBPUSD)
    if len(t) == 6 and t.endswith("USD"):
        return ["cfd", "america"]

    # TradingView-native futures/contract notation
    if t.endswith("!"):
        return ["cfd", "america"]

    # yfinance-style futures (GC=F, CL=F, ES=F) — try US futures venues first
    if "=" in t:
        return ["america", "cfd"]

    # Default: US equity
    return ["america"]


@smart_cache(open_ttl=300, closed_ttl=3600)
async def get_tv_analysis(
    ticker: str,
    interval: str = "1d",
    exchange: str = "NASDAQ",
    screener: str | None = None,
) -> dict[str, Any]:
    """
    Get TradingView technical analysis consensus for a ticker.

    Returns the aggregate recommendation (STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL)
    from 26 technical indicators, plus breakdowns by moving averages and oscillators.

    Args:
        ticker: Ticker symbol. Works across asset classes:

            - **US equities**: ``NVDA``, ``AAPL``, ``TSLA`` (screener=america)
            - **Forex / commodity CFDs**: ``XAUUSD``, ``EURUSD``, ``GBPUSD``
              (screener=cfd, exchange=OANDA)
            - **Crypto**: ``BTCUSD``, ``ETHUSD``, ``SOLUSD``
              (screener=crypto, exchange=BINANCE)
            - **Futures**: ``GC=F`` (yfinance notation) or ``GC1!``
              (TradingView notation)

        interval: Timeframe — 1m, 5m, 15m, 1h, 4h, 1d (default), 1w, 1M.
        exchange: Exchange name. Default: NASDAQ. Ignored if ``screener``
            is set explicitly.
        screener: TradingView screener — ``"america"``, ``"cfd"``, or
            ``"crypto"``. If ``None`` (default), auto-detected from the
            symbol shape. Set explicitly to bypass detection.
    """
    tv_interval = INTERVAL_MAP.get(interval, Interval.INTERVAL_1_DAY)
    symbol = ticker.upper().strip()

    # Build the ordered list of (screener, exchange) candidates to try.
    if screener:
        # User pinned the screener — honor it, but still try fallback exchanges.
        screeners = [screener]
    else:
        screeners = _detect_screeners(symbol)

    # If the caller passed an exchange for the default screener, prefer it first
    # within that screener's bucket.
    candidates: list[tuple[str, str]] = []
    for scr in screeners:
        exchanges = _DEFAULT_EXCHANGES.get(scr, [])
        if scr == screeners[0] and exchange in exchanges:
            # Try the user-provided exchange first for the primary screener
            candidates.append((scr, exchange))
            exchanges = [e for e in exchanges if e != exchange]
        for exch in exchanges:
            candidates.append((scr, exch))

    last_error: Exception | None = None
    for scr, exch in candidates:
        try:
            handler = TA_Handler(
                symbol=symbol,
                screener=scr,
                exchange=exch,
                interval=tv_interval,
            )
            analysis = handler.get_analysis()

            # Build the response
            summary = analysis.summary
            ma_rec = analysis.moving_averages
            osc_rec = analysis.oscillators

            return {
                "ticker": symbol,
                "exchange": exch,
                "screener": scr,
                "interval": interval,
                "recommendation": summary.get("RECOMMENDATION", "N/A"),
                "summary": {
                    "buy": summary.get("BUY", 0),
                    "sell": summary.get("SELL", 0),
                    "neutral": summary.get("NEUTRAL", 0),
                },
                "moving_averages": {
                    "recommendation": ma_rec.get("RECOMMENDATION", "N/A"),
                    "buy": ma_rec.get("BUY", 0),
                    "sell": ma_rec.get("SELL", 0),
                    "neutral": ma_rec.get("NEUTRAL", 0),
                },
                "oscillators": {
                    "recommendation": osc_rec.get("RECOMMENDATION", "N/A"),
                    "buy": osc_rec.get("BUY", 0),
                    "sell": osc_rec.get("SELL", 0),
                    "neutral": osc_rec.get("NEUTRAL", 0),
                },
                "indicators": {
                    "RSI": round(analysis.indicators.get("RSI", 0), 2),
                    "Stoch_K": round(analysis.indicators.get("Stoch.K", 0), 2),
                    "CCI": round(analysis.indicators.get("CCI20", 0), 2),
                    "ADX": round(analysis.indicators.get("ADX", 0), 2),
                    "MACD_signal": round(analysis.indicators.get("MACD.macd", 0), 4),
                    "ATR": round(analysis.indicators.get("ATR", 0), 2),
                    "BB_upper": round(analysis.indicators.get("BB.upper", 0), 2),
                    "BB_lower": round(analysis.indicators.get("BB.lower", 0), 2),
                    "EMA20": round(analysis.indicators.get("EMA20", 0), 2),
                    "EMA50": round(analysis.indicators.get("EMA50", 0), 2),
                    "EMA200": round(analysis.indicators.get("EMA200", 0), 2),
                    "SMA20": round(analysis.indicators.get("SMA20", 0), 2),
                    "SMA50": round(analysis.indicators.get("SMA50", 0), 2),
                    "SMA200": round(analysis.indicators.get("SMA200", 0), 2),
                    "close": round(analysis.indicators.get("close", 0), 2),
                    "volume": analysis.indicators.get("volume", 0),
                },
            }

        except Exception as e:
            last_error = e
            logger.debug("TV-TA failed for %s on %s/%s: %s", symbol, scr, exch, e)
            continue

    logger.warning("TV-TA: all candidates failed for %s: %s", symbol, last_error)
    return {
        "ticker": symbol,
        "error": f"Could not fetch TradingView analysis: {last_error}",
        "tried": [{"screener": s, "exchange": e} for s, e in candidates],
    }
