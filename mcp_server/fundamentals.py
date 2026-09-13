"""
fundamentals.py — Fundamental data via yfinance

Returns ~40 data points: P/E, forward P/E, EPS, revenue, revenue growth,
profit margins, earnings dates, short interest, institutional ownership,
analyst targets, dividend yield, beta, sector/industry, etc.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

from mcp_server.cache import smart_cache


@smart_cache(open_ttl=3600, closed_ttl=86400)  # Fundamentals change slowly — 1hr/24hr
async def get_fundamentals(ticker: str) -> dict[str, Any]:
    """Fetch fundamental data for a ticker via yfinance."""
    import yfinance as yf

    ticker = ticker.strip().upper()

    try:
        stock = yf.Ticker(ticker)
        info = stock.info or {}
    except Exception as e:
        logger.error("Failed to fetch fundamentals for %s: %s", ticker, e)
        return {"ticker": ticker, "error": str(e)}

    def _g(key: str, default=None):
        return info.get(key, default)

    def _fmt_large(val):
        """Format large numbers (revenue, market cap) to human-readable."""
        if val is None:
            return None
        if abs(val) >= 1e12:
            return f"${val / 1e12:.2f}T"
        if abs(val) >= 1e9:
            return f"${val / 1e9:.2f}B"
        if abs(val) >= 1e6:
            return f"${val / 1e6:.1f}M"
        return f"${val:,.0f}"

    result = {
        "ticker": ticker.upper(),
        "name": _g("longName") or _g("shortName"),
        "sector": _g("sector"),
        "industry": _g("industry"),
        "price": _g("currentPrice") or _g("regularMarketPrice"),
        "market_cap": _g("marketCap"),
        "market_cap_fmt": _fmt_large(_g("marketCap")),

        # Valuation
        "pe_trailing": _g("trailingPE"),
        "pe_forward": _g("forwardPE"),
        "peg_ratio": _g("pegRatio"),
        "price_to_book": _g("priceToBook"),
        "price_to_sales": _g("priceToSalesTrailing12Months"),
        "enterprise_value": _g("enterpriseValue"),
        "ev_to_ebitda": _g("enterpriseToEbitda"),
        "ev_to_revenue": _g("enterpriseToRevenue"),

        # Earnings & Revenue
        "eps_trailing": _g("trailingEps"),
        "eps_forward": _g("forwardEps"),
        "revenue": _g("totalRevenue"),
        "revenue_fmt": _fmt_large(_g("totalRevenue")),
        "revenue_growth": _g("revenueGrowth"),
        "earnings_growth": _g("earningsGrowth"),
        "quarterly_revenue_growth": _g("revenueQuarterlyGrowth"),
        "quarterly_earnings_growth": _g("earningsQuarterlyGrowth"),

        # Profitability
        "gross_margins": _g("grossMargins"),
        "operating_margins": _g("operatingMargins"),
        "profit_margins": _g("profitMargins"),
        "return_on_equity": _g("returnOnEquity"),
        "return_on_assets": _g("returnOnAssets"),

        # Dividends
        "dividend_yield": _g("dividendYield"),
        "dividend_rate": _g("dividendRate"),
        "payout_ratio": _g("payoutRatio"),
        "ex_dividend_date": _g("exDividendDate"),

        # Risk & Volatility
        "beta": _g("beta"),
        "52_week_high": _g("fiftyTwoWeekHigh"),
        "52_week_low": _g("fiftyTwoWeekLow"),
        "50_day_ma": _g("fiftyDayAverage"),
        "200_day_ma": _g("twoHundredDayAverage"),

        # Short Interest
        "short_ratio": _g("shortRatio"),
        "short_pct_of_float": _g("shortPercentOfFloat"),
        "shares_short": _g("sharesShort"),

        # Institutional
        "institutional_ownership": _g("heldPercentInstitutions"),
        "insider_ownership": _g("heldPercentInsiders"),
        "float_shares": _g("floatShares"),
        "shares_outstanding": _g("sharesOutstanding"),

        # Analyst
        "target_high": _g("targetHighPrice"),
        "target_low": _g("targetLowPrice"),
        "target_mean": _g("targetMeanPrice"),
        "target_median": _g("targetMedianPrice"),
        "recommendation": _g("recommendationKey"),
        "number_of_analysts": _g("numberOfAnalystOpinions"),
    }

    # Clean out None values for a tighter response
    return {k: v for k, v in result.items() if v is not None}
