"""
Technical analysis module using fin-primitives (see mcp_server/indicators.py).

Fetches historical data and computes RSI and MACD indicators,
returning the most recent values alongside price context.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from mcp_server import indicators as ta
from mcp_server.data import get_historical_data, get_live_price

logger = logging.getLogger(__name__)

from mcp_server.cache import smart_cache
from mcp_server.schema import SignalResult


@smart_cache(open_ttl=300, closed_ttl=3600)
async def analyze_technicals(
    ticker: str,
    period: str = "1y",
) -> SignalResult:
    """Compute a full technical indicator suite for a ticker.

    Fetches daily OHLCV data and applies:
    - **RSI(14)** — Relative Strength Index
    - **MACD(12, 26, 9)** — Moving Average Convergence Divergence
    - **EMA Stack (8/21/34/55/89)** — Michael's signature momentum stack
    - **SMA (50/100/200)** — Classic long-term trend levels
    - **ADX(14)** — Average Directional Index (trend strength)
    - **ATR(14)** — Average True Range (volatility)
    - **Williams %R(14)** — Williams Percent Range
    - **Stochastic (14,3,3)** — %K and %D
    - **Bollinger Bands (20,2)** — Upper/Middle/Lower
    - **CCI(20)** — Commodity Channel Index

    Args:
        ticker: Stock ticker symbol (e.g. ``"AAPL"``).
        period: Lookback period. Defaults to ``"6mo"``.

    Returns:
        A SignalResult with all indicator values + a plain-English summary.
    """
    ticker = ticker.strip().upper()

    try:
        # Fetch OHLCV data (already validated inside data module)
        records = await get_historical_data(ticker, period=period, interval="1d")

        if len(records) < 35:
            return SignalResult.error_msg(
                f"Insufficient data for '{ticker}': got {len(records)} bars but "
                f"need ≥35 for reliable indicators. Try a longer period."
            )

        df = pd.DataFrame(records)
        close = pd.to_numeric(df["close"], errors="coerce")
        high = pd.to_numeric(df["high"], errors="coerce")
        low = pd.to_numeric(df["low"], errors="coerce")
        volume = pd.to_numeric(df["volume"], errors="coerce")

        # Extract the latest bar info
        latest = df.iloc[-1]
        latest_date = latest["date"]

        # Use live price — OHLCV close is stale during market hours
        try:
            latest_close = await get_live_price(ticker)
        except Exception:
            latest_close = latest["close"]

        # --- Core Oscillators ---
        rsi_series = ta.rsi(close, length=14)
        rsi_val = _extract_last(rsi_series)
        rsi_prev = _extract_at(rsi_series, -2)  # Previous bar RSI for cross detection

        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        macd_val = _extract_last(macd_df["macd"])
        macd_prev = _extract_at(macd_df["macd"], -2)
        signal_val = _extract_last(macd_df["signal"])
        signal_prev = _extract_at(macd_df["signal"], -2)
        hist_val = _extract_last(macd_df["histogram"])

        # --- EMA Stack (8/21/34/55/89) — Michael's signature ---
        ema_stack = {}
        for length in [8, 21, 34, 55, 89]:
            ema_stack[f"ema_{length}"] = _extract_last(ta.ema(close, length=length))

        # --- SMA (50/100/200) ---
        sma_50 = _extract_last(ta.sma(close, length=50))
        sma_100 = _extract_last(ta.sma(close, length=100))
        sma_200 = _extract_last(ta.sma(close, length=200))

        # --- ADX(14) — trend strength + DI components ---
        adx_df = ta.adx(high, low, close, length=14)
        adx_val = _extract_last(adx_df["adx"])
        # DI+ and DI- for directional movement
        plus_di = _extract_last(adx_df["plus_di"])
        minus_di = _extract_last(adx_df["minus_di"])
        plus_di_prev = _extract_at(adx_df["plus_di"], -2)
        minus_di_prev = _extract_at(adx_df["minus_di"], -2)

        # --- ATR(14) — volatility ---
        atr_val = _extract_last(ta.atr(high, low, close, length=14))

        # --- Williams %R(14) ---
        willr_val = _extract_last(ta.willr(high, low, close, length=14))

        # --- Stochastic (14,3,3) ---
        stoch_df = ta.stoch(high, low, close, k=14, d=3, smooth_k=3)
        stoch_k = _extract_last(stoch_df["k"])
        stoch_d = _extract_last(stoch_df["d"])

        # --- Bollinger Bands (20, 2) ---
        bb_df = ta.bbands(close, length=20, std=2)
        bb_lower = _extract_last(bb_df["lower"])
        bb_mid = _extract_last(bb_df["mid"])
        bb_upper = _extract_last(bb_df["upper"])

        # --- CCI(20) ---
        cci_val = _extract_last(ta.cci(high, low, close, length=20))

        # --- Volume metrics (for spike detection) ---
        vol_current = _extract_last(volume)
        vol_prev = _extract_at(volume, -2)
        vol_sma_20 = _extract_last(ta.sma(volume, length=20))

        # --- Previous close (for EMA breakout detection) ---
        close_prev = _extract_at(close, -2)

        # --- EMA Stack state ---
        ema_vals = [ema_stack.get(f"ema_{l}") for l in [8, 21, 34, 55, 89]]
        ema_stack_bullish = (
            all(v is not None for v in ema_vals)
            and all(ema_vals[i] > ema_vals[i + 1] for i in range(len(ema_vals) - 1))
        )

        # Build analysis
        analysis = _build_analysis(ticker, latest_close, rsi_val, macd_val, signal_val, hist_val)

        # Note missing SMAs so the LLM explains WHY, not just "null"
        sma_notes = []
        bars_available = len(records)
        if sma_200 is None and bars_available < 200:
            sma_notes.append(f"SMA(200) unavailable — only {bars_available} trading days of history (need 200)")
        if sma_100 is None and bars_available < 100:
            sma_notes.append(f"SMA(100) unavailable — only {bars_available} trading days of history (need 100)")

        data = {
            "ticker": ticker,
            "date": latest_date,
            "close": latest_close,
            # Core oscillators
            "rsi_14": rsi_val,
            "rsi_14_prev": rsi_prev,
            "macd": macd_val,
            "macd_prev": macd_prev,
            "macd_signal": signal_val,
            "macd_signal_prev": signal_prev,
            "macd_histogram": hist_val,
            # EMA stack (8/21/34/55/89)
            **ema_stack,
            "ema_stack_bullish": ema_stack_bullish,
            # SMAs
            "sma_50": sma_50,
            "sma_100": sma_100,
            "sma_200": sma_200,
            # Trend & volatility
            "adx_14": adx_val,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "plus_di_prev": plus_di_prev,
            "minus_di_prev": minus_di_prev,
            "atr_14": atr_val,
            # Momentum oscillators
            "williams_r_14": willr_val,
            "stoch_k": stoch_k,
            "stoch_d": stoch_d,
            "cci_20": cci_val,
            # Bollinger Bands
            "bb_upper": bb_upper,
            "bb_mid": bb_mid,
            "bb_lower": bb_lower,
            # Volume
            "volume": vol_current,
            "volume_prev": vol_prev,
            "vol_sma_20": vol_sma_20,
            # Previous close
            "close_prev": close_prev,
            # Summary
            "analysis": analysis,
        }

        if sma_notes:
            data["data_notes"] = sma_notes

        return SignalResult.success(data)

    except Exception as e:
        logger.error("Technicals failed for %s: %s", ticker, e)
        return SignalResult.error_msg(str(e))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_last(series: pd.Series | None) -> float | None:
    """Get the last non-NaN value from a series, rounded."""
    return _extract_at(series, -1)


def _extract_at(series: pd.Series | None, idx: int = -1) -> float | None:
    """Get the value at a specific index from a series, rounded.

    Args:
        series: A pandas Series (e.g., RSI values over time).
        idx: The index position (negative for from-end, e.g. -2 = second-to-last).

    Returns:
        Rounded float value, or None if unavailable.
    """
    if series is None or series.empty:
        return None
    try:
        val = series.iloc[idx]
        if pd.isna(val):
            return None
        return round(float(val), 4)
    except (IndexError, KeyError):
        return None


def _build_analysis(
    ticker: str,
    close: Any,
    rsi: float | None,
    macd: float | None,
    signal: float | None,
    histogram: float | None,
) -> str:
    """Generate a concise plain-English analysis string."""
    parts: list[str] = [f"{ticker} last traded at {close}."]

    if rsi is not None:
        if rsi >= 70:
            parts.append(f"RSI(14) is {rsi:.1f} — overbought territory.")
        elif rsi <= 30:
            parts.append(f"RSI(14) is {rsi:.1f} — oversold territory.")
        else:
            parts.append(f"RSI(14) is {rsi:.1f} — neutral range.")

    if macd is not None and signal is not None:
        if macd > signal:
            parts.append("MACD is above the signal line (bullish crossover).")
        else:
            parts.append("MACD is below the signal line (bearish crossover).")

    if histogram is not None:
        direction = "expanding" if histogram > 0 else "contracting"
        parts.append(f"Histogram is {direction} at {histogram:.4f}.")

    return " ".join(parts)
