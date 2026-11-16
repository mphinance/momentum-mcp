"""
Phase 7 — Backtesting Engine.

Vectorised strategy backtesting with fin-primitives indicators.
Strategies are defined as JSON-serialisable condition dicts so the LLM
can translate natural language → structured rules → replay.

Key design decisions:
  • Entry signals on day T execute at OPEN of T+1 (no lookahead bias).
  • Exits checked intraday: stop-loss/take-profit hit on the bar itself,
    signal-based exits execute next open.
  • Trailing stops are deferred to Phase 7.5 (vectorisation is hard).
  • Pattern extraction (Phase 9 merge) runs after each backtest.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from mcp_server import indicators as ta
from mcp_server.data import get_historical_data

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_STRATEGIES_FILE = PROJECT_ROOT / "data" / "saved_strategies.json"


# ── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class Condition:
    """A single entry or exit condition."""
    indicator: str       # "ema_8", "rsi_14", "close", etc.
    op: str              # "crosses_above", "crosses_below", ">", "<", ">=", "<=", "between"
    value: float | str   # numeric threshold OR another indicator name
    lookback: int = 0    # "was true in any of last N bars" (0 = current bar only)


@dataclass
class Trade:
    """A single completed or open trade."""
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    direction: str = "long"
    pnl: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0
    exit_reason: str = ""


@dataclass
class BacktestResult:
    """Full results of a backtest run."""
    ticker: str
    strategy_name: str
    period: str
    total_return_pct: float = 0.0
    buy_and_hold_pct: float = 0.0
    cagr_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_days: int = 0
    total_trades: int = 0
    win_rate_pct: float = 0.0
    avg_trade_pct: float = 0.0
    best_trade_pct: float = 0.0
    worst_trade_pct: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_hold_days: float = 0.0
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)


# ── Preset Strategies ────────────────────────────────────────────────────────

PRESET_STRATEGIES: dict[str, dict] = {
    "ema_crossover": {
        "description": "EMA 8/21 crossover with RSI filter — Michael's bread & butter",
        "entry": [
            {"indicator": "ema_8", "op": "crosses_above", "value": "ema_21"},
            {"indicator": "rsi_14", "op": ">", "value": 50},
        ],
        "exit": [
            {"indicator": "ema_8", "op": "crosses_below", "value": "ema_21"},
        ],
    },
    "rsi_bounce": {
        "description": "Oversold bounce in uptrend — RSI < 30, above SMA200",
        "entry": [
            {"indicator": "rsi_14", "op": "<", "value": 30},
            {"indicator": "close", "op": ">", "value": "sma_200"},
        ],
        "exit": [
            {"indicator": "rsi_14", "op": ">", "value": 60},
        ],
    },
    "macd_momentum": {
        "description": "MACD signal cross with ADX trend filter",
        "entry": [
            {"indicator": "macd", "op": "crosses_above", "value": "macd_signal"},
            {"indicator": "adx_14", "op": ">", "value": 25},
        ],
        "exit": [
            {"indicator": "macd", "op": "crosses_below", "value": "macd_signal"},
        ],
    },
    "bollinger_squeeze": {
        "description": "Mean reversion at lower Bollinger Band",
        "entry": [
            {"indicator": "close", "op": "<", "value": "bb_lower"},
            {"indicator": "rsi_14", "op": "<", "value": 35},
        ],
        "exit": [
            {"indicator": "close", "op": ">", "value": "bb_mid"},
        ],
    },
    "golden_cross": {
        "description": "Classic SMA 50/200 golden cross — long-term trend",
        "entry": [
            {"indicator": "sma_50", "op": "crosses_above", "value": "sma_200"},
        ],
        "exit": [
            {"indicator": "sma_50", "op": "crosses_below", "value": "sma_200"},
        ],
    },
    "ema_stack_breakout": {
        "description": "Full EMA stack alignment + ADX trend strength",
        "entry": [
            {"indicator": "ema_8", "op": ">", "value": "ema_21"},
            {"indicator": "ema_21", "op": ">", "value": "ema_34"},
            {"indicator": "adx_14", "op": ">", "value": 20},
        ],
        "exit": [
            {"indicator": "ema_8", "op": "crosses_below", "value": "ema_21"},
        ],
    },
}


# ── Indicator Computation ────────────────────────────────────────────────────

def _compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute the full indicator suite and return enriched DataFrame.

    Column naming matches PHASE7.md indicator registry exactly.
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)

    # EMAs
    for length in [8, 21, 34, 55, 89]:
        series = ta.ema(close, length=length)
        if series is not None:
            df[f"ema_{length}"] = series.astype(float)

    # SMAs
    for length in [50, 100, 200]:
        series = ta.sma(close, length=length)
        if series is not None:
            df[f"sma_{length}"] = series.astype(float)

    # RSI
    rsi = ta.rsi(close, length=14)
    if rsi is not None:
        df["rsi_14"] = rsi.astype(float)

    # MACD
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None:
        df["macd"] = macd_df["macd"].astype(float)
        df["macd_signal"] = macd_df["signal"].astype(float)
        df["macd_histogram"] = macd_df["histogram"].astype(float)

    # ADX
    adx_df = ta.adx(high, low, close, length=14)
    if adx_df is not None:
        df["adx_14"] = adx_df["adx"].astype(float)

    # ATR
    atr = ta.atr(high, low, close, length=14)
    if atr is not None:
        df["atr_14"] = atr.astype(float)

    # Williams %R
    willr = ta.willr(high, low, close, length=14)
    if willr is not None:
        df["williams_r_14"] = willr.astype(float)

    # Stochastic
    stoch_df = ta.stoch(high, low, close, k=14, d=3, smooth_k=3)
    if stoch_df is not None:
        df["stoch_k"] = stoch_df["k"].astype(float)
        df["stoch_d"] = stoch_df["d"].astype(float)

    # Bollinger Bands
    bb_df = ta.bbands(close, length=20, std=2)
    if bb_df is not None:
        df["bb_lower"] = bb_df["lower"].astype(float)
        df["bb_mid"] = bb_df["mid"].astype(float)
        df["bb_upper"] = bb_df["upper"].astype(float)

    # CCI
    cci = ta.cci(high, low, close, length=20)
    if cci is not None:
        df["cci_20"] = cci.astype(float)

    return df


# ── Condition Evaluation ─────────────────────────────────────────────────────

def _resolve_series(df: pd.DataFrame, ref: float | str) -> pd.Series:
    """Resolve a condition value to a Series — either a constant or column ref."""
    if isinstance(ref, str) and ref in df.columns:
        return df[ref].astype(float)
    return pd.Series(float(ref), index=df.index)


def _eval_single_condition(df: pd.DataFrame, cond: dict) -> pd.Series:
    """Evaluate a single condition, returning a boolean Series."""
    indicator = cond["indicator"]
    op = cond["op"]
    value = cond["value"]
    lookback = cond.get("lookback", 0)

    if indicator not in df.columns:
        logger.warning("Indicator '%s' not found in DataFrame, condition always False", indicator)
        return pd.Series(False, index=df.index)

    lhs = df[indicator].astype(float)
    rhs = _resolve_series(df, value)

    if op == "crosses_above":
        result = (lhs > rhs) & (lhs.shift(1) <= rhs.shift(1))
    elif op == "crosses_below":
        result = (lhs < rhs) & (lhs.shift(1) >= rhs.shift(1))
    elif op == ">":
        result = lhs > rhs
    elif op == ">=":
        result = lhs >= rhs
    elif op == "<":
        result = lhs < rhs
    elif op == "<=":
        result = lhs <= rhs
    elif op == "==":
        result = (lhs - rhs).abs() < 1e-6
    elif op == "between":
        # value should be a list [low, high] but fallback to treating as > value
        if isinstance(value, list) and len(value) == 2:
            lo = _resolve_series(df, value[0])
            hi = _resolve_series(df, value[1])
            result = (lhs >= lo) & (lhs <= hi)
        else:
            result = pd.Series(False, index=df.index)
    else:
        logger.warning("Unknown operator '%s', treating as always False", op)
        result = pd.Series(False, index=df.index)

    # Lookback: "was true in any of last N bars"
    if lookback > 0:
        result = result.rolling(window=lookback, min_periods=1).max().fillna(0).astype(bool)

    return result.fillna(False)


def _evaluate_conditions(
    df: pd.DataFrame,
    conditions: list[dict],
    logic: str = "and",
) -> pd.Series:
    """Evaluate multiple conditions with AND or OR logic."""
    if not conditions:
        return pd.Series(False, index=df.index)

    signals = [_eval_single_condition(df, c) for c in conditions]

    if logic == "and":
        combined = signals[0]
        for s in signals[1:]:
            combined = combined & s
    else:  # OR
        combined = signals[0]
        for s in signals[1:]:
            combined = combined | s

    return combined.fillna(False)


# ── Trade Simulation ─────────────────────────────────────────────────────────

def _simulate_trades(
    df: pd.DataFrame,
    entries: pd.Series,
    exits: pd.Series,
    initial_capital: float = 10_000,
    position_size: float = 1.0,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    trailing_stop_pct: float | None = None,
    slippage_bps: int = 10,
) -> tuple[list[Trade], list[dict]]:
    """Walk forward through the DataFrame, simulating trades.

    Lookahead prevention: signals on bar T → execute at OPEN of bar T+1.
    """
    # Shift signals forward by 1 bar (execute next day open)
    entry_signals = entries.shift(1).fillna(False).infer_objects(copy=False).astype(bool)
    exit_signals = exits.shift(1).fillna(False).infer_objects(copy=False).astype(bool)

    trades: list[Trade] = []
    equity_curve: list[dict] = []
    capital = initial_capital
    in_trade = False
    entry_price = 0.0
    trade_high = 0.0
    entry_date = ""
    entry_idx = 0
    slippage_mult = 1 + (slippage_bps / 10_000)

    for i in range(len(df)):
        row = df.iloc[i]
        date_str = str(row.get("date", ""))
        open_price = float(row["open"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        close_price = float(row["close"])

        if in_trade:
            # Update high water mark for trailing stop
            if trailing_stop_pct is not None:
                trade_high = max(trade_high, high_price)
                trailing_stop_price = trade_high * (1 - trailing_stop_pct / 100)
                if low_price <= trailing_stop_price:
                    exit_price = trailing_stop_price / slippage_mult
                    pnl_pct = ((exit_price / entry_price) - 1) * 100
                    trades.append(Trade(
                        entry_date=entry_date, entry_price=round(entry_price, 4),
                        exit_date=date_str, exit_price=round(exit_price, 4),
                        pnl=round((exit_price - entry_price) * (capital * position_size / entry_price), 2),
                        pnl_pct=round(pnl_pct, 2),
                        hold_days=i - entry_idx,
                        exit_reason="trailing_stop",
                    ))
                    capital += trades[-1].pnl
                    in_trade = False
                    continue

            # Check stop loss (intraday)
            if stop_loss_pct is not None:
                stop_price = entry_price * (1 - stop_loss_pct / 100)
                if low_price <= stop_price:
                    exit_price = stop_price / slippage_mult
                    pnl_pct = ((exit_price / entry_price) - 1) * 100
                    trades.append(Trade(
                        entry_date=entry_date, entry_price=round(entry_price, 4),
                        exit_date=date_str, exit_price=round(exit_price, 4),
                        pnl=round((exit_price - entry_price) * (capital * position_size / entry_price), 2),
                        pnl_pct=round(pnl_pct, 2),
                        hold_days=i - entry_idx,
                        exit_reason="stop_loss",
                    ))
                    capital += trades[-1].pnl
                    in_trade = False
                    continue

            # Check take profit (intraday)
            if take_profit_pct is not None:
                tp_price = entry_price * (1 + take_profit_pct / 100)
                if high_price >= tp_price:
                    exit_price = tp_price / slippage_mult
                    pnl_pct = ((exit_price / entry_price) - 1) * 100
                    trades.append(Trade(
                        entry_date=entry_date, entry_price=round(entry_price, 4),
                        exit_date=date_str, exit_price=round(exit_price, 4),
                        pnl=round((exit_price - entry_price) * (capital * position_size / entry_price), 2),
                        pnl_pct=round(pnl_pct, 2),
                        hold_days=i - entry_idx,
                        exit_reason="take_profit",
                    ))
                    capital += trades[-1].pnl
                    in_trade = False
                    continue

            # Check signal-based exit
            if exit_signals.iloc[i]:
                exit_price = open_price * slippage_mult
                pnl_pct = ((exit_price / entry_price) - 1) * 100
                trades.append(Trade(
                    entry_date=entry_date, entry_price=round(entry_price, 4),
                    exit_date=date_str, exit_price=round(exit_price, 4),
                    pnl=round((exit_price - entry_price) * (capital * position_size / entry_price), 2),
                    pnl_pct=round(pnl_pct, 2),
                    hold_days=i - entry_idx,
                    exit_reason="signal",
                ))
                capital += trades[-1].pnl
                in_trade = False

        elif entry_signals.iloc[i]:
            # Enter trade at open with slippage
            entry_price = open_price * slippage_mult
            trade_high = high_price
            entry_date = date_str
            entry_idx = i
            in_trade = True

        # Track equity
        if in_trade:
            unrealised = (close_price - entry_price) * (capital * position_size / entry_price)
            eq = capital + unrealised
        else:
            eq = capital

        equity_curve.append({"date": date_str, "equity": round(eq, 2)})

    # Close any open trade at last close
    if in_trade and len(df) > 0:
        last = df.iloc[-1]
        exit_price = float(last["close"])
        pnl_pct = ((exit_price / entry_price) - 1) * 100
        trades.append(Trade(
            entry_date=entry_date, entry_price=round(entry_price, 4),
            exit_date=str(last.get("date", "")), exit_price=round(exit_price, 4),
            pnl=round((exit_price - entry_price) * (capital * position_size / entry_price), 2),
            pnl_pct=round(pnl_pct, 2),
            hold_days=len(df) - 1 - entry_idx,
            exit_reason="end_of_data",
        ))

    return trades, equity_curve


# ── Performance Stats ────────────────────────────────────────────────────────

def _compute_stats(
    trades: list[Trade],
    equity_curve: list[dict],
    initial_capital: float,
    buy_and_hold_return: float,
    period_days: int,
) -> dict:
    """Compute all performance statistics."""
    if not trades:
        return {
            "total_return_pct": 0, "buy_and_hold_pct": round(buy_and_hold_return, 2),
            "sharpe_ratio": 0, "sortino_ratio": 0, "max_drawdown_pct": 0,
            "total_trades": 0, "win_rate_pct": 0, "profit_factor": 0,
        }

    # Basic returns
    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_capital
    total_return = ((final_equity / initial_capital) - 1) * 100
    years = max(period_days / 365.25, 0.01)
    cagr = ((final_equity / initial_capital) ** (1 / years) - 1) * 100

    # Trade stats
    pnls = [t.pnl_pct for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = (len(wins) / len(pnls)) * 100 if pnls else 0

    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (999 if wins else 0)
    expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

    # Drawdown from equity curve
    equities = pd.Series([e["equity"] for e in equity_curve])
    peak = equities.expanding().max()
    drawdown = ((equities - peak) / peak) * 100
    max_dd = float(drawdown.min()) if len(drawdown) > 0 else 0

    # Max drawdown duration (bars)
    dd_duration = 0
    max_dd_dur = 0
    for i in range(len(equities)):
        if equities.iloc[i] < peak.iloc[i]:
            dd_duration += 1
            max_dd_dur = max(max_dd_dur, dd_duration)
        else:
            dd_duration = 0

    # Sharpe & Sortino (annualised from daily returns)
    daily_returns = equities.pct_change().dropna()
    if len(daily_returns) > 1 and daily_returns.std() > 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * (252 ** 0.5)
        downside = daily_returns[daily_returns < 0]
        sortino = (daily_returns.mean() / downside.std()) * (252 ** 0.5) if len(downside) > 1 and downside.std() > 0 else 0
    else:
        sharpe = sortino = 0

    hold_days = [t.hold_days for t in trades]

    return {
        "total_return_pct": round(total_return, 2),
        "buy_and_hold_pct": round(buy_and_hold_return, 2),
        "cagr_pct": round(cagr, 2),
        "sharpe_ratio": round(float(sharpe), 2),
        "sortino_ratio": round(float(sortino), 2),
        "max_drawdown_pct": round(max_dd, 2),
        "max_drawdown_days": max_dd_dur,
        "total_trades": len(trades),
        "win_rate_pct": round(win_rate, 1),
        "avg_trade_pct": round(sum(pnls) / len(pnls), 2) if pnls else 0,
        "best_trade_pct": round(max(pnls), 2) if pnls else 0,
        "worst_trade_pct": round(min(pnls), 2) if pnls else 0,
        "profit_factor": round(profit_factor, 2),
        "expectancy": round(expectancy, 2),
        "avg_hold_days": round(sum(hold_days) / len(hold_days), 1) if hold_days else 0,
    }


# ── Pattern Extraction (Phase 9 Memory Merge) ───────────────────────────────

def _extract_patterns(result: dict, conditions: list[dict]) -> dict | None:
    """Extract a learned pattern from backtest results.

    Stored so Sam can cite: "This setup historically returns +X% with Y% win rate."
    """
    if result.get("total_trades", 0) < 3:
        return None  # not enough data to learn from

    # Describe the setup in plain text
    setup_parts = []
    for c in conditions:
        ind = c.get("indicator", "?")
        op = c.get("op", "?")
        val = c.get("value", "?")
        setup_parts.append(f"{ind} {op} {val}")
    setup_desc = " AND ".join(setup_parts)

    return {
        "setup": setup_desc,
        "ticker": result.get("ticker", ""),
        "period": result.get("period", ""),
        "avg_return_pct": result.get("avg_trade_pct", 0),
        "win_rate_pct": result.get("win_rate_pct", 0),
        "total_trades": result.get("total_trades", 0),
        "sharpe_ratio": result.get("sharpe_ratio", 0),
        "profit_factor": result.get("profit_factor", 0),
        "max_drawdown_pct": result.get("max_drawdown_pct", 0),
        "extracted_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Learned Patterns Store ───────────────────────────────────────────────────

_PATTERNS_FILE = PROJECT_ROOT / "data" / "learned_patterns.json"


def _load_patterns() -> list[dict]:
    if _PATTERNS_FILE.exists():
        try:
            return json.loads(_PATTERNS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_pattern(pattern: dict) -> None:
    patterns = _load_patterns()
    patterns.append(pattern)
    # Keep max 500 patterns
    if len(patterns) > 500:
        patterns = patterns[-500:]
    _PATTERNS_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PATTERNS_FILE.write_text(json.dumps(patterns, indent=2))


# ── Strategy Persistence (JSON file) ────────────────────────────────────────

def _load_saved_strategies() -> dict[str, dict]:
    if _STRATEGIES_FILE.exists():
        try:
            return json.loads(_STRATEGIES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_strategies(strategies: dict[str, dict]) -> None:
    _STRATEGIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STRATEGIES_FILE.write_text(json.dumps(strategies, indent=2))


# ── Public Tools ─────────────────────────────────────────────────────────────

async def backtest_strategy(
    ticker: str,
    entry_conditions: list[dict] | None = None,
    exit_conditions: list[dict] | None = None,
    period: str = "1y",
    initial_capital: float = 10_000,
    position_size: float = 1.0,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    trailing_stop_pct: float | None = None,
    slippage_bps: int = 10,
    strategy_name: str | None = None,
) -> dict:
    """Backtest a strategy on historical data.

    Resolution order for strategy:
    1. If strategy_name matches a preset → use preset conditions
    2. If strategy_name matches a saved strategy → load conditions
    3. If inline entry/exit_conditions provided → use those
    """
    ticker = ticker.strip().upper()

    # Resolve strategy
    description = ""
    if strategy_name:
        # Check presets first
        if strategy_name in PRESET_STRATEGIES:
            preset = PRESET_STRATEGIES[strategy_name]
            entry_conditions = preset["entry"]
            exit_conditions = preset["exit"]
            description = preset.get("description", "")
        else:
            # Check saved strategies
            saved = _load_saved_strategies()
            if strategy_name in saved:
                s = saved[strategy_name]
                entry_conditions = s["entry_conditions"]
                exit_conditions = s["exit_conditions"]
                stop_loss_pct = stop_loss_pct or s.get("stop_loss_pct")
                take_profit_pct = take_profit_pct or s.get("take_profit_pct")
                trailing_stop_pct = trailing_stop_pct or s.get("trailing_stop_pct")
                description = s.get("description", "")
            else:
                return {"error": f"Strategy '{strategy_name}' not found. Use list_strategies to see available ones."}

    if not entry_conditions or not exit_conditions:
        return {"error": "No entry/exit conditions provided. Specify conditions or use a strategy_name."}

    # Fetch and enrich data
    records = await get_historical_data(ticker, period=period, interval="1d")
    if len(records) < 50:
        return {"error": f"Only {len(records)} bars for {ticker}. Need at least 50 for reliable backtest."}

    def _run() -> dict:
        df = pd.DataFrame(records)
        df = _compute_all_indicators(df)

        # Generate signals
        entry_signals = _evaluate_conditions(df, entry_conditions, logic="and")
        exit_signals = _evaluate_conditions(df, exit_conditions, logic="or")

        # Simulate trades
        trades, equity_curve = _simulate_trades(
            df, entry_signals, exit_signals,
            initial_capital=initial_capital,
            position_size=position_size,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
            slippage_bps=slippage_bps,
        )

        # Buy & hold benchmark
        first_close = float(df.iloc[0]["close"])
        last_close = float(df.iloc[-1]["close"])
        bnh_return = ((last_close / first_close) - 1) * 100

        # Compute performance stats
        stats = _compute_stats(
            trades, equity_curve, initial_capital, bnh_return, len(df)
        )

        strat_label = strategy_name or "custom"

        # Build result
        result = {
            "ticker": ticker,
            "strategy_name": strat_label,
            "description": description,
            "period": period,
            "bars": len(df),
            **stats,
            "trades": [asdict(t) for t in trades],
            # Downsample equity curve for JSON (max 100 points)
            "equity_curve": _downsample(equity_curve, 100),
            "entry_conditions": entry_conditions,
            "exit_conditions": exit_conditions,
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "trailing_stop_pct": trailing_stop_pct,
        }

        # Phase 7.2: Auto-generate charts
        try:
            eq_b64 = _render_equity_chart(equity_curve, ticker, strat_label, stats)
            if eq_b64:
                result["equity_chart_base64"] = eq_b64
        except Exception as e:
            logger.warning("Equity chart generation failed: %s", e)

        try:
            trade_b64 = _render_trade_chart(df, trades, ticker, strat_label)
            if trade_b64:
                result["trade_chart_base64"] = trade_b64
        except Exception as e:
            logger.warning("Trade chart generation failed: %s", e)

        # Phase 9 merge: extract and store learned pattern
        pattern = _extract_patterns(result, entry_conditions)
        if pattern:
            try:
                _save_pattern(pattern)
                result["learned_pattern"] = pattern["setup"]
            except Exception as e:
                logger.warning("Failed to save learned pattern: %s", e)

        # QuantStats tearsheet generation
        try:
            tearsheet_url = _generate_tearsheet(equity_curve, ticker, strat_label)
            if tearsheet_url:
                result["tearsheet_url"] = tearsheet_url
        except Exception as e:
            logger.warning("Tearsheet generation failed: %s", e)

        return result

    return await asyncio.to_thread(_run)


async def save_strategy(
    name: str,
    entry_conditions: list[dict],
    exit_conditions: list[dict],
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    trailing_stop_pct: float | None = None,
    description: str = "",
) -> dict:
    """Save a named strategy for future re-use."""
    name = name.strip().lower().replace(" ", "_")

    if name in PRESET_STRATEGIES:
        return {"error": f"'{name}' is a built-in preset and cannot be overwritten."}

    strategies = _load_saved_strategies()
    strategies[name] = {
        "entry_conditions": entry_conditions,
        "exit_conditions": exit_conditions,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_strategies(strategies)

    return {
        "status": "saved",
        "name": name,
        "description": description,
        "total_saved": len(strategies),
    }


async def list_strategies() -> dict:
    """List all available strategies (presets + saved)."""
    presets = []
    for name, s in PRESET_STRATEGIES.items():
        presets.append({
            "name": name,
            "type": "preset",
            "description": s.get("description", ""),
            "entry_count": len(s.get("entry", [])),
            "exit_count": len(s.get("exit", [])),
        })

    saved = _load_saved_strategies()
    user_strategies = []
    for name, s in saved.items():
        user_strategies.append({
            "name": name,
            "type": "saved",
            "description": s.get("description", ""),
            "entry_count": len(s.get("entry_conditions", [])),
            "exit_count": len(s.get("exit_conditions", [])),
            "created_at": s.get("created_at", ""),
        })

    return {
        "presets": presets,
        "saved": user_strategies,
        "total": len(presets) + len(user_strategies),
    }


async def get_learned_patterns(
    ticker: str | None = None,
    setup_keyword: str | None = None,
    min_trades: int = 3,
) -> dict:
    """Retrieve learned patterns from past backtests.

    Phase 9 merge: Sam can cite her own historical analysis.
    """
    patterns = _load_patterns()

    filtered = []
    for p in patterns:
        if p.get("total_trades", 0) < min_trades:
            continue
        if ticker and p.get("ticker", "").upper() != ticker.upper():
            continue
        if setup_keyword and setup_keyword.lower() not in p.get("setup", "").lower():
            continue
        filtered.append(p)

    # Sort by win rate descending
    filtered.sort(key=lambda x: x.get("win_rate_pct", 0), reverse=True)

    return {
        "patterns": filtered[:20],
        "total_matched": len(filtered),
        "total_stored": len(patterns),
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

def _downsample(curve: list[dict], max_points: int) -> list[dict]:
    """Downsample equity curve for JSON response size."""
    if len(curve) <= max_points:
        return curve
    step = len(curve) / max_points
    return [curve[int(i * step)] for i in range(max_points)]


# ── Phase 7.2: Backtest Chart Generation ─────────────────────────────────────

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.dates as mdates  # noqa: E402
import base64  # noqa: E402
import io  # noqa: E402

try:
    import quantstats_lumi as qs
    _HAS_QS = True
except ImportError:
    try:
        import quantstats as qs
        _HAS_QS = True
    except ImportError:
        _HAS_QS = False
        qs = None

# Match charts.py dark theme
_BG = "#0f0f0f"
_TEXT = "#cccccc"
_GRID = "#1a1a1a"
_GREEN = "#22c55e"
_RED = "#ef4444"
_CYAN = "#00d4ff"
_GOLD = "#f0b400"

CHARTS_DIR = PROJECT_ROOT / "charts"
_TEARSHEETS_DIR = PROJECT_ROOT / "public" / "tearsheets"


def _generate_tearsheet(
    equity_curve: list[dict],
    ticker: str,
    strategy_name: str,
) -> str | None:
    """Generate a QuantStats HTML tearsheet and return its public URL.

    Returns:
        Relative URL path (e.g. '/tearsheets/SPY-ema_crossover-abc123.html')
        or None if quantstats is not available or generation fails.
    """
    if not _HAS_QS or qs is None:
        logger.debug("quantstats not available, skipping tearsheet")
        return None

    if len(equity_curve) < 10:
        return None

    # Build returns Series from equity curve
    equities = pd.Series(
        [e["equity"] for e in equity_curve],
        index=pd.to_datetime([e["date"] for e in equity_curve]),
    )
    returns = equities.pct_change().dropna()

    if len(returns) < 10 or returns.std() == 0:
        return None

    # Generate file hash for deduplication
    content_hash = hashlib.md5(
        f"{ticker}-{strategy_name}-{len(equity_curve)}".encode()
    ).hexdigest()[:8]

    filename = f"{ticker}-{strategy_name}-{content_hash}.html"
    _TEARSHEETS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = _TEARSHEETS_DIR / filename

    try:
        qs.reports.html(
            returns,
            benchmark="SPY",
            title=f"{ticker} — {strategy_name}",
            output=str(filepath),
        )
        logger.info("Tearsheet saved: %s", filepath)
        return f"/tearsheets/{filename}"
    except Exception as e:
        logger.warning("quantstats report generation failed: %s", e)
        return None


def _render_equity_chart(
    equity_curve: list[dict],
    ticker: str,
    strategy_name: str,
    stats: dict,
) -> str:
    """Render equity curve + drawdown as base64 PNG."""
    if len(equity_curve) < 2:
        return ""

    dates = [e["date"] for e in equity_curve]
    equities = [e["equity"] for e in equity_curve]

    # Compute drawdown series
    eq_series = pd.Series(equities)
    peak = eq_series.expanding().max()
    dd = ((eq_series - peak) / peak) * 100

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, 7), height_ratios=[3, 1],
        facecolor=_BG, sharex=True,
    )

    # Equity curve
    ax1.set_facecolor(_BG)
    color = _GREEN if equities[-1] >= equities[0] else _RED
    ax1.plot(dates, equities, color=color, linewidth=1.5, alpha=0.9)
    ax1.fill_between(dates, equities, equities[0], alpha=0.08, color=color)
    ax1.axhline(equities[0], color=_TEXT, linewidth=0.5, alpha=0.3, linestyle="--")
    ax1.set_ylabel("Equity ($)", color=_TEXT, fontsize=10)
    ax1.tick_params(colors=_TEXT, labelsize=8)
    ax1.grid(True, color=_GRID, linestyle="--", alpha=0.5)
    ax1.set_title(
        f"{ticker} — {strategy_name}  |  "
        f"Return: {stats.get('total_return_pct', 0)}%  |  "
        f"Sharpe: {stats.get('sharpe_ratio', 0)}  |  "
        f"Win Rate: {stats.get('win_rate_pct', 0)}%",
        color=_CYAN, fontsize=11, fontweight="bold", pad=12,
    )

    # Drawdown
    ax2.set_facecolor(_BG)
    ax2.fill_between(dates, dd.values, 0, alpha=0.4, color=_RED)
    ax2.plot(dates, dd.values, color=_RED, linewidth=0.8, alpha=0.7)
    ax2.set_ylabel("Drawdown %", color=_TEXT, fontsize=10)
    ax2.set_ylim(dd.min() * 1.1 if dd.min() < 0 else -1, 0.5)
    ax2.tick_params(colors=_TEXT, labelsize=8)
    ax2.grid(True, color=_GRID, linestyle="--", alpha=0.5)

    # Thin x-axis labels
    n = len(dates)
    step = max(n // 8, 1)
    ax2.set_xticks(range(0, n, step))
    ax2.set_xticklabels([dates[i][:10] if i < n else "" for i in range(0, n, step)], rotation=30, fontsize=7)

    plt.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor=_BG)
    plt.close(fig)
    buf.seek(0)
    raw = buf.read()

    # Also save to disk
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = CHARTS_DIR / f"backtest_{ticker}_{strategy_name}.png"
    filepath.write_bytes(raw)

    return base64.b64encode(raw).decode("utf-8")


def _render_trade_chart(
    df: pd.DataFrame,
    trades: list[Trade],
    ticker: str,
    strategy_name: str,
) -> str:
    """Render candlestick chart with entry/exit markers as base64 PNG."""
    try:
        import mplfinance as mpf
    except ImportError:
        logger.warning("mplfinance not available for trade chart")
        return ""

    if len(df) < 5 or not trades:
        return ""

    # Prepare mplfinance DataFrame
    chart_df = df.copy()
    chart_df["date_dt"] = pd.to_datetime(chart_df["date"])
    chart_df.set_index("date_dt", inplace=True)
    chart_df.rename(columns={
        "open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume",
    }, inplace=True)
    for col in ("Open", "High", "Low", "Close", "Volume"):
        chart_df[col] = pd.to_numeric(chart_df[col], errors="coerce")
    chart_df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)

    # Build entry/exit marker series (NaN where no trade)
    entries_s = pd.Series(float("nan"), index=chart_df.index)
    exits_s = pd.Series(float("nan"), index=chart_df.index)

    for t in trades:
        try:
            entry_dt = pd.Timestamp(t.entry_date)
            if entry_dt in entries_s.index:
                entries_s.loc[entry_dt] = t.entry_price
        except Exception:
            pass
        try:
            exit_dt = pd.Timestamp(t.exit_date)
            if exit_dt in exits_s.index:
                exits_s.loc[exit_dt] = t.exit_price
        except Exception:
            pass

    addplots = []
    if entries_s.notna().any():
        addplots.append(mpf.make_addplot(
            entries_s, type="scatter", marker="^", markersize=80, color=_GREEN, panel=0,
        ))
    if exits_s.notna().any():
        addplots.append(mpf.make_addplot(
            exits_s, type="scatter", marker="v", markersize=80, color=_RED, panel=0,
        ))

    style = mpf.make_mpf_style(
        base_mpf_style="nightclouds",
        marketcolors=mpf.make_marketcolors(
            up=_GREEN, down=_RED,
            wick={"up": _GREEN, "down": _RED},
            edge={"up": _GREEN, "down": _RED},
            volume={"up": f"{_GREEN}80", "down": f"{_RED}80"},
        ),
        facecolor=_BG, figcolor=_BG, gridcolor=_GRID, gridstyle="--",
        y_on_right=True,
        rc={"font.size": 9, "axes.labelcolor": _TEXT, "xtick.color": "#888888", "ytick.color": "#888888"},
    )

    buf = io.BytesIO()
    plot_kwargs = {
        "type": "candle", "style": style, "volume": True,
        "figsize": (14, 8), "tight_layout": True,
        "title": f"\n{ticker} — {strategy_name} (▲ Entry  ▼ Exit)",
        "warn_too_much_data": 500,
    }
    if addplots:
        plot_kwargs["addplot"] = addplots

    mpf.plot(chart_df, **plot_kwargs, savefig=dict(fname=buf, dpi=150, bbox_inches="tight"))
    buf.seek(0)
    raw = buf.read()

    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = CHARTS_DIR / f"trades_{ticker}_{strategy_name}.png"
    filepath.write_bytes(raw)

    return base64.b64encode(raw).decode("utf-8")


# ── Phase 7.5: Sweep & Walk-Forward ─────────────────────────────────────────

async def sweep_strategy(
    tickers: list[str],
    entry_conditions: list[dict] | None = None,
    exit_conditions: list[dict] | None = None,
    period: str = "1y",
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    strategy_name: str | None = None,
    sort_by: str = "sharpe_ratio",
) -> dict:
    """Run a strategy across multiple tickers and rank results.

    Returns a leaderboard sorted by the chosen metric.
    """
    results = []
    errors = []

    for ticker in tickers[:20]:  # Cap at 20 tickers to avoid abuse
        try:
            r = await backtest_strategy(
                ticker=ticker,
                entry_conditions=entry_conditions,
                exit_conditions=exit_conditions,
                period=period,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
                strategy_name=strategy_name,
            )
            if "error" in r:
                errors.append({"ticker": ticker, "error": r["error"]})
            else:
                # Strip heavy fields for sweep summary
                summary = {
                    "ticker": r["ticker"],
                    "total_return_pct": r.get("total_return_pct", 0),
                    "buy_and_hold_pct": r.get("buy_and_hold_pct", 0),
                    "sharpe_ratio": r.get("sharpe_ratio", 0),
                    "sortino_ratio": r.get("sortino_ratio", 0),
                    "max_drawdown_pct": r.get("max_drawdown_pct", 0),
                    "win_rate_pct": r.get("win_rate_pct", 0),
                    "total_trades": r.get("total_trades", 0),
                    "profit_factor": r.get("profit_factor", 0),
                    "avg_trade_pct": r.get("avg_trade_pct", 0),
                    "avg_hold_days": r.get("avg_hold_days", 0),
                }
                results.append(summary)
        except Exception as e:
            errors.append({"ticker": ticker, "error": str(e)})

    # Sort by chosen metric
    valid_sort = sort_by if sort_by in ("sharpe_ratio", "total_return_pct", "win_rate_pct", "profit_factor", "sortino_ratio") else "sharpe_ratio"
    results.sort(key=lambda x: x.get(valid_sort, 0), reverse=True)

    return {
        "strategy": strategy_name or "custom",
        "period": period,
        "sort_by": valid_sort,
        "results": results,
        "total_tickers": len(results),
        "errors": errors,
    }


async def walk_forward_test(
    ticker: str,
    entry_conditions: list[dict] | None = None,
    exit_conditions: list[dict] | None = None,
    total_period: str = "2y",
    n_folds: int = 4,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    strategy_name: str | None = None,
) -> dict:
    """Walk-forward validation: split data into n_folds IS/OOS windows.

    Tests whether a strategy's performance is consistent across time periods,
    detecting overfitting to specific market conditions.
    """
    ticker = ticker.strip().upper()

    # Resolve strategy
    entry_conds = entry_conditions
    exit_conds = exit_conditions
    if strategy_name:
        if strategy_name in PRESET_STRATEGIES:
            preset = PRESET_STRATEGIES[strategy_name]
            entry_conds = preset["entry"]
            exit_conds = preset["exit"]
        else:
            saved = _load_saved_strategies()
            if strategy_name in saved:
                s = saved[strategy_name]
                entry_conds = s["entry_conditions"]
                exit_conds = s["exit_conditions"]
                stop_loss_pct = stop_loss_pct or s.get("stop_loss_pct")
                take_profit_pct = take_profit_pct or s.get("take_profit_pct")

    if not entry_conds or not exit_conds:
        return {"error": "No conditions provided."}

    # Fetch full period data
    records = await get_historical_data(ticker, period=total_period, interval="1d")
    if len(records) < 100:
        return {"error": f"Need ≥100 bars for walk-forward, got {len(records)}."}

    def _run_wf() -> dict:
        df = pd.DataFrame(records)
        df = _compute_all_indicators(df)
        total_bars = len(df)
        fold_size = total_bars // n_folds

        folds = []
        for fold_i in range(n_folds):
            start = fold_i * fold_size
            end = min(start + fold_size, total_bars)
            if end - start < 30:
                continue

            fold_df = df.iloc[start:end].copy().reset_index(drop=True)

            entry_signals = _evaluate_conditions(fold_df, entry_conds, logic="and")
            exit_signals = _evaluate_conditions(fold_df, exit_conds, logic="or")

            trades, equity_curve = _simulate_trades(
                fold_df, entry_signals, exit_signals,
                initial_capital=10_000,
                stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct,
            )

            first_close = float(fold_df.iloc[0]["close"])
            last_close = float(fold_df.iloc[-1]["close"])
            bnh = ((last_close / first_close) - 1) * 100

            stats = _compute_stats(trades, equity_curve, 10_000, bnh, end - start)

            fold_start_date = str(fold_df.iloc[0].get("date", ""))
            fold_end_date = str(fold_df.iloc[-1].get("date", ""))

            folds.append({
                "fold": fold_i + 1,
                "period": f"{fold_start_date[:10]} → {fold_end_date[:10]}",
                "bars": end - start,
                **stats,
            })

        # Summary stats across folds
        if folds:
            returns = [f["total_return_pct"] for f in folds]
            sharpes = [f["sharpe_ratio"] for f in folds]
            win_rates = [f["win_rate_pct"] for f in folds]
            positive_folds = sum(1 for r in returns if r > 0)

            summary = {
                "avg_return_pct": round(sum(returns) / len(returns), 2),
                "std_return_pct": round(pd.Series(returns).std(), 2) if len(returns) > 1 else 0,
                "avg_sharpe": round(sum(sharpes) / len(sharpes), 2),
                "avg_win_rate_pct": round(sum(win_rates) / len(win_rates), 1),
                "positive_folds": positive_folds,
                "total_folds": len(folds),
                "consistency_pct": round(positive_folds / len(folds) * 100, 1),
            }
        else:
            summary = {"error": "No valid folds generated."}

        return {
            "ticker": ticker,
            "strategy": strategy_name or "custom",
            "total_period": total_period,
            "n_folds": n_folds,
            "folds": folds,
            "summary": summary,
        }

    return await asyncio.to_thread(_run_wf)
