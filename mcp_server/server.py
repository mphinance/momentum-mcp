"""
momentum-mcp: MCP server exposing all trading tools.

Registers ALL quantitative trading tools with FastMCP and exposes them
for AI agent consumption via the Model Context Protocol (SSE transport).

Can be used standalone (`python -m mcp_server.server`) or mounted on
the FastAPI brain via `app.mount("/mcp", mcp.sse_app())`.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

# ── Tool imports ──────────────────────────────────────────────────────────────
from mcp_server.screener import run_stock_screen as _run_stock_screen
from mcp_server.screener import run_custom_screen as _run_custom_screen
from mcp_server.data import get_historical_data as _get_historical_data
from mcp_server.technicals import analyze_technicals as _analyze_technicals
from mcp_server.charts import generate_chart as _generate_chart
from mcp_server.news import (
    fetch_ticker_news as _fetch_ticker_news,
    extract_article_text as _extract_article_text,
)
from mcp_server.options import (
    analyze_options_setup as _analyze_options_setup,
    find_best_to_sell as _find_best_to_sell,
    find_best_to_buy as _find_best_to_buy,
    sweep_setups as _sweep_setups,
)
from mcp_server.tv_analysis import get_tv_analysis as _get_tv_analysis
from mcp_server.fundamentals import get_fundamentals as _get_fundamentals
from mcp_server.knowledge import search_knowledge as _search_knowledge
from mcp_server.conviction import (
    log_conviction as _log_conviction,
    get_track_record as _get_track_record,
)
from mcp_server.backtest import (
    backtest_strategy as _backtest_strategy,
    save_strategy as _save_strategy,
    list_strategies as _list_strategies,
    get_learned_patterns as _get_learned_patterns,
    sweep_strategy as _sweep_strategy,
    walk_forward_test as _walk_forward_test,
)
from mcp_server.traderdaddy import (
    get_market_pulse as _get_market_pulse,
    get_unusual_activity as _get_unusual_activity,
    get_sector_flow as _get_sector_flow,
    get_signals as _get_signals,
    get_gex_overview as _get_gex_overview,
    get_earnings_calendar as _get_earnings_calendar,
    get_put_call_ratios as _get_put_call_ratios,
    get_market_stats as _get_market_stats,
    get_politician_trades as _get_politician_trades,
    get_earnings_flow as _get_earnings_flow,
)
from mcp_server.position_sizer import calculate_position_size as _calculate_position_size
from mcp_server.vcp_screener import screen_vcp as _screen_vcp
from mcp_server.market_top import detect_market_top as _detect_market_top
from mcp_server.ftd_detector import detect_ftd as _detect_ftd
from mcp_server.pead_screener import screen_pead as _screen_pead
from mcp_server.pair_trade import analyze_pair as _analyze_pair
from mcp_server.canslim_screener import screen_canslim as _screen_canslim
from mcp_server.scenario import analyze_scenario as _analyze_scenario, model_price_distribution as _model_price_distribution
from mcp_server.exposure import get_exposure_recommendation as _get_exposure_recommendation
from mcp_server.environment import get_market_environment as _get_market_environment
from mcp_server.macro_regime import detect_macro_regime as _detect_macro_regime
from mcp_server.breadth import analyze_breadth as _analyze_breadth
from mcp_server.uptrend import analyze_uptrend_participation as _analyze_uptrend_participation
from mcp_server.themes import detect_themes as _detect_themes
from mcp_server.earnings_analyzer import analyze_recent_gap as _analyze_recent_gap
from mcp_server.bubble import detect_bubble_risk as _detect_bubble_risk

from mcp_server.alpha_cards import generate_alpha_card as _generate_alpha_card
from mcp_server.warmer import get_alpha_signals as _get_alpha_signals, WARM_TICKERS

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── FastMCP server instance ──────────────────────────────────────────────────
mcp = FastMCP(
    "momentum",
    instructions=(
        "Welcome to the Momentum MCP Server — powered by TraderDaddy Pro.\n\n"
        "This server provides 33 quantitative trading tools for AI agents:\n"
        "• Stock screening (22 presets + custom filters)\n"
        "• Technical analysis (24 indicators: EMA stack, RSI, MACD, ADX, ATR, Bollinger, etc.)\n"
        "• Options analysis via VoPR™ engine (vol surface, Black-Scholes, A-F grading)\n"
        "• Auto-find best options to sell/buy/straddle\n"
        "• Institutional flow data, GEX, sector rotation, politician trades\n"
        "• Backtesting suite (6 presets, walk-forward validation, multi-ticker sweep)\n"
        "• 139-book trading knowledge base (RAG search)\n\n"
        "Data sources: yfinance, TradingView, TraderDaddy Pro REST API, ChromaDB.\n"
        "Rate limited to 30 requests/minute per IP. Results are cached with "
        "market-hours-aware TTL (shorter during market open for freshness).\n\n"
        "Learn more at https://traderdaddy.pro"
    ),
)


# ── Welcome Resource ──────────────────────────────────────────────────────────

_WELCOME_TEXT = """# 🐂 Momentum × TraderDaddy Pro

**Institutional-grade trading intelligence, exposed as MCP tools.**

## What You Can Do

| Category | Tools | Examples |
|:---|:---|:---|
| **Screening** | `run_stock_screen`, `run_custom_screen` | "Find oversold large-caps", "Gap-up stocks today" |
| **Technicals** | `analyze_technicals`, `get_tv_analysis` | 24 indicators + TradingView 26-indicator consensus |
| **Options** | `analyze_options_setup`, `find_best_to_sell`, `find_best_to_buy` | VoPR™ grading, auto-scanner, budget-aware |
| **Flow Data** | `get_unusual_activity`, `get_ticker_flow`, `get_flow_summary` | Institutional trades, conviction scoring |
| **Market Intel** | `get_market_pulse`, `get_gex_overview`, `get_sector_flow` | Sentiment, gamma exposure, sector rotation |
| **Backtesting** | `backtest_strategy`, `sweep_strategy`, `walk_forward_test` | 6 presets, multi-ticker, overfitting detection |
| **Knowledge** | `search_knowledge` | 139 trading books + Options Field Manual |

## Quick Start

1. Call `get_market_pulse` for the current market mood
2. Use `run_stock_screen` with a preset to find candidates
3. Run `analyze_technicals` on interesting tickers
4. Use `find_best_to_sell` or `find_best_to_buy` for options plays

## Rate Limits

- **30 requests / 60 seconds** per IP (configurable via `MCP_RATE_LIMIT` env var)
- Results are cached with market-hours-aware TTL
- During market hours: 2-5 min cache (fresh data)
- After hours / weekends: 30-60 min cache (data is static)

---

*Powered by [TraderDaddy Pro](https://traderdaddy.pro) — Where smart money meets smart tools.*
"""


@mcp.resource("traderdaddy://welcome")
def get_welcome() -> str:
    """Welcome message and overview of the Momentum trading tools platform."""
    return _WELCOME_TEXT


@mcp.prompt()
def trading_assistant() -> str:
    """Start a trading analysis session with access to institutional-grade tools."""
    return (
        "You are a quantitative trading analyst with access to the Momentum MCP server "
        "powered by TraderDaddy Pro. You have 33 tools for stock screening, technical analysis, "
        "options analysis (VoPR™ engine), institutional flow data, backtesting, and a "
        "139-book trading knowledge base.\n\n"
        "Start by understanding what the user wants to analyze, then use the appropriate tools. "
        "Combine multiple data sources for high-conviction insights. "
        "Key workflow: screen → technicals → options → flow confirmation → trade thesis.\n\n"
        "Always cite specific data points (RSI values, flow scores, VoPR grades) in your analysis."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SCREENERS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def run_stock_screen(
    preset: str = "most_active",
    limit: int = 25,
) -> dict[str, Any]:
    """Run a stock screen using TradingView's scanner.

    22 presets: most_active, new_highs, new_lows, overbought, oversold,
    high_relative_volume, gap_up, gap_down, bullish_ema_stack, bearish_ema_stack,
    high_momentum, large_cap_undervalued, top_gainers, biggest_losers,
    most_volatile, pre_market_gainers, pre_market_losers, pre_market_active,
    pre_market_gappers, after_hours_gainers, after_hours_losers, after_hours_active.
    """
    return await _run_stock_screen(preset=preset, limit=limit)


@mcp.tool()
async def run_custom_screen(
    filters: list[dict[str, Any]],
    sort_by: str = "volume",
    sort_ascending: bool = False,
    limit: int = 25,
) -> dict[str, Any]:
    """Build a custom stock screen with dynamic filter conditions.

    Each filter is {field, operator, value}. Operators: >, >=, <, <=, ==, !=.
    Fields: RSI, ADX, ATR, EMA5-200, SMA5-200, MACD.macd, MACD.signal,
    BB.upper, BB.lower, Stoch.K, Stoch.D, CCI20, W.R, volume, close,
    change, gap, market_cap_basic, Aroon.Up, Aroon.Down, VWAP, MoneyFlow.
    """
    return await _run_custom_screen(filters=filters, sort_by=sort_by, sort_ascending=sort_ascending, limit=limit)


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET DATA
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_historical_data(
    ticker: str,
    period: str = "3mo",
    interval: str = "1d",
) -> list[dict[str, Any]]:
    """Fetch OHLCV historical price data for a stock.

    Args:
        ticker: Stock symbol (e.g. "AAPL").
        period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max.
        interval: 1m, 5m, 15m, 30m, 1h, 1d, 1wk, 1mo.
    """
    return await _get_historical_data(ticker=ticker, period=period, interval=interval)


# ═══════════════════════════════════════════════════════════════════════════════
# TECHNICALS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def analyze_technicals(ticker: str, period: str = "1y") -> dict[str, Any]:
    """Compute 24 technical indicators: EMA 8/21/34/55/89, SMA 50/100/200,
    RSI(14), MACD(12,26,9), ADX(14), ATR(14), Williams %R, Stochastic,
    Bollinger Bands, CCI(20). Returns latest readings + analysis summary."""
    res = await _analyze_technicals(ticker=ticker, period=period)
    return res.dict()


@mcp.tool()
async def get_tv_analysis(
    ticker: str,
    interval: str = "1d",
    exchange: str = "NASDAQ",
    screener: str | None = None,
) -> dict[str, Any]:
    """Get TradingView 26-indicator technical consensus for a ticker.

    Returns STRONG_BUY/BUY/NEUTRAL/SELL/STRONG_SELL with buy/sell/neutral
    counts for both oscillators and moving averages.

    **Screener selection** — TradingView partitions symbols by asset class.
    Pick the right ``screener`` for the instrument or leave it ``None`` to
    auto-detect from the symbol shape:

    | Asset class            | Example symbols                | screener |
    |------------------------|--------------------------------|----------|
    | US equities            | NVDA, AAPL, TSLA               | america  |
    | Forex / commodity CFDs | XAUUSD, EURUSD, GBPUSD, USDJPY | cfd      |
    | Crypto                 | BTCUSD, ETHUSD, SOLUSD         | crypto   |
    | Futures (yfinance)     | GC=F, CL=F, ES=F               | america  |
    | Futures (TradingView)  | GC1!, ES1!, CL1!               | cfd      |

    Args:
        ticker: Symbol as shown above.
        interval: 1m, 5m, 15m, 1h, 4h, 1d (default), 1w, 1M.
        exchange: Exchange within the screener. Defaults to NASDAQ.
            Ignored when ``screener`` is set explicitly.
        screener: ``"america"``, ``"cfd"``, or ``"crypto"``. ``None``
            (default) auto-detects from the symbol.
    """
    return await _get_tv_analysis(
        ticker=ticker, interval=interval, exchange=exchange, screener=screener
    )


# ═══════════════════════════════════════════════════════════════════════════════
# CHARTS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def generate_chart(
    ticker: str, period: str = "6mo", interval: str = "1d",
    style: str = "dark", show_emas: bool = True,
) -> dict[str, Any]:
    """Generate a candlestick chart with EMA overlays (8/21/34/55/89).
    Returns base64-encoded PNG and file path."""
    return await _generate_chart(
        ticker=ticker, period=period, interval=interval,
        style=style, show_emas=show_emas,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIONS (VoPR™ Engine)
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def analyze_options_setup(
    ticker: str, option_type: str = "put", dte: int = 30,
    budget: float | None = None, contracts: int = 1,
    iv_override: float | None = None,
) -> dict[str, Any]:
    """VoPR™ engine: composite realized vol (4 estimators), VRP ratio,
    Black-Scholes Delta/Theta, A-F grade. Use when you need a specific
    DTE/strike analysis. Pass budget for strike recommendations."""
    res = await _analyze_options_setup(
        ticker=ticker, option_type=option_type, dte=dte,
        budget=budget, contracts=contracts, iv_override=iv_override,
    )
    return res.dict()


@mcp.tool()
async def find_best_to_sell(ticker: str, budget: float | None = None) -> dict[str, Any]:
    """Auto-find the best puts and calls to SELL. Scans 7-45 DTE across
    multiple strikes. Scores on RoC, VoPR grade, theta efficiency, delta
    sweet spot. Returns top 3 puts + top 3 calls."""
    res = await _find_best_to_sell(ticker=ticker, budget=budget)
    return res.dict()


@mcp.tool()
async def find_best_to_buy(ticker: str, budget: float | None = None) -> dict[str, Any]:
    """Auto-find the best directional option to BUY. Reads technicals
    (RSI, EMA stack, MACD) to determine bullish/bearish bias, then scans
    21-60 DTE for optimal contract. Returns top 3 with direction rationale."""
    res = await _find_best_to_buy(ticker=ticker, budget=budget)
    return res.dict()


@mcp.tool()
async def sweep_setups(
    tickers: list[str] | None = None, budget: float | None = None,
    max_tickers: int = 10,
) -> dict[str, Any]:
    """Opportunity Board: scan multiple tickers for best options trades.
    If no tickers given, auto-discovers from the most-active screener.
    Runs sell + buy scanners on each in parallel. Returns ranked board."""
    res = await _sweep_setups(tickers=tickers, budget=budget, max_tickers=max_tickers)
    return res.dict()
# ═══════════════════════════════════════════════════════════════════════════════
# FUNDAMENTALS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_fundamentals(ticker: str) -> dict[str, Any]:
    """Get fundamental data: P/E, EPS, revenue growth, profit margin,
    short interest, analyst targets, earnings dates, market cap."""
    return await _get_fundamentals(ticker=ticker)


# ═══════════════════════════════════════════════════════════════════════════════
# NEWS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def fetch_ticker_news(ticker: str, limit: int = 10) -> list[dict[str, Any]]:
    """Fetch recent financial news headlines for a stock from RSS feeds."""
    return await _fetch_ticker_news(ticker=ticker, limit=limit)


@mcp.tool()
async def extract_article_text(url: str) -> dict[str, Any]:
    """Extract the full-text body of a news article. Strips ads and nav."""
    return await _extract_article_text(url=url)


# ═══════════════════════════════════════════════════════════════════════════════
# ALPHA CARDS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def generate_alpha_card(ticker: str, sam_take: str = "") -> dict[str, Any]:
    """Generate a shareable Alpha Card — a branded HTML analysis card.
    Combines technicals + TradingView consensus into a sleek visual.
    Perfect for sharing trade setups on Discord/Twitter."""
    technicals = None
    tv_data = None
    try:
        technicals = await _analyze_technicals(ticker=ticker)
    except Exception:
        pass
    try:
        tv_data = await _get_tv_analysis(ticker=ticker)
    except Exception:
        pass

    html = _generate_alpha_card(
        ticker=ticker,
        technicals=technicals,
        tv_analysis=tv_data,
        sam_take=sam_take,
    )
    return {"ticker": ticker, "html_length": len(html), "html": html[:500] + "..."}


# ═══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE BASE (RAG — 139 trading books)
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def search_knowledge(query: str, n_results: int = 5) -> dict[str, Any]:
    """Search Sam's library of 139 trading books + methodology guides.
    Returns relevant passages with source citations."""
    return await _search_knowledge(query=query, n_results=n_results)


# ═══════════════════════════════════════════════════════════════════════════════
# CONVICTION JOURNAL
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def log_conviction(
    ticker: str, direction: str, conviction: str,
    thesis: str, entry_price: float | None = None,
) -> dict[str, Any]:
    """Log a trade conviction to the journal. Direction: long/short.
    Conviction: high/medium/low. Include a thesis for future review."""
    return await _log_conviction(
        ticker=ticker, direction=direction, conviction=conviction,
        thesis=thesis, entry_price=entry_price,
    )


@mcp.tool()
async def get_track_record() -> dict[str, Any]:
    """Get the full conviction journal track record with win/loss stats."""
    return await _get_track_record()


# ═══════════════════════════════════════════════════════════════════════════════
# BACKTESTING
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def backtest_strategy(
    ticker: str, strategy: str = "ema_crossover",
    period: str = "2y", initial_capital: float = 10000,
    stop_loss_pct: float | None = None, take_profit_pct: float | None = None,
) -> dict[str, Any]:
    """Backtest a trading strategy on historical data. 6 presets:
    ema_crossover, rsi_bounce, macd_momentum, bollinger_squeeze,
    golden_cross, ema_stack_breakout. Returns Sharpe, win rate, CAGR, etc."""
    return await _backtest_strategy(
        ticker=ticker, strategy=strategy, period=period,
        initial_capital=initial_capital,
        stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
    )


@mcp.tool()
async def save_strategy(name: str, conditions: dict[str, Any]) -> dict[str, Any]:
    """Save a custom strategy to disk for re-use."""
    return await _save_strategy(name=name, conditions=conditions)


@mcp.tool()
async def list_strategies() -> dict[str, Any]:
    """List all saved custom strategies."""
    return await _list_strategies()


@mcp.tool()
async def get_learned_patterns() -> dict[str, Any]:
    """Get auto-extracted patterns from past backtests with win rates."""
    return await _get_learned_patterns()


@mcp.tool()
async def sweep_strategy(
    tickers: list[str], strategy: str = "ema_crossover",
    period: str = "2y",
) -> dict[str, Any]:
    """Run a strategy across multiple tickers (max 20). Ranks by Sharpe/return."""
    return await _sweep_strategy(tickers=tickers, strategy=strategy, period=period)


@mcp.tool()
async def walk_forward_test(
    ticker: str, strategy: str = "ema_crossover",
    n_folds: int = 5, period: str = "3y",
) -> dict[str, Any]:
    """Walk-forward validation: splits data into n folds, detects overfitting."""
    return await _walk_forward_test(
        ticker=ticker, strategy=strategy, n_folds=n_folds, period=period,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TRADERDADDY PRO (Live Institutional Flow)
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_market_pulse() -> dict[str, Any]:
    """AI-generated market sentiment with options flow score (-7 to +7).
    +5 to +7 = extremely bullish. -5 to -7 = panic-level bearish."""
    res = await _get_market_pulse()
    return res.dict()


@mcp.tool()
async def get_market_stats() -> dict[str, Any]:
    """Market-wide put/call ratios and sentiment indicators."""
    res = await _get_market_stats()
    return res.dict()


@mcp.tool()
async def get_put_call_ratios(ticker: str = "SPY") -> dict[str, Any]:
    """Put/call ratios for SPY, QQQ, IWM (or any ticker).
    Below 0.7 = complacent. Above 1.0 = elevated fear (contrarian bullish)."""
    res = await _get_put_call_ratios(ticker=ticker)
    return res.dict()


@mcp.tool()
async def get_sector_flow() -> dict[str, Any]:
    """Sector-by-sector options flow with bullish/bearish sentiment.
    Compare flows: all red = real selling, mixed = rotation."""
    res = await _get_sector_flow()
    return res.dict()


@mcp.tool()
async def get_unusual_activity() -> dict[str, Any]:
    """Unusual options flow feed — institutional trades, premium, conviction.
    High conviction = $500K+ premium, unusual volume vs OI."""
    res = await _get_unusual_activity()
    return res.dict()


@mcp.tool()
async def get_signals() -> dict[str, Any]:
    """Breakout and continuation signals with technical indicator data."""
    res = await _get_signals()
    return res.dict()


@mcp.tool()
async def get_gex_overview() -> dict[str, Any]:
    """Gamma Exposure (GEX) for SPY/QQQ/IWM. Positive = pinning/calm.
    Negative = trending/volatile. GEX flip level = regime boundary."""
    res = await _get_gex_overview()
    return res.dict()


@mcp.tool()
async def get_earnings_calendar() -> dict[str, Any]:
    """Weekly earnings calendar — who reports this week."""
    res = await _get_earnings_calendar()
    return res.dict()


@mcp.tool()
async def get_earnings_flow() -> dict[str, Any]:
    """Pre-earnings options flow — institutional positioning ahead of earnings."""
    res = await _get_earnings_flow()
    return res.dict()


@mcp.tool()
async def get_politician_trades() -> dict[str, Any]:
    """Congressional stock trading disclosures."""
    res = await _get_politician_trades()
    return res.dict()
# ═══════════════════════════════════════════════════════════════════════════════
# ALPHA STREAM (Proactive Signal Detection)
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
async def get_alpha_signals(
    ticker: str | None = None,
    signal_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get recent alpha signals detected by the background signal factory.

    9 signal types: RSI_CROSS_UP_70, RSI_CROSS_DOWN_70, RSI_CROSS_UP_30,
    RSI_CROSS_DOWN_30, MACD_CROSS_UP, MACD_CROSS_DOWN, VOLUME_SPIKE,
    EMA_STACK_BREAKOUT, ADX_TREND_ENTRY.

    Signals are auto-detected every 5 min (market open) / 30 min (closed)
    across 24 popular tickers. Filter by ticker and/or signal type.
    """
    return await _get_alpha_signals(ticker=ticker, signal_type=signal_type, limit=limit)


@mcp.tool()
async def calculate_position_size(
    ticker: str,
    account_size: float,
    risk_pct: float = 1.0,
    entry_price: float | None = None,
    stop_price: float | None = None,
    method: str = "fixed_fractional",
) -> dict[str, Any]:
    """Calculate risk-based position size using Fixed Fractional, ATR, or Kelly methods.
    Answers 'how many shares/contracts should I buy?' given account size and risk tolerance."""
    res = await _calculate_position_size(
        ticker=ticker,
        account_size=account_size,
        risk_pct=risk_pct,
        entry_price=entry_price,
        stop_price=stop_price,
        method=method,
    )
    return res.dict()


@mcp.tool()
async def screen_vcp(
    tickers: list[str] | None = None,
    max_tickers: int = 50,
) -> dict[str, Any]:
    """Screen for stocks forming a Volatility Contraction Pattern (VCP).
    Identifies Stage 2 uptrends forming tight bases near breakout points."""
    res = await _screen_vcp(tickers=tickers, max_tickers=max_tickers)
    return res.dict()


@mcp.tool()
async def detect_market_top() -> dict[str, Any]:
    """Detect market topping signals using distribution days and leadership trends.
    Uses O'Neil distribution day counting and defensive sector rotation analysis."""
    res = await _detect_market_top()
    return res.dict()


@mcp.tool()
async def detect_ftd() -> dict[str, Any]:
    """Detect Follow-Through Days (FTDs) on major indices to confirm market bottoms.
    Identifies the start of a new bull market using O'Neil price/volume expansion rules."""
    res = await _detect_ftd()
    return res.dict()


@mcp.tool()
async def screen_pead(
    lookback_days: int = 10,
) -> dict[str, Any]:
    """Screen for Post-Earnings Announcement Drift (PEAD) setups.
    Identifies stocks that gapped up on earnings and are now pulling back to EMA10/20."""
    res = await _screen_pead(lookback_days=lookback_days)
    return res.dict()


@mcp.tool()
async def analyze_pair(
    ticker_a: str,
    ticker_b: str,
    lookback: int = 60,
) -> dict[str, Any]:
    """Analyze a pair of stocks for statistical arbitrage.
    Computes correlation, ratio, and Z-score of the spread between two assets."""
    res = await _analyze_pair(ticker_a=ticker_a, ticker_b=ticker_b, lookback=lookback)
    return res.dict()


@mcp.tool()
async def screen_canslim(
    tickers: list[str] | None = None,
    max_tickers: int = 30,
) -> dict[str, Any]:
    """Screen for growth stocks matching CANSLIM criteria.
    Identifies high-growth leaders near 52-week highs with strong institutional sponsorship."""
    res = await _screen_canslim(tickers=tickers, max_tickers=max_tickers)
    return res.dict()


@mcp.tool()
async def analyze_scenario(
    ticker: str,
    catalyst: str,
    timeframe: str = "30d",
) -> dict[str, Any]:
    """Generate bull/base/bear scenarios for a ticker based on a catalyst (e.g. 'earnings', 'tariffs').
    Provides price targets and probabilities based on technical volatility and trend."""
    res = await _analyze_scenario(ticker=ticker, catalyst=catalyst, timeframe=timeframe)
    return res.dict()


@mcp.tool()
async def model_price_distribution(
    ticker: str,
    days_forward: int = 30,
) -> dict[str, Any]:
    """Compute statistical price targets using historical volatility.
    Returns confidence intervals (68%, 95%, 99%) for where the price is likely to be."""
    res = await _model_price_distribution(ticker=ticker, days_forward=days_forward)
    return res.dict()


@mcp.tool()
async def get_exposure_recommendation() -> dict[str, Any]:
    """Get a market exposure recommendation (0-100% capital deployment).
    Synthesizes VIX, flow, distribution days, and trend confirmation into a single capital deployment ceiling."""
    res = await _get_exposure_recommendation()
    return res.dict()


@mcp.tool()
async def get_market_environment() -> dict[str, Any]:
    """Get a cross-asset market environment report.
    Snapshots performance for Equities, Bonds, Commodities, Currencies, and Crypto to identify macro rotation."""
    res = await _get_market_environment()
    return res.dict()


@mcp.tool()
async def detect_macro_regime(lookback: int = 90) -> dict[str, Any]:
    """Detect the current structural market regime (Growth, Inflation, Deflation, or Goldilocks).
    Uses cross-asset ratios like RSP/SPY, TLT/SHY, and XLY/XLP to identify macro shifts."""
    res = await _detect_macro_regime(lookback=lookback)
    return res.dict()


@mcp.tool()
async def analyze_breadth() -> dict[str, Any]:
    """Get a comprehensive market breadth health score (0-100).
    Synthesizes equal-weight vs cap-weight trends, new highs/lows, and volatility term structure."""
    res = await _analyze_breadth()
    return res.dict()


@mcp.tool()
async def analyze_uptrend_participation() -> dict[str, Any]:
    """Measure market participation in structural uptrends.
    Scans all 11 SPDR sectors and major indices to see what % are trading above their EMA50 and EMA200."""
    res = await _analyze_uptrend_participation()
    return res.dict()


@mcp.tool()
async def detect_themes(lookback: int = 20) -> dict[str, Any]:
    """Identify trending market themes by clustering thematic ETF performance.
    Detects which groups (AI, Biotech, Energy, etc.) are moving together with the most momentum."""
    res = await _detect_themes(lookback=lookback)
    return res.dict()


@mcp.tool()
async def analyze_recent_gap(ticker: str) -> dict[str, Any]:
    """Score the most recent overnight gap reaction (0-100) for a specific ticker.
    Evaluates gap size, volume expansion, price hold, and fundamental quality to grade the trade setup."""
    res = await _analyze_recent_gap(ticker=ticker)
    return res.dict()


@mcp.tool()
async def detect_bubble_risk() -> dict[str, Any]:
    """Assess current market euphoria and bubble risk (0-15 score).
    Evaluates price extension from 200d MA, VIX complacency, valuation (PE), speculative volume, and meme stock fever."""
    res = await _detect_bubble_risk()
    return res.dict()


@mcp.tool()
async def get_momentum_pulse(
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Calculate real-time momentum scores (0-100) for each ticker.

    Uses EMA stack alignment, RSI sweet-spot, and ADX strength.
    Score >70 = strong bullish momentum, <30 = momentum exhausted.
    Defaults to the 24 pre-warmed tickers if none specified.
    """
    target_tickers = tickers or WARM_TICKERS
    results = []

    for t in target_tickers[:30]:  # cap at 30
        try:
            tech = await _analyze_technicals(ticker=t)
            d = tech.data if hasattr(tech, 'data') and isinstance(tech.data, dict) else tech if isinstance(tech, dict) else None
            if not d:
                continue

            score = 0.0
            max_score = 43.0

            # ADX strength (max 18 pts)
            adx = d.get("adx_14")
            if adx is not None:
                if adx >= 40: score += 18
                elif adx >= 30: score += 14
                elif adx >= 25: score += 10
                elif adx >= 20: score += 5

            # RSI sweet spot (max 15 pts) — bullish: 45-65 is ideal
            rsi = d.get("rsi_14")
            if rsi is not None:
                if 50 <= rsi <= 60: score += 15     # perfect
                elif 45 <= rsi < 50: score += 12
                elif 60 < rsi <= 65: score += 10
                elif 65 < rsi <= 70: score += 5     # getting overbought
                elif 40 <= rsi < 45: score += 5     # still decent
                elif rsi > 70: score -= 5           # overbought penalty
                elif rsi < 30: score -= 10          # oversold penalty

            # EMA alignment (max 10 pts)
            stack_bullish = d.get("ema_stack_bullish")
            if stack_bullish is True:
                score += 10
            elif stack_bullish is False:
                # Check bearish alignment (inverse)
                ema_vals = [d.get(f"ema_{l}") for l in [8, 21, 34, 55, 89]]
                if all(v is not None for v in ema_vals):
                    if all(ema_vals[i] < ema_vals[i+1] for i in range(len(ema_vals)-1)):
                        score -= 10  # full bearish stack

            # Normalize to 0-100
            normalized = ((score + max_score) / (2 * max_score)) * 100
            normalized = max(0, min(100, normalized))

            # Momentum label
            if normalized >= 70: label = "🟢 STRONG"
            elif normalized >= 55: label = "🟡 MODERATE"
            elif normalized >= 40: label = "⚪ NEUTRAL"
            elif normalized >= 25: label = "🟠 WEAK"
            else: label = "🔴 EXHAUSTED"

            results.append({
                "ticker": t,
                "pulse_score": round(normalized, 1),
                "label": label,
                "rsi_14": rsi,
                "adx_14": adx,
                "ema_stack_bullish": stack_bullish,
                "close": d.get("close"),
            })
        except Exception:
            continue

    # Sort by momentum score descending
    results.sort(key=lambda x: x["pulse_score"], reverse=True)

    return {
        "pulse": results,
        "count": len(results),
        "strongest": results[0]["ticker"] if results else None,
        "weakest": results[-1]["ticker"] if results else None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Entry point (standalone mode)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import os
    transport = os.getenv("MCP_TRANSPORT", "sse")
    host = os.getenv("MCP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_PORT", "8401"))
    sse_path = os.getenv("MCP_SSE_PATH", "/mcp/sse")
    logger.info(
        "Starting momentum MCP server on %s:%s%s (transport=%s, 35 tools registered)...",
        host, port, sse_path, transport,
    )
    if transport == "sse":
        import asyncio
        # Configure via FastMCP settings
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.settings.sse_path = sse_path
        # Allow the public reverse-proxy host so Apache forwarding doesn't get 421
        mcp.settings.transport_security.allowed_hosts.extend([
            "ghost.mphinance.com",
            "ghost.mphinance.com:443",
        ])
        asyncio.run(mcp.run_sse_async())
    else:
        mcp.run()
