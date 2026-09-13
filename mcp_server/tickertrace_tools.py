"""TickerTrace MCP tools — daily ETF holdings intelligence, 17 tools.

Registers `core/tickertrace.py` onto a shared FastMCP instance, the same way
`registry.py` mounts the momentum tiers.

**Every tool here is `etf_`-prefixed, and that is not cosmetic.** Two of the
underlying names — `get_signals` and `get_sector_flow` — already exist on this
server as TraderMatrix-backed options-flow tools. Same words, completely
different data: `get_sector_flow` is options premium by sector, `etf_sector_flow`
is institutional fund inflows by sector. Registering both unprefixed would have
been a silent collision, and worse, a model picking between them by name would
have no way to tell which dataset it was getting. The prefix also makes
provenance obvious in a tool list, which matters when one source is free and
open and another is paid.

**These are the free tools that a competitor cannot reproduce.** The rest of the
free surface (screeners, technicals, backtests) is code over public data — real
work, but anyone can rewrite it. This is a diff of daily ETF holdings
accumulated since 2026-02, so a fork gets the client and none of the history.
See `core/tickertrace.py` for why that distinction drives the whole free/paid
line.
"""

from __future__ import annotations

from typing import Any, Literal

from mcp_server import tickertrace as tt

Category = Literal["active-equity", "option-income"]
Period = Literal["daily", "weekly", "monthly"]


def register_tickertrace_tools(mcp: Any) -> list[str]:
    """Register all 17 TickerTrace tools; return their names."""

    @mcp.tool()
    def etf_briefing() -> dict[str, Any]:
        """Pre-market institutional briefing: top ETF buys and sells, multi-provider
        convergence, accumulation streaks, notable new option positions."""
        return tt.get_briefing()

    @mcp.tool()
    def etf_signals(category: Category | None = None) -> dict[str, Any]:
        """Conviction-scored institutional buy/sell signals from daily ETF holdings
        changes. `category` restricts to 'active-equity' (ARK, Avantis, Sprott) or
        'option-income' (YieldMax, Kurv, REX, Roundhill)."""
        return tt.get_signals(category)

    @mcp.tool()
    def etf_institutional_flow(
        period: Period = "daily", limit: int = 25
    ) -> dict[str, Any]:
        """Aggregate accumulation/distribution: all active-equity funds blended into
        one AUM-weighted portfolio, showing what institutions are net buying."""
        return tt.get_institutional_flow(period, limit)

    @mcp.tool()
    def etf_institutional_trend(limit: int = 15) -> dict[str, Any]:
        """Per-ticker institutional accumulation/distribution trend across day, week
        and month horizons at once."""
        return tt.get_institutional_trend(limit)

    @mcp.tool()
    def etf_holdings_changes(
        provider: str | None = None,
        fund: str | None = None,
        direction: Literal["buying", "selling"] | None = None,
        period: Period = "daily",
        limit: int = 50,
        category: Category | None = None,
    ) -> dict[str, Any]:
        """Raw institutional position changes, filterable by provider ('ARK Invest',
        'Avantis', 'YieldMax'), fund symbol, or direction. This is the primitive the
        other signal tools are computed from."""
        return tt.get_holdings_changes(provider, fund, direction, period, limit, category)

    @mcp.tool()
    def etf_divergences(category: Category | None = None) -> list[dict[str, Any]]:
        """Cross-fund divergences: where different institutional funds traded the SAME
        ticker in OPPOSITE directions. Disagreement between managers, not consensus."""
        return tt.get_divergences(category)

    @mcp.tool()
    def etf_layering_patterns(
        window_days: int = 7, min_funds: int = 3, limit: int = 20
    ) -> dict[str, Any]:
        """Layering: 3+ independent stock-pickers opening the SAME new position within
        days of each other. Needs the full cross-fund panel over time to see at all."""
        return tt.get_layering_patterns(window_days, min_funds, limit)

    @mcp.tool()
    def etf_sector_flow(category: Category | None = None) -> dict[str, Any]:
        """Sector-level institutional inflows and outflows from fund holdings.
        NOTE: this is fund positioning — `get_sector_flow` (no prefix) is a different
        dataset, options premium by sector."""
        return tt.get_sector_flow(category)

    @mcp.tool()
    def etf_stock_activity(ticker: str) -> dict[str, Any]:
        """Everything institutional for one stock: which funds hold it, how weights
        moved, and its accumulation/distribution trend."""
        return tt.get_stock_activity(ticker)

    @mcp.tool()
    def etf_fund_detail(fund: str) -> dict[str, Any]:
        """One ETF's detail: top holdings, options count, AUM. e.g. ARKK, AVUV, NVDY."""
        return tt.get_fund_detail(fund)

    @mcp.tool()
    def etf_list_funds(category: Category | None = None) -> dict[str, Any]:
        """All 71 tracked funds with holdings counts and top positions."""
        return tt.list_all_funds(category)

    @mcp.tool()
    def etf_list_tickers(
        limit: int = 100,
        sort: Literal["funds", "weight"] = "funds",
        category: Category | None = None,
    ) -> dict[str, Any]:
        """The most widely-held underlyings across all tracked funds (~2,270 total)."""
        return tt.list_all_tickers(limit, sort, category)

    @mcp.tool()
    def etf_income_overview() -> dict[str, Any]:
        """Option-income funds by structure: covered-call, synthetic, leap-proxy, swap."""
        return tt.get_income_overview()

    @mcp.tool()
    def etf_income_fund_detail(fund: str) -> dict[str, Any]:
        """One income fund's full book: call coverage, moneyness, options overlay."""
        return tt.get_income_fund_detail(fund)

    @mcp.tool()
    def etf_options_listings() -> dict[str, Any]:
        """CBOE daily diff: newly optionable stocks and new weekly listings."""
        return tt.get_options_listings()

    @mcp.tool()
    def etf_signal_performance() -> dict[str, Any]:
        """Historical backtest performance of TickerTrace conviction signals.

        The published track record — the one asset here a fork cannot obtain,
        because forking gets the code from today forward, not the history that
        makes the score mean anything."""
        return tt.get_signal_performance()

    @mcp.tool()
    def etf_global_stats() -> dict[str, Any]:
        """Coverage stats: funds tracked, unique underlyings, options contracts,
        and today's new positions and exits."""
        return tt.get_global_stats()

    return [
        "etf_briefing", "etf_signals", "etf_institutional_flow",
        "etf_institutional_trend", "etf_holdings_changes", "etf_divergences",
        "etf_layering_patterns", "etf_sector_flow", "etf_stock_activity",
        "etf_fund_detail", "etf_list_funds", "etf_list_tickers",
        "etf_income_overview", "etf_income_fund_detail", "etf_options_listings",
        "etf_signal_performance", "etf_global_stats",
    ]
