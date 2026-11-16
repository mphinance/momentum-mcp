"""Indicator suite tests (mcp_server/indicators.py), fully offline.

Each indicator is checked against a plain textbook implementation written
here with loops and pandas rolling windows, and against TA-Lib when it is
installed. Recursive indicators (EMA, RSI, ATR, DI, ADX) are seeded slightly
differently by different libraries, so they are compared once the seed has
decayed; window indicators are compared on every bar.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from mcp_server import indicators as ind

N = 500
TAIL = slice(-60, None)


@pytest.fixture(scope="module")
def ohlcv() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.018, N)))
    open_ = close * np.exp(rng.normal(0, 0.006, N))
    high = np.maximum(open_, close) * np.exp(np.abs(rng.normal(0, 0.009, N)))
    low = np.minimum(open_, close) * np.exp(-np.abs(rng.normal(0, 0.009, N)))
    volume = rng.integers(1_000_000, 5_000_000, N)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


# ── textbook references ─────────────────────────────────────────────────────

def ref_ema(x: pd.Series, n: int) -> pd.Series:
    vals = x.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    first = np.flatnonzero(~np.isnan(vals))[0]
    out[first + n - 1] = vals[first:first + n].mean()
    k = 2 / (n + 1)
    for i in range(first + n, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return pd.Series(out, index=x.index)


def ref_wilder(x: np.ndarray, n: int, start: int) -> np.ndarray:
    """Wilder smoothing seeded with the mean of x[start:start+n]."""
    out = np.full(len(x), np.nan)
    out[start + n - 1] = x[start:start + n].mean()
    for i in range(start + n, len(x)):
        out[i] = (out[i - 1] * (n - 1) + x[i]) / n
    return out


def ref_rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff().to_numpy()
    gain = ref_wilder(np.where(d > 0, d, 0.0), n, 1)
    loss = ref_wilder(np.where(d < 0, -d, 0.0), n, 1)
    return pd.Series(100 - 100 / (1 + gain / loss), index=c.index)


def ref_true_range(h, l, c) -> np.ndarray:
    pc = c.shift(1)
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1).to_numpy()


def ref_dmi(h, l, c, n: int = 14):
    up, down = h.diff().to_numpy(), -l.diff().to_numpy()
    pdm = np.where((up > down) & (up > 0), up, 0.0)
    ndm = np.where((down > up) & (down > 0), down, 0.0)
    tr = ref_wilder(ref_true_range(h, l, c), n, 1)
    pdi = 100 * ref_wilder(pdm, n, 1) / tr
    ndi = 100 * ref_wilder(ndm, n, 1) / tr
    dx = 100 * np.abs(pdi - ndi) / (pdi + ndi)
    adx = ref_wilder(dx, n, n)
    return (pd.Series(v, index=c.index) for v in (adx, pdi, ndi))


def ref_stoch(h, l, c, k=14, d=3, smooth_k=3):
    raw = 100 * (c - l.rolling(k).min()) / (h.rolling(k).max() - l.rolling(k).min())
    slow_k = raw.rolling(smooth_k).mean()
    return slow_k, slow_k.rolling(d).mean()


def ref_cci(h, l, c, n=20):
    tp = (h + l + c) / 3
    mad = tp.rolling(n).apply(lambda w: np.mean(np.abs(w - w.mean())), raw=True)
    return (tp - tp.rolling(n).mean()) / (0.015 * mad)


def close_on(a: pd.Series, b: pd.Series, rows=slice(None), rtol=1e-9, atol=1e-9) -> None:
    a, b = a.iloc[rows], b.iloc[rows]
    assert a.notna().equals(b.notna()), "warm-up NaNs differ"
    np.testing.assert_allclose(a.dropna(), b.dropna(), rtol=rtol, atol=atol)


# ── against the textbook ────────────────────────────────────────────────────

def test_moving_averages(ohlcv):
    c = ohlcv["close"]
    for n in (8, 21, 34, 55, 89):
        close_on(ind.ema(c, n), ref_ema(c, n))
    for n in (50, 100, 200):
        close_on(ind.sma(c, n), c.rolling(n).mean())


def test_rsi_is_wilder(ohlcv):
    c = ohlcv["close"]
    close_on(ind.rsi(c, 14), ref_rsi(c, 14))


def test_macd_columns_are_line_signal_histogram(ohlcv):
    c = ohlcv["close"]
    m = ind.macd(c, 12, 26, 9)
    assert list(m.columns) == ["macd", "signal", "histogram"]
    line = ref_ema(c, 12) - ref_ema(c, 26)
    close_on(m["macd"], line)
    close_on(m["signal"], ref_ema(line, 9))
    close_on(m["histogram"], line - ref_ema(line, 9))


def test_atr_is_wilder(ohlcv):
    h, l, c = ohlcv["high"], ohlcv["low"], ohlcv["close"]
    ref = pd.Series(ref_wilder(ref_true_range(h, l, c), 14, 1), index=c.index)
    close_on(ind.atr(h, l, c, 14), ref, TAIL, rtol=1e-8)


def test_adx_and_directional_indicators(ohlcv):
    h, l, c = ohlcv["high"], ohlcv["low"], ohlcv["close"]
    got = ind.adx(h, l, c, 14)
    assert list(got.columns) == ["adx", "plus_di", "minus_di"]
    adx, pdi, ndi = ref_dmi(h, l, c, 14)
    close_on(got["plus_di"], pdi, TAIL, rtol=1e-8)
    close_on(got["minus_di"], ndi, TAIL, rtol=1e-8)
    close_on(got["adx"], adx, TAIL, rtol=1e-6)
    assert got.dropna().apply(lambda s: s.between(0, 100).all()).all()


def test_window_oscillators(ohlcv):
    h, l, c = ohlcv["high"], ohlcv["low"], ohlcv["close"]
    hh, ll = h.rolling(14).max(), l.rolling(14).min()
    close_on(ind.willr(h, l, c, 14), -100 * (hh - c) / (hh - ll))
    k, d = ref_stoch(h, l, c)
    st = ind.stoch(h, l, c, k=14, d=3, smooth_k=3)
    close_on(st["k"], k)
    close_on(st["d"], d)
    close_on(ind.cci(h, l, c, 20), ref_cci(h, l, c, 20))


def test_bollinger_uses_population_std(ohlcv):
    c = ohlcv["close"]
    bb = ind.bbands(c, 20, 2)
    mid, sd = c.rolling(20).mean(), c.rolling(20).std(ddof=0)
    close_on(bb["mid"], mid)
    close_on(bb["upper"], mid + 2 * sd)
    close_on(bb["lower"], mid - 2 * sd)


def test_missing_bar_is_skipped_not_fatal(ohlcv):
    df = ohlcv.copy()
    df.loc[300, ["high", "close"]] = np.nan
    out = ind.rsi(df["close"], 14)
    assert np.isnan(out[300]) and out.iloc[301:].notna().all()
    clean = df.drop(index=300)
    close_on(out.drop(index=300), ind.rsi(clean["close"], 14))
    a = ind.adx(df["high"], df["low"], df["close"])
    assert a.iloc[300].isna().all() and a.iloc[-1].notna().all()


def test_short_history_gives_nan_not_error():
    s = pd.Series([10.0, 11.0, 12.0])
    assert ind.sma(s, 200).isna().all()
    assert ind.adx(s + 1, s - 1, s).isna().all().all()
    assert ind.macd(s).isna().all().all()


# ── against TA-Lib, when installed ──────────────────────────────────────────

def test_matches_talib(ohlcv):
    talib = pytest.importorskip("talib")

    def close_on(a, b, rows=slice(None), rtol=1e-9, atol=1e-9):
        # TA-Lib starts some outputs a bar or two later (STOCH waits for %D before
        # emitting %K), so compare every bar where both have a value.
        a, b = pd.Series(a).iloc[rows], pd.Series(b).iloc[rows]
        both = a.notna() & b.notna()
        assert both.sum() >= min(len(a), 60)
        np.testing.assert_allclose(a[both], b[both], rtol=rtol, atol=atol)

    h, l, c = ohlcv["high"], ohlcv["low"], ohlcv["close"]
    close_on(ind.rsi(c), talib.RSI(c, 14))
    close_on(ind.ema(c, 21), talib.EMA(c, 21))
    close_on(ind.sma(c, 50), talib.SMA(c, 50))
    close_on(ind.cci(h, l, c, 20), talib.CCI(h, l, c, 20))
    close_on(ind.willr(h, l, c, 14), talib.WILLR(h, l, c, 14))
    k, d = talib.STOCH(h, l, c, 14, 3, 0, 3, 0)
    st = ind.stoch(h, l, c)
    close_on(st["k"], k)
    close_on(st["d"], d)
    up, _, lo = talib.BBANDS(c, 20, 2, 2, 0)
    bb = ind.bbands(c, 20, 2)
    close_on(bb["upper"], up)
    close_on(bb["lower"], lo)
    line, sig, hist = talib.MACD(c, 12, 26, 9)
    m = ind.macd(c)
    close_on(m["macd"], line, TAIL)
    close_on(m["signal"], sig, TAIL)
    close_on(m["histogram"], hist, TAIL)
    close_on(ind.atr(h, l, c), talib.ATR(h, l, c, 14), TAIL, rtol=1e-8)
    a = ind.adx(h, l, c)
    close_on(a["plus_di"], talib.PLUS_DI(h, l, c, 14), TAIL, rtol=1e-8)
    close_on(a["minus_di"], talib.MINUS_DI(h, l, c, 14), TAIL, rtol=1e-8)
    close_on(a["adx"], talib.ADX(h, l, c, 14), TAIL, rtol=1e-6)


# ── wired into the tools ────────────────────────────────────────────────────

def test_analyze_technicals_end_to_end(ohlcv, monkeypatch):
    from mcp_server import technicals

    dates = pd.bdate_range("2025-01-02", periods=N)
    records = [
        {"date": d.date().isoformat(), **{k: float(v) for k, v in row.items() if k != "volume"},
         "volume": int(row["volume"])}
        for d, (_, row) in zip(dates, ohlcv.iterrows())
    ]

    async def fake_history(ticker, period="1y", interval="1d"):
        return records

    async def fake_live(ticker):
        return records[-1]["close"]

    monkeypatch.setattr(technicals, "get_historical_data", fake_history)
    monkeypatch.setattr(technicals, "get_live_price", fake_live)

    res = asyncio.run(technicals.analyze_technicals("ZZTEST", period="2y"))
    assert res.status == "success", res
    d = res.data
    h, l, c = ohlcv["high"], ohlcv["low"], ohlcv["close"]
    line = ref_ema(c, 12) - ref_ema(c, 26)
    sig = ref_ema(line, 9)
    adx, pdi, ndi = ref_dmi(h, l, c, 14)
    k, dd = ref_stoch(h, l, c)
    expect = {
        "rsi_14": ref_rsi(c).iloc[-1],
        "macd": line.iloc[-1],
        "macd_signal": sig.iloc[-1],
        "macd_signal_prev": sig.iloc[-2],
        "macd_histogram": (line - sig).iloc[-1],
        "ema_89": ref_ema(c, 89).iloc[-1],
        "sma_200": c.rolling(200).mean().iloc[-1],
        "adx_14": adx.iloc[-1],
        "plus_di": pdi.iloc[-1],
        "minus_di": ndi.iloc[-1],
        "minus_di_prev": ndi.iloc[-2],
        "williams_r_14": -100 * (h.rolling(14).max() - c).iloc[-1]
        / (h.rolling(14).max() - l.rolling(14).min()).iloc[-1],
        "stoch_k": k.iloc[-1],
        "stoch_d": dd.iloc[-1],
        "cci_20": ref_cci(h, l, c).iloc[-1],
        "bb_upper": (c.rolling(20).mean() + 2 * c.rolling(20).std(ddof=0)).iloc[-1],
        "vol_sma_20": ohlcv["volume"].rolling(20).mean().iloc[-1],
    }
    for key, want in expect.items():
        assert d[key] == pytest.approx(want, abs=1.5e-4), key
    assert d["atr_14"] > 0


def test_backtest_indicator_columns(ohlcv):
    from mcp_server import backtest

    df = backtest._compute_all_indicators(ohlcv.copy())
    for col in ("ema_8", "ema_89", "sma_200", "rsi_14", "macd", "macd_signal", "macd_histogram",
                "adx_14", "atr_14", "williams_r_14", "stoch_k", "stoch_d",
                "bb_lower", "bb_mid", "bb_upper", "cci_20"):
        assert df[col].iloc[-1] == df[col].iloc[-1], col  # not NaN
    np.testing.assert_allclose(df["macd_histogram"], df["macd"] - df["macd_signal"], equal_nan=True)
    preset = backtest.PRESET_STRATEGIES["macd_momentum"]
    entries = backtest._evaluate_conditions(df, preset["entry"], logic="and")
    assert entries.dtype == bool and entries.any()
