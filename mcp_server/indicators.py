"""
Indicator suite shared by technicals.py and backtest.py.

Every series comes from fin-primitives (https://pypi.org/project/fin-primitives/),
a Rust indicator library with prebuilt abi3 wheels for every CPython >= 3.9, so
there is no numba pin and nothing to install with ``--no-deps``.

Functions take pandas Series that share one index and return Series (or
DataFrames with named columns) on that same index, NaN until warmed up.
Bars with a missing value are skipped (NaN in the output) rather than
poisoning everything after them. Multi-output indicators use column names,
never positions, so callers cannot pick the wrong column.

Definitions follow TA-Lib:
  * RSI, ATR and the directional indicators use Wilder's smoothing (RMA).
  * EMA is seeded with the SMA of its first ``length`` values.
  * Bollinger Bands use the population standard deviation.
  * CCI is (TP - SMA(TP)) / (0.015 * mean absolute deviation of TP).
"""

from __future__ import annotations

import fin_primitives as fp
import numpy as np
import pandas as pd


def _float(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").astype("float64")


def _bars(*cols: pd.Series) -> list[pd.Series]:
    """The input columns as float64, keeping only bars where every column is finite."""
    floats = [_float(c) for c in cols]
    keep = np.logical_and.reduce([np.isfinite(f.to_numpy()) for f in floats])
    return [f[keep] for f in floats]


def _on_valid(fn, s: pd.Series) -> pd.Series:
    """Apply ``fn`` to the non-NaN tail of ``s`` and put it back on ``s``'s index.

    Used for second-pass smoothing (MACD signal, Stochastic %D) whose input
    starts with warm-up NaNs.
    """
    valid = s.dropna()
    if valid.empty:
        return pd.Series(np.nan, index=s.index, dtype="float64")
    return fn(valid).reindex(s.index)


def _frame(cols: dict[str, pd.Series], index: pd.Index) -> pd.DataFrame:
    return pd.DataFrame({k: v.reindex(index) for k, v in cols.items()}, index=index)


def sma(close: pd.Series, length: int) -> pd.Series:
    """Simple moving average."""
    (c,) = _bars(close)
    return fp.sma(c, length).reindex(close.index)


def ema(close: pd.Series, length: int) -> pd.Series:
    """Exponential moving average, seeded with the SMA of the first ``length`` bars."""
    (c,) = _bars(close)
    return fp.ema(c, length).reindex(close.index)


def rsi(close: pd.Series, length: int = 14) -> pd.Series:
    """Wilder's RSI (0 to 100)."""
    (c,) = _bars(close)
    return fp.rsi(c, length).reindex(close.index)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """MACD line, signal line and histogram as columns ``macd``, ``signal``, ``histogram``."""
    (c,) = _bars(close)
    line = fp.ema(c, fast) - fp.ema(c, slow)
    sig = _on_valid(lambda s: fp.ema(s, signal), line)
    return _frame({"macd": line, "signal": sig, "histogram": line - sig}, close.index)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Average True Range with Wilder's smoothing."""
    h, l, c = _bars(high, low, close)
    return fp.compute("wilder_smoothed_range", c, length, high=h, low=l).reindex(close.index)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.DataFrame:
    """ADX plus the directional indicators, as columns ``adx``, ``plus_di``, ``minus_di``.

    ADX is fin-primitives' Wilder ADX. +DI and -DI are 100 * RMA(+DM or -DM) / RMA(TR),
    with the RMA from fin-primitives' ``smma`` (Wilder moving average).
    """
    h, l, c = _bars(high, low, close)
    adx_line = fp.compute("adx", c, length, high=h, low=l)

    up = h.diff()
    down = -l.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    prev_close = c.shift(1)
    tr = pd.concat([h - l, (h - prev_close).abs(), (l - prev_close).abs()], axis=1).max(axis=1)

    # The first bar has no previous bar, so smoothing starts on the second.
    rest = slice(1, None)
    tr_rma = fp.compute("smma", tr.iloc[rest], length)
    plus_di = 100 * fp.compute("smma", plus_dm.iloc[rest], length) / tr_rma
    minus_di = 100 * fp.compute("smma", minus_dm.iloc[rest], length) / tr_rma

    return _frame({"adx": adx_line, "plus_di": plus_di, "minus_di": minus_di}, close.index)


def willr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Williams %R (-100 to 0)."""
    h, l, c = _bars(high, low, close)
    return fp.compute("williams_r", c, length, high=h, low=l).reindex(close.index)


def stoch(
    high: pd.Series, low: pd.Series, close: pd.Series, k: int = 14, d: int = 3, smooth_k: int = 3
) -> pd.DataFrame:
    """Slow stochastic as columns ``k`` and ``d``.

    ``k`` is the ``smooth_k``-bar SMA of raw %K(``k``); ``d`` is the ``d``-bar SMA of ``k``.
    """
    h, l, c = _bars(high, low, close)
    slow_k = fp.compute("stochastic_d", c, k, smooth_k, high=h, low=l)
    slow_d = _on_valid(lambda s: fp.sma(s, d), slow_k)
    return _frame({"k": slow_k, "d": slow_d}, close.index)


def bbands(close: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    """Bollinger Bands as columns ``lower``, ``mid``, ``upper`` (population std)."""
    (c,) = _bars(close)
    mid = fp.sma(c, length)
    dev = fp.compute("std_dev", c, length)
    return _frame({"lower": mid - std * dev, "mid": mid, "upper": mid + std * dev}, close.index)


def cci(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 20) -> pd.Series:
    """Commodity Channel Index with Lambert's 0.015 constant."""
    h, l, c = _bars(high, low, close)
    return fp.compute("cci", c, length, high=h, low=l).reindex(close.index)
